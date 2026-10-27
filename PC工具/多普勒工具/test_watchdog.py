#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真机判死回归：接管但**不开推送页**时，电台会不会自己交还。

为什么值得单独一个脚本：`verify_radio.py` 测的是参考模型，模型这一条一直是对的；
固件里 `DOPPLER_ExtActive()` 当时还带着 `&& ext_page_on`，于是
`app.c:1917` 的 Tick 闸门三条件全假 —— **5 s 帧静默和 6 s 串口判死那段代码一次
都不执行**，拔线后 ext_ctrl 永真、芯片停在手机最后一帧，只能断电。2026-10-27 的
`8bca2f4` 修掉了它。这条只能上真机验。

测法（故意不发 0x076E，也不推 0x076C、不写 0x0602）：
  1. 0x076A 问基线状态；
  2. 0x0768=1 接管；
  3. 每 300 ms 轮询 0x076A —— 自检**不刷心跳**（刷心跳只有 0x076C 和 0x0602），
     而持续的串口流量又会让"串口 6 s 没动静"那条 `gone` 判据不成立，
     于是唯一能生效的就剩下 `silent`（500 tick = 5 s）这一条。
  4. 断言 0x01 位在 4.5~7 s 之间消失；收尾发 0x0768=0，别把电台留在接管态。

跑法：
    python test_watchdog.py                 # 用 COM7
    python test_watchdog.py --port COM9
    python test_watchdog.py --with-page     # 对照：开推送页那条老路径
退出码：0 过，1 不过（老固件就是不过：位永远不清）。
"""
from __future__ import annotations

import argparse
import sys
import time

import serial

sys.path.insert(0, __file__.rsplit("/", 1)[0].replace("\\", "/"))
from fakeradio import parse_replies, request_bytes  # noqa: E402  同一套帧规则


class Radio:
    def __init__(self, port: str):
        self.s = serial.Serial(port, 38400, timeout=0.05)
        self.buf = bytearray()
        time.sleep(0.2)

    def drain(self) -> None:
        self.buf = bytearray()
        self.s.timeout = 0.05
        while self.s.read(256):
            pass

    def ask(self, cmd: int, pl: bytes = b"", reply: int | None = None,
            wait: float = 0.8):
        self.drain()
        self.s.write(request_bytes(cmd, pl))
        if reply is None:
            return None
        end = time.time() + wait
        while time.time() < end:
            chunk = self.s.read(64)
            if chunk:
                self.buf += chunk
                frames, rest = parse_replies(self.buf)
                self.buf = bytearray(rest)
                for f in frames:
                    if f is not None and (reply is None or f.cmd == reply):
                        return f
        return None


def bits(state: int) -> str:
    names = {0x01: "接管", 0x02: "过境中", 0x04: "有表", 0x08: "武装",
             0x10: "推送页", 0x20: "PTT", 0x40: "发射中", 0x80: "刷写中"}
    on = [v for k, v in names.items() if state & k]
    return "+".join(on) if on else "全清"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM7")
    ap.add_argument("--with-page", action="store_true", help="先发 0x076E=1（对照老路径）")
    ap.add_argument("--limit", type=float, default=9.0, help="最多等多久，秒")
    a = ap.parse_args()

    r = Radio(a.port)
    try:
        base = r.ask(0x076A, reply=0x076B)
        if base is None:
            print("× 电台没答 0x076A：先确认线、开机、以及有没有人占着串口")
            return 1
        proto, state, phase = base.payload[0], base.payload[1], base.payload[2]
        print(f"基线：proto {proto} · state {state:#04x}（{bits(state)}）· phase {phase}")
        if state & 0x01:
            print("× 电台已经在接管态，先手动交还（0x0768=0）再跑本测试")
            return 1

        if a.with_page:
            r.ask(0x076E, b"\x01", reply=0x076F)
        t0 = time.time()
        r.ask(0x0768, b"\x01")                     # 接管：App 侧就是这么发的，不读回包
        got = r.ask(0x076A, reply=0x076B)
        if got is None or not (got.payload[1] & 0x01):
            print(f"× 接管后没拿到 0x01 位：{got.payload.hex() if got else '没回包'}")
            return 1
        print(f"接管生效（{bits(got.payload[1])}），开始只轮询不推帧……")

        released = None
        while time.time() - t0 < a.limit + 5.0:
            f = r.ask(0x076A, reply=0x076B)
            if f is None:
                continue
            if not (f.payload[1] & 0x01):
                released = time.time() - t0
                print(f"→ {released:.2f} s 时 0x01 位清了，phase 回到 {f.payload[2]}")
                break
        if released is None:
            print(f"× 等了 {a.limit + 5:.0f} s 还是接管态 —— 判死代码没跑（这就是 8bca2f4 "
                  f"修的那个洞：接管不切页时 Tick 根本不执行）")
            return 1
        if not (4.5 <= released <= 7.5):
            print(f"× 交还时刻 {released:.2f} s 不在 5 s 看门狗的合理窗口（4.5~7.5 s）内")
            return 1
        print(f"✓ 接管不切页时也会判死：{released:.2f} s 自动交还（doppler.c:1657 的 500 tick）")

        r.ask(0x076E, b"\x00", reply=0x076F)       # 收尾：不留残留
        r.ask(0x0768, b"\x00")
        f = r.ask(0x076A, reply=0x076B)
        print(f"收尾后：{bits(f.payload[1]) if f else '没回包'}")
        return 0
    finally:
        r.s.close()


if __name__ == "__main__":
    sys.exit(main())
