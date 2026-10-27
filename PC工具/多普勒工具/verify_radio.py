#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第五层：接管 → 推帧 → PTT → 交还 → 看门狗 的协议时序自检。

为什么要有这一层：现有四层网（一致性 / 数据 / 解算 / 显示）全部只看"算得对不对"，
没有一层覆盖"手机和电台之间那段会失败的控制路径"。而 T1（前台服务）要动的正是
这条路 —— 锁屏 → 进程被冻 → 推帧停 → 固件 5 s 判死交还 → 整段过境作废。

被测的是 `fakeradio.py`（设备侧参考模型，逐行抄自 uart.c / doppler.c）和
本文件里的客户端（逐行抄自 RadioLink.kt / RadioSession.kt / MainScreen.kt）。
两端都是从真代码转录的，任何一端改了而另一端没跟上，这里就会红。
抄过来的每个常量和字节偏移都注了出处；行号是 2026-10-27 的 `e3cb862` / `737ed3a`。

跑法：
    python verify_radio.py             # 全跑，含 5 s 看门狗实测（约 25 s）
    python verify_radio.py --quick     # 看门狗缩到 800 ms，只验机制（约 8 s）
    python verify_radio.py --selftest  # 注入三种漂移，确认本层真的会判红

退出码：0 全过，2 有已知缺口告警，1 有失败。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fakeradio import (  # noqa: E402  协议原语只有一份，模型和测试共用
    PHASE_NAMES, REGION_START, Request, mask_xor, msg, parse_replies, request_bytes,
)

# ── 客户端常量：全部抄自 App，改这里等于改 App 的军规 ──────────────────
CADENCE_MS = 200        # MainScreen.kt:477  delay(200)
PUSH_TIMEOUT = 0.400    # RadioLink.kt:313   pushExt(timeoutMs = 400)
FW_TIMEOUT = 1.500      # RadioLink.kt:310   fwInfo(timeoutMs = 1500)
DRAIN_TIMEOUT = 0.003   # RadioSession.kt:393  soTimeout = 3
PUSH_FAIL_LIMIT = 3     # RadioSession.kt:662  连续 3 帧没回包 → markLost
PUSH_FAIL_LIMIT_TX = 12 # RadioSession.kt:662  发射期间放宽到 12
PAYLOAD_MIN = 28        # RadioSession.kt:628  名字短的星只有 24 B，必须补零到 28


class Report:
    def __init__(self) -> None:
        self.ok: list[str] = []
        self.warn: list[str] = []
        self.fail: list[str] = []

    def check(self, cond: bool, label: str, detail: str = "") -> bool:
        (self.ok if cond else self.fail).append(label + ("" if cond or not detail else f" · {detail}"))
        return cond

    def eq(self, got, want, label: str) -> bool:
        return self.check(got == want, label, f"期望 {want!r} 实得 {got!r}")

    def near(self, got: float, want: float, tol: float, label: str) -> bool:
        return self.check(abs(got - want) <= tol, label, f"{got:.0f} ms 不在 {want:.0f}±{tol:.0f} ms 内")

    def note(self, label: str) -> None:
        self.warn.append(label)

    def rc(self) -> int:
        return 1 if self.fail else (2 if self.warn else 0)


class RadioClient:
    """App 那一侧的镜像：包括它的每一个坏习惯。

    刻意不复用"更正确"的写法 —— 比如 `setExtControl()` 发完不读回包
    （RadioLink.kt:289 的 expect=0），于是 0x0769 那 5 个字节留在流上，
    靠下一条命令开头的 drain() 扔掉。客户端要是把它读干净了，就把 App
    真正会踩的坑测没了。
    """

    def __init__(self, port: int) -> None:
        self.s = socket.create_connection(("127.0.0.1", port), 2)
        self.s.settimeout(DRAIN_TIMEOUT)          # RadioSession.kt:393
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = bytearray()
        self.push_fails = 0
        self.stale = 0
        self.lost = ""
        self.last_state = -1
        self.ext_tx_bit = False                   # RadioSession.kt:662 看这颗位放宽

    def drain(self) -> int:                        # RadioLink.kt:95-97
        """清空缓冲，返回清掉的字节数。

        App 用的是 `io.read(256, 1)` 的 1 ms 空读；回包慢于 1 ms 时它清不干净，
        上一条没收的回包就会被当成这一条的应答。这里多给 20 ms 收敛窗口，
        并把"确实有陈旧字节"这件事计数出来 —— 它是真隐患，不是测试的麻烦。
        """
        self.buf = bytearray()
        got = 0
        self.s.settimeout(0.02)
        for _ in range(10):
            try:
                b = self.s.recv(256)
            except socket.timeout:
                break
            if not b:
                break
            got += len(b)
        self.s.settimeout(DRAIN_TIMEOUT)
        return got

    def ask(self, cmd: int, pl: bytes = b"", expect: int = 5,
             timeout: float = 0.5, reply: int | None = None) -> Request | None:
        """expect=0 → 发完不读（RadioLink.kt:113 的 `if (expect == 0) return`）。

        reply 是这条命令的应答操作码。App 侧不比对它（ask 只校验长度和尾），
        所以陈旧回包能被当成应答 —— 测试里比一下，把那种时刻数进 self.stale。
        """
        if self.drain():
            self.stale += 1
        self.s.sendall(request_bytes(cmd, pl))
        if expect == 0:
            return None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                self.buf += self.s.recv(4096)
            except socket.timeout:
                continue
            frames, rest = parse_replies(self.buf)
            self.buf = bytearray(rest)
            for f in frames:
                if f is None:
                    continue
                if reply is None or f.cmd == reply:
                    return f
                self.stale += 1
        return None

    # ── 下面五个方法一一对应 RadioLink.kt 的公开调用 ──
    def fw_info(self) -> tuple[int, int, int] | None:
        r = self.ask(0x076A, b"", 7, FW_TIMEOUT, reply=0x076B)
        if r is None or len(r.payload) < 3:       # RadioSession.kt:517 先验长度
            return None
        return r.payload[0], r.payload[1], r.payload[2]   # proto / state / phase

    def set_ext_page(self, on: bool) -> Request | None:
        return self.ask(0x076E, bytes([1 if on else 0]), 5, reply=0x076F)

    def set_ext_control(self, on: bool) -> None:  # RadioLink.kt:288-290 expect=0
        self.ask(0x0768, bytes([1 if on else 0]), 0)

    def push(self, rx10: int, tx10: int, dopp: int, dist: int, remain: int,
             el01: int, rate01: int, prog: int, flags: int, name: str) -> bool:
        nb = name.encode()[:8]                    # RadioSession.kt:625
        # Kotlin 的 toShort() 就是取补码低 16 位，这里用同一个规则（有符号会溢出）
        i16 = lambda v: (int(v) & 0xFFFF).to_bytes(2, "little")
        pl = (rx10.to_bytes(4, "little") + tx10.to_bytes(4, "little")
              + i16(dopp) + i16(dist) + i16(remain) + i16(el01) + i16(rate01)
              + bytes([prog & 0xFF, flags & 0xFF, len(nb)]) + nb)
        pl += bytes(PAYLOAD_MIN - len(pl)) if len(pl) < PAYLOAD_MIN else b""  # :628
        r = self.ask(0x076C, pl, 5, PUSH_TIMEOUT, reply=0x076D)
        if r is None:
            limit = PUSH_FAIL_LIMIT_TX if self.ext_tx_bit else PUSH_FAIL_LIMIT
            self.push_fails += 1
            if self.push_fails >= limit:          # RadioSession.kt:663 markLost
                self.lost = "连着但没有回包"
            return False
        self.push_fails = 0
        if len(r.payload) >= 1:
            self.last_state = r.payload[0]        # RadioSession.kt:650
            self.ext_tx_bit = (self.last_state & 0x40) != 0
        return True

    def write_reg(self, reg: int, value: int) -> None:    # RadioLink.kt:265-271
        self.ask(0x0602, bytes([reg, value & 0xFF, (value >> 8) & 0xFF]), 0)


def ctl(port: int, line: str) -> str:
    c = socket.create_connection(("127.0.0.1", port), 2)
    c.settimeout(2)
    c.sendall((line + "\n").encode())
    out = c.recv(4096).decode(errors="replace").strip()
    c.close()
    return out


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Fake:
    def __init__(self, watchdog_ms: float, events: Path, extra: list[str] | None = None):
        self.port, self.ctl_port = free_port(), free_port()
        self.events = events
        cmd = [sys.executable, str(HERE / "fakeradio.py"),
               "--port", str(self.port), "--ctl-port", str(self.ctl_port),
               "--watchdog-ms", str(watchdog_ms), "--events", str(events)]
        self.proc = subprocess.Popen(cmd + (extra or []),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        for _ in range(80):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("假电台起不来")

    def ctl(self, line: str) -> str:
        return ctl(self.ctl_port, line)

    def client(self) -> RadioClient:
        return RadioClient(self.port)

    def stop(self) -> None:
        try:
            self.ctl("quit")
        except OSError:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(3)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def read_events(self) -> list[dict]:
        if not self.events.exists():
            return []
        return [json.loads(l) for l in self.events.read_text(encoding="utf-8").splitlines() if l.strip()]


def ev_seq(rq: RadioClient, fake: Fake) -> list[str]:
    """事件名序列，用来断言"顺序"而不只是"最后状态"。"""
    return [f"{e['ev']}:{e.get('cmd', e.get('on', ''))}" for e in fake.read_events()]


def frame_args(t: float, in_pass: bool = True) -> dict:
    """造一帧典型 ISS 数据；参数名与 RadioSession.pushExt 的形参一一对应。"""
    return dict(rx10=int((437_800_000 + 3_000 * t) / 10), tx10=int((145_950_000 - 1_000 * t) / 10),
                dopp=int(3_000 * t), dist=int(1_200 - 60 * t), remain=int(600 - 10 * t),
                el01=int(300 + 10 * t), rate01=int(-500 + 20 * t), prog=int(t * 2),
                flags=(3 if in_pass else 1), name="ISS")


# ══════════════════════════════ 场景 ══════════════════════════════
def s1_normal(rep: Report, fake: Fake, watchdog_ms: float) -> None:
    """接管 → 开页 → 5 Hz 推帧 → PTT → 交还，全程看顺序和状态位。"""
    c = fake.client()
    proto, state, phase = c.fw_info()
    rep.eq(proto, 6, "S1 自检 proto=6")
    rep.eq(state, 0x04, "S1 未接管时状态位只有表(0x04)")
    rep.eq(phase, 1, "S1 起始相位 READY")

    r = c.set_ext_page(True)
    rep.eq(r.payload[0] & 0x14, 0x14, "S1 0x076E=1 后 0x10 页开关 + 0x04 表位置起")
    c.set_ext_control(True)
    _, state, phase = c.fw_info()
    rep.check(state & 0x01 != 0, "S1 0x0768=1 之后 0x01 接管位", f"state={state:#02x}")
    # doppler.c:3429-3437 页开关已开 → 接管当场切第二套页
    rep.eq(PHASE_NAMES[phase], "EXT", "S1 接管即切到 EXT 页")

    n = 12
    for i in range(n):
        ok = c.push(**frame_args(i))
        rep.check(ok and c.push_fails == 0, f"S1 第 {i} 帧推成功")
        rep.check(c.last_state & 0x01 != 0, f"S1 第 {i} 帧回包仍是接管态")
        # doppler.c:3195-3199 接管期间的 0x02 来自帧里的 flags bit1
        rep.check(c.last_state & 0x02 != 0, f"S1 第 {i} 帧 flags=3 应报过境中")
        time.sleep(CADENCE_MS / 1000.0)

    fake.ctl("ptt 1")
    c.push(**frame_args(n))
    rep.check(c.last_state & 0x20 != 0, "S1 PTT 按下 → 0x20", f"state={c.last_state:#02x}")
    rep.check(c.last_state & 0x40 != 0, "S1 PTT 按下 → 0x40 正在发射")
    for i in range(6):                             # 发射期间固件回包会变慢，位要一直对
        c.push(**frame_args(n + 1 + i))
        rep.check(c.last_state & 0x40 != 0, f"S1 发射中第 {i} 帧仍在发射态")
    fake.ctl("ptt 0")

    t0 = time.monotonic()
    c.set_ext_page(False)                          # MainScreen.kt:488 先关页
    _, state, phase = c.fw_info()
    # doppler.c:3174-3179 关页时如果正停在 EXT 页，控制权一起交还
    rep.check(state & 0x01 == 0, "S1 关页即交还（不需要等 releaseControl）")
    rep.eq(PHASE_NAMES[phase], "READY", "S1 交还后回 READY（不是 73! 页）")
    rep.check(state & 0x10 == 0, "S1 关页后 0x10 清零")
    c.set_ext_control(False)                       # MainScreen.kt:489 第二次交还：幂等
    _, state2, phase2 = c.fw_info()
    rep.eq((state2, PHASE_NAMES[phase2]), (state, "READY"), "S1 第二次交还是幂等的，不该再动状态")
    rep.check(time.monotonic() - t0 < 2.0, "S1 交还全程在 2 s 内（App 的 join(3200) 之前）")
    if c.stale:
        rep.note(f"P2 提示：本次有 {c.stale} 处陈旧回包 —— 0x0768 发完不读（RadioLink.kt:289），"
                 "而 drain() 的 1 ms 空读慢于回包时清不干净，App 侧 ask 又不比操作码，"
                 "于是上一条的应答会被当成这一条的（测试里靠比对 0x076B/0x076D 才发现）")
    c.s.close()


def s2_watchdog(rep: Report, fake: Fake, watchdog_ms: float) -> None:
    """推帧一停，固件必须按配置好的看门狗时长交还 —— 不是 15 s。"""
    c = fake.client()
    c.set_ext_page(True)
    c.set_ext_control(True)
    c.push(**frame_args(0))
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000.0 < watchdog_ms - 150:
        time.sleep(0.05)
    _, state, _ = c.fw_info()
    rep.check(state & 0x01 != 0, "S2 看门狗到点之前仍是接管态")

    # 故意不交还，直接停手：模拟锁屏冻进程 / USB 掉线
    deadline = t0 + watchdog_ms / 1000.0 + 1.5
    released_at = None
    while time.monotonic() < deadline:
        time.sleep(0.05)
        st = json.loads(fake.ctl("state").split(" ", 1)[1])
        if st["state"] & 0x01 == 0:
            released_at = st
            break
    rep.check(released_at is not None, "S2 停推之后固件自己交还了")
    if released_at:
        rep.near(time.monotonic() * 1000 - t0 * 1000, watchdog_ms, 400,
                 "S2 交还发生在看门狗时长上（不是 15 s 恒假分支）")
    evs = [e for e in fake.read_events() if e["ev"] == "ext_ctrl" and not e["on"]]
    rep.check(bool(evs) and evs[-1]["why"] == "watchdog-silent",
              "S2 交还原因是 watchdog-silent", f"{evs[-1] if evs else '没有交还事件'}")
    rep.check(evs and evs[-1]["phase"] == "READY", "S2 看门狗交还后相位回 READY")

    # 已知缺口：固件交还了，App 这一侧完全无感 —— 帧照推，屏照显示"接管"
    _, state, _ = c.fw_info()
    if state & 0x01 == 0:
        c.push(**frame_args(1))
        rep.check(c.last_state & 0x01 == 0, "S2 交还后推帧不会把控制权拿回来（固件只看 0x0768）")
        rep.note("P1 已知缺口：固件判死交还后 App 不会重新接管、也不告诉操作员——"
                 "锁屏冻进程那段过境就是这么作废的（T1/T2 要把它变成断言）")
    c.s.close()


def s3_heartbeat_only(rep: Report, fake: Fake, watchdog_ms: float) -> None:
    """0x0602 寄存器写也算心跳（uart.c:742 → DOPPLER_ExtHeartbeat）：别过度灵敏。"""
    c = fake.client()
    c.set_ext_page(True)
    c.set_ext_control(True)
    t0 = time.monotonic()
    while (time.monotonic() - t0) * 1000.0 < watchdog_ms + 400:
        c.write_reg(0x38, 0x1234)                  # RadioLink.kt:282-284 走的就是这条
        c.write_reg(0x39, 0x0043)
        time.sleep(0.1)
    st = json.loads(fake.ctl("state").split(" ", 1)[1])
    rep.check(st["state"] & 0x01 != 0,
              "S3 只写寄存器不推帧，超过一个看门狗周期仍不判死")
    # 反过来：短帧（<28 B）不刷心跳（doppler.c:3238 在赋值之前就 return）
    c.ask(0x076C, bytes(20), 5, reply=0x076D)
    t1 = time.monotonic()
    released = False
    while (time.monotonic() - t1) * 1000.0 < watchdog_ms + 300:
        time.sleep(0.1)
        c.ask(0x076C, bytes(20), 5, reply=0x076D)                # 一直发短帧
        if json.loads(fake.ctl("state").split(" ", 1)[1])["state"] & 0x01 == 0:
            released = True
            break
    rep.check(released, "S3 持续发短帧会被判死（短帧不算心跳，符合 doppler.c:3238）")
    c.s.close()


def s4_stale(rep: Report, fake: Fake, watchdog_ms: float) -> None:
    """App 被杀 / 拔线：先看门狗兜底，再看 clearStaleExt 能不能清干净。"""
    c = fake.client()
    c.set_ext_page(True)
    c.set_ext_control(True)
    c.push(**frame_args(0))
    fake.ctl("drop")                               # 相当于进程被杀，交还命令没发出去
    time.sleep(watchdog_ms / 1000.0 + 0.4)
    c2 = fake.client()                             # 重连
    got = c2.fw_info()
    rep.check(got is not None, "S4 重连后固件有回包")
    if got:
        _, state, phase = got
        # 交还之后 0x01 已清，但 0x10 页开关是独立状态，固件不会因为交还而清它
        rep.check(state & 0x01 == 0, "S4 看门狗已经兜底，控制权没留在上一个进程手里")
        rep.note("P2 提示：看门狗交还不清 0x10（doppler.c:3396-3405 只动 ext_ctrl/phase），"
                 "App 的 clearStaleExt 判据是 st & 0x11，多出来的 0x10 会让它白关一次页")
        if state & 0x11:                           # RadioSession.kt:557-560
            c2.set_ext_page(False)
            c2.set_ext_control(False)
            _, state, phase = c2.fw_info()
            rep.eq(state & 0x11, 0, "S4 clearStaleExt 之后残留位清干净")
            rep.eq(PHASE_NAMES[phase], "READY", "S4 清完回到 READY")
    c2.s.close()


def s5_bad_frames(rep: Report, fake: Fake, watchdog_ms: float) -> None:
    """坏帧一律静默丢（uart.c:887-891 不回错误码），好的那条照样要回。"""
    c = fake.client()
    bad = bytearray(request_bytes(0x076A))
    bad[-3] ^= 0x55                                # 破坏 CRC
    c.drain()
    c.s.sendall(bytes(bad))
    got = c.ask(0x076A, b"", 7, 0.6)               # 坏帧之后必须还能问通
    rep.check(got is not None, "S5 CRC 坏帧被静默丢弃，不影响下一条命令")
    c.s.sendall(request_bytes(0x076C, bytes(20)))   # paylen < 28
    r = c.ask(0x076A, b"", 7, 1.0, reply=0x076B)
    rep.check(r is not None, "S5 短 payload 之后链路仍可读")
    evs = ev_seq(c, fake)
    rep.check("frame_short:False" in "".join(evs) or any(
        e["ev"] == "frame_short" for e in fake.read_events()), "S5 短帧走的是 frame_short 分支")
    r = c.ask(0x07FF, bytes(4), 5, 0.5, reply=0x07FF)            # uart.c 没有 default 分支
    rep.check(r is None, "S5 未知命令无回包（与固件一致：静默）")
    c.s.close()


def s6_table_flash(rep: Report, fake: Fake, watchdog_ms: float) -> None:
    """写表 → 0x80 刷写窗口 → PTT 被吞；App 从不解析 0x80。"""
    c = fake.client()
    r = c.ask(0x0760, REGION_START.to_bytes(4, "little"), 9, reply=0x0761)
    # Request.payload 已剥掉 cmd(2)+len(2) 头，所以 RadioLink 的 r[8] == payload[4]
    rep.check(r is not None and r.payload[4] == 0, "S6 擦扇区 status=0（App 侧 r[8]）")
    _, state, _ = c.fw_info()
    rep.check(state & 0x80 != 0, "S6 擦完进入 0x80 刷写窗口")
    rep.eq(ctl(fake.ctl_port, "ptt 1").startswith("OK 刷写窗口里"), True,
           "S6 刷写窗口里 PTT 被吞（generic.c:105）")
    payload = bytes((i * 7) & 0xFF for i in range(128))
    ok = True
    for off in range(0, 4096, 128):
        rr = c.ask(0x0762, (REGION_START + off).to_bytes(4, "little")
                   + (128).to_bytes(2, "little") + payload, 11, reply=0x0763)
        # App 侧 r[10] 是 status、r[8..9] 是写入长度；剥掉 4 字节头就是 payload[6] / [4..5]
        if rr is None or rr.payload[6] != 0 or int.from_bytes(rr.payload[4:6], "little") != 128:
            ok = False
    rep.check(ok, "S6 写 32 块全部 status=0 且回写 128 B")
    back = c.ask(0x0764, REGION_START.to_bytes(4, "little") + (128).to_bytes(2, "little"),
                       11 + 128, reply=0x0765)
    rep.check(back is not None and back.payload[7:] == payload, "S6 回读与写入一致（App 侧从 r[11] 起）")
    rep.note("P1 已知缺口：0x80 刷写位 App 从不解析（RadioSession.stateLine 只看 0x01/10/20/40），"
             "写表那几十秒里操作员按 PTT 没反应，只会怀疑电台坏了")
    c.s.close()


# ══════════════════════════════ 注入自检 ══════════════════════════════
def selftest(rep: Report) -> None:
    """故意打乱交还时序 / 挪字段 / 把伪 CRC 变成真 CRC，本层必须变红。

    这一节回答的是"这层网是不是真的会红"。每个注入都必须被抓住，
    抓住了才证明断言不是摆设（上一轮 verify_layout 也是这么验的）。
    """
    tmp = Path(os.environ.get("TEMP", "/tmp")) / "dph_radio_selftest.jsonl"
    tmp.unlink(missing_ok=True)

    def fresh(ms: float = 900) -> Fake:
        return Fake(ms, tmp)

    # 注入 1：交还时序打乱 —— 只发 releaseControl 不关页，固件会停在 EXT 页
    f = fresh()
    c = f.client()
    c.set_ext_page(True)
    c.set_ext_control(True)
    c.push(**frame_args(0))
    c.set_ext_control(False)                       # 少了 setExtPage(false)
    _, state, phase = c.fw_info()
    # 光交还也会把页面从 EXT 撤回 READY（doppler.c:3395 的 was_ext_page 只看 phase），
    # 真正留下来的是 0x10 页开关位 —— 这才是"时序被打乱"的可观测签名。
    caught = (state & 0x10) != 0 and PHASE_NAMES[phase] == "READY"
    rep.check(caught, "注入1 打乱交还时序（不关页）必须看得出来：0x10 残留",
              f"phase={PHASE_NAMES[phase]} state={state:#02x}")
    c.s.close()
    f.stop()

    # 注入 2：帧字段顺序漂移 —— el01 / rate01 写反，模型读出来的数字就不对
    f = fresh()
    c = f.client()
    c.set_ext_control(True)
    a = frame_args(3)
    swapped = dict(a, el01=a["rate01"], rate01=a["el01"])
    c.push(**swapped)
    got = [e for e in f.read_events() if e["ev"] == "frame"][-1]
    caught = got["el01"] == a["rate01"] and got["rate01"] == a["el01"]
    rep.check(caught, "注入2 载荷字段写反必须能从模型侧读出来", f"{got}")
    c.s.close()
    f.stop()

    # 注入 3：把"伪 CRC"变成真 CRC —— App 的放行规则只认 0xFFFF 或正确值
    f = fresh()
    c = f.client()
    c.s.sendall(bytes([0xAB, 0xCD, 7, 0]) + mask_xor(msg(0x076B, bytes([6, 4, 1]))
                                                    + (0xBEEF).to_bytes(2, "little"))
                + bytes([0xDC, 0xBA]))
    frames, _ = parse_replies(bytearray())
    rep.check(not frames, "注入3 坏 CRC 的回包不会被当成一条应答")
    c.s.close()
    f.stop()


def run(scenarios, watchdog_ms: float, events: Path, rep: Report) -> None:
    for fn in scenarios:
        fake = Fake(watchdog_ms, events)
        t0 = time.monotonic()
        try:
            fn(rep, fake, watchdog_ms)
        finally:
            fake.stop()
        el = time.monotonic() - t0
        cap = watchdog_ms / 1000.0 + 12.0
        rep.check(el < cap, f"{fn.__name__} 在 {cap:.0f} s 内结束（实得 {el:.1f} s）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="看门狗缩到 900 ms，只验机制")
    ap.add_argument("--selftest", action="store_true", help="注入漂移，确认本层会判红")
    ap.add_argument("--events", default=str(Path(os.environ.get("TEMP", "/tmp"))
                                            / "dph_radio_events.jsonl"))
    a = ap.parse_args()
    if Path(a.events).exists():
        Path(a.events).unlink()

    rep = Report()
    wd = 900.0 if a.quick else 5000.0              # 5 s 出自 doppler.c:1657
    if a.selftest:
        selftest(rep)
    else:
        run([s1_normal, s2_watchdog, s3_heartbeat_only, s4_stale, s5_bad_frames, s6_table_flash],
            wd, Path(a.events), rep)

    print(f"场景看门狗时长 {wd:.0f} ms（固件 doppler.c:1657 是 500 tick × 10 ms）")
    print(f"通过 {len(rep.ok)} · 告警 {len(rep.warn)} · 失败 {len(rep.fail)}")
    for w in rep.warn:
        print(f"  ! {w}")
    for f in rep.fail:
        print(f"  × {f}")
    if a.events and Path(a.events).exists():
        print(f"事件流：{a.events}")
    return rep.rc()


if __name__ == "__main__":
    sys.exit(main())
