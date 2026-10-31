#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自检：doppler_tool.Link 的 ask/erase/write/read/set_clock 与固件对得上。

为什么需要这个文件：2026-10-28 的审计发现 `Link.ask(..., expect=)` 把
"载荷长度"当成了"回包 body 长度"，三处各短 4 字节 —— 擦除必抛 IndexError、
写完的状态字节永远取不到、回读每轮少 4 字节直到死循环。这一整类错误在真机上
的表现只是"卡住"，看不出错在哪，所以这里用一台**按 uart.c 格式回包**的假电台
把四个命令逐个跑通，把字节级契约钉死。

不碰硬件、不需要串口：假的 serial 只实现 write/read/reset_input_buffer。
真机路径仍是 多普勒工具/verify_all.sh 的 hw_* 脚本。

用法：python verify_link_ask.py
"""
from __future__ import annotations

import struct
import sys

import radio_proto as rp

OK = "\033[32mok\033[0m"
BAD = "\033[31mFAIL\033[0m"

fails: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  {OK}  {name}")
    else:
        fails.append(name)
        print(f"  {BAD}  {name} {detail}")


class FakeRadio:
    """一台只认四个命令的假电台：擦/写/读/对时，格式照 App/app/uart.c。"""

    SECTOR = 4096

    # 外部 flash 是 2 MB（py25q16），过境表在 0x162000 起：默认就得覆盖到，
    # 否则 0x0764 回读越界、假电台只回 7 字节，测试会伪装成"协议不符"。
    def __init__(self, size=0x200000):
        self.flash = bytearray(b"\xFF" * size)
        self.rx = bytearray()          # 待被客户端读走的字节
        self.tx = bytearray()          # 客户端刚写进来的帧
        self.clock = None
        self.clock_shift = 0           # 回显偏多少秒（"收下了但时钟没跟着走"）
        self.corrupt_next = False      # 让下一条回包的 CRC 故意坏掉
        self.short_next = False        # 让下一条回包少给 4 字节（老 bug 的形态）
        self.short_every = False       # 每一条回包都少 4 字节（持续坏）
        self.proto = 7                 # 0x076A 回包：7 = 带 0x0772/亚音
        self.state = 0
        self.phase = 2
        self.ppm = 3                   # 0x0772 回包里的实测 ppm（有符号）
        self.flash_drop = set()        # 这些偏移"写不进去"（复现 WIP 竞争）

    # ── serial 接口 ────────────────────────────────────────────────
    def write(self, data):
        self.tx += data
        self._pump()

    def read(self, n):
        out = bytes(self.rx[:n])
        del self.rx[:n]
        return out

    def reset_input_buffer(self):
        self.rx.clear()

    def close(self):
        pass

    # ── 收帧 ───────────────────────────────────────────────────────
    def _pump(self):
        while True:
            i = self.tx.find(b"\xAB\xCD")
            if i < 0:
                self.tx.clear()
                return
            if i:
                del self.tx[:i]
            if len(self.tx) < 4:
                return
            size = self.tx[2] | (self.tx[3] << 8)
            total = 4 + size + 2 + 2
            if len(self.tx) < total:
                return
            body = rp.mask_xor(bytes(self.tx[4:4 + size + 2]))
            del self.tx[:total]
            self._serve(body[:size])

    def _serve(self, msg):
        cmd = msg[0] | (msg[1] << 8)
        paylen = msg[2] | (msg[3] << 8)
        p = msg[4:4 + paylen]
        if cmd == 0x0760:
            addr = struct.unpack("<I", p[0:4])[0]
            self.flash[addr:addr + self.SECTOR] = b"\xFF" * self.SECTOR
            self._reply(0x0761, p[0:4] + bytes([0]))
        elif cmd == 0x0762:
            addr = struct.unpack("<I", p[0:4])[0]
            n = struct.unpack("<H", p[4:6])[0]
            data = p[6:6 + n]
            for i in range(n):
                if addr + i in self.flash_drop:
                    data = data[:i] + bytes([255]) + data[i + 1:]
            self.flash[addr:addr + n] = data
            self._reply(0x0763, p[0:4] + struct.pack("<H", n) + bytes([0]))
        elif cmd == 0x0764:
            addr = struct.unpack("<I", p[0:4])[0]
            n = struct.unpack("<H", p[4:6])[0]
            data = bytes(self.flash[addr:addr + n])
            if self.short_next or self.short_every:  # 复现"少 4 字节"的老形态
                self.short_next = False
                data = data[:max(0, n - 4)]
            self._reply(0x0765, p[0:4] + struct.pack("<H", n) + bytes([0]) + data)
        elif cmd == 0x076A:
            # State/Proto/Phase，回包恒 7 字节（ID 2 + 长度 2 + 3）
            self._reply(0x076B, bytes([self.proto, self.state, self.phase]))
        elif cmd == 0x0772:
            if self.proto < 7:      # 老固件不认这条，不回包
                return
            tag = b"v6.3.0".ljust(8, bytes([0])) + bytes([self.ppm & 0xFF,
                                                      76, self.proto, self.phase])
            self._reply(0x0773, tag)
        elif cmd == 0x0766:
            days, secs = struct.unpack("<II", p[0:8])
            self.clock = (days, secs)
            self._reply(0x0767, struct.pack("<I", secs + self.clock_shift) + bytes([0]))
        else:
            # 不认识的命令：固件直接 break，不回包（客户端该超时）。
            pass

    def _reply(self, rid, payload):
        body = struct.pack("<HH", rid, len(payload)) + payload
        frame = rp.encode_reply(body)
        if self.corrupt_next:
            self.corrupt_next = False
            # 只动 CRC 那两个字节：头、脚、长度、载荷都对，只有校验过不去。
            b = bytearray(frame)
            b[4 + len(body)] ^= 0xFF
            frame = bytes(b)
        self.rx += frame



def core_start():
    """过境表在外部 flash 里的起始地址（固件 doppler.h 的 TABLE_START）。"""
    import doppler_core as core
    return core.TABLE_START


def _link_at(fake):
    """同一台假电台、同一套缓冲，再造一个 Link 视图（不重开串口）。"""
    import doppler_tool as dt
    lk = dt.Link.__new__(dt.Link)
    lk.ser = fake
    lk.buf = bytearray()
    return lk


def main():
    import doppler_tool as dt

    print("== Link.ask 字节级契约 ==")
    fake = FakeRadio()
    link = dt.Link.__new__(dt.Link)      # 不走 __init__（它要真串口）
    link.ser = fake
    link.buf = bytearray()

    # 1 擦除：body 9，Status 在 body[8]
    # 原来这里写的是 check("erase 不再抛 IndexError", True) —— 恒真，永远绿。
    # "崩掉整条脚本"不等于"这条检查存在"：它抓的是回包比 9 字节短时
    # r[8] 越界，只有把异常接住并如实判红，才能在那种回包真的出现时变红。
    try:
        link.erase(0x2000)
        erase_ok, erase_why = True, ""
    except Exception as exc:                       # noqa: BLE001 - 就是要接住
        erase_ok, erase_why = False, repr(exc)
    check("erase 不再抛 IndexError", erase_ok, erase_why)
    check("擦除后整扇区为 0xFF", bytes(fake.flash[0x2000:0x2000 + 4096]) == b"\xFF" * 4096)

    # 2 写入 + 每块回读（Link.write 自带回读比对）
    data = bytes(range(256)) * 2 + b"\x01\x02\x03"      # 515 字节：跨页、末块奇数
    link.write(0x2000, data)
    check("write 515 字节全部落盘", bytes(fake.flash[0x2000:0x2000 + len(data)]) == data)

    # 3 回读
    back = link.read(0x2000, len(data))
    check("read 长度一致", len(back) == len(data), f"得到 {len(back)}")
    check("read 内容一致", back == data)

    # 4 跨块回读（read 内部按 128 字节一帧）
    long = link.read(0x2000, 400)
    check("read 400 字节（4 帧）不挂死", long == data[:400])

    # 5 对时：body 9，回显 Seconds
    secs = 73512
    got = link.set_clock()
    check("set_clock 发的是 8 字节载荷", fake.clock is not None)
    check("set_clock 回显能读到", isinstance(got, int) and got == fake.clock[1])

    # 回显偏了必须报错：以前只验 Status，这种"对时假成功"会一路带到写表，
    # 电台照着自己的旧钟倒计时，屏幕上一切正常、过境时间全错。
    fake.clock_shift = -7
    try:
        link.set_clock()   # 用当前时间；FakeRadio 会把回显改掉
        check("set_clock 回显偏差必须报错", False, "没报错")
    except RuntimeError as e:
        check("set_clock 回显偏差必须报错", "回显不符" in str(e), str(e))
    fake.clock_shift = 0

    print("== 坏帧必须被拒（以前会被当成好数据） ==")
    fake.corrupt_next = True
    try:
        link.erase(0x3000)
        check("CRC 坏掉要报错", False, "竟然通过了")
    except RuntimeError as e:
        check("CRC 坏掉要报错", "CRC" in str(e), str(e))

    # 单次短包：read() 内部有 3 次重试，应该自动补上，用户看不到。
    fake.short_next = True
    back2 = link.read(0x2000, 128)
    check("单次短包被重试吃掉", back2 == data[:128])

    # 持续短包：重试耗尽后必须抛错，不能像以前那样转成死循环。
    fake.short_every = True
    try:
        link.read(0x2000, 128)
        check("持续短包要抛错（而不是死循环）", False, "竟然通过了")
    except TimeoutError as e:
        # 类型仍是 TimeoutError（上层统一处理），但文案必须说出真实原因，
        # 不能把"回了但字节不对"说成"没回包"。
        ok = ("都没回包" not in str(e)) and ("长度" in str(e) or "paylen" in str(e))
        check("持续短包要抛错（而不是死循环）", ok, str(e))
    except RuntimeError as e:
        check("持续短包要抛错（而不是死循环）", True, str(e))
    finally:
        fake.short_every = False

    print("== 握手：先证明对面是带表固件 ==")
    proto, state, phase = link.hello()
    check("hello 解析 proto/state/phase", (proto, state, phase) == (7, 0, 2),
          f"{proto},{state},{phase}")
    tag = link.fw_tag()
    check("fw_tag 解析固件标识", tag and tag["ver"] == "v6.3.0" and tag["ppm"] == 3,
          str(tag))
    fake.proto = 5
    check("老固件（proto<7）读不到 fw_tag 返回 None", link.fw_tag() is None)
    fake.proto = 7

    print("== 整表回读：写漏一个字节必须被抓住 ==")
    # 换一台干净的假电台：上一段故意造过坏帧/短包，缓冲里有残留。
    fake = FakeRadio()
    link = _link_at(fake)
    tbl = bytes(range(200)) * 3
    link.erase(core_start())
    link.write(core_start(), tbl)
    check("整表一致时返回 None",
          dt.Tool._verify_table(object(), _link_at(fake), tbl, lambda t: None) is None)

    # 复现真机事故：每一页写完回读都是对的，事后整段变成 0xFF（WIP 竞争）。
    # 所以这里不改写入路径，而是在写完**之后**把那个字节改成 0xFF —— 逐页
    # 回读（Link.write 内部）看不出问题，只有整表回读能抓住。
    fake.flash[core_start() + 137] = 0xFF
    bad = dt.Tool._verify_table(object(), _link_at(fake), tbl, lambda t: None)
    check("丢字的偏移被定位", bad is not None and bad[0] == 137, str(bad))
    check("报出的是设备里真实的字节", bad is not None and bad[1] == 0xFF, str(bad))

    # 重写一遍（擦除幂等）后能恢复 —— 这就是 _writer 里的重试语义。
    link.erase(core_start())
    link.write(core_start(), tbl)
    check("整表重写一次后一致",
          dt.Tool._verify_table(object(), _link_at(fake), tbl, lambda t: None) is None)

    print("== expect 语义固定为 body 长度 ==")
    # 删掉一条永绿断言：原来这里是
    #   check("erase 的 expect 是 9", "9)" in open(__file__).read() or True)
    # 两个问题叠在一起 —— `or True` 让它恒真，而它 grep 的是**本文件自己**，
    # 就算 doppler_tool.py 里的 expect 改了也照绿。下面这条对
    # dt.Link.erase 真实源码的断言才是有用的那一条。
    import inspect
    src = inspect.getsource(dt.Link.erase)
    check("erase 源码里 expect=9", "9)" in src, src.strip().splitlines()[1])

    print()
    if fails:
        print(f"\033[31m{len(fails)} 项失败\033[0m: " + ", ".join(fails))
        return 1
    print("\033[32m全部通过\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
