#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CRC 约定三端一致检查（静态 + 行为，都不要设备）。

盯的是这一件事：**回包到底有没有真校验**。

历史：固件对请求真算 CRC（`UART_IsCommandAvailable` 里 `CRC_Calculate == Crc`
会丢错帧），对回包却恒填 `0xFFFF`；App 为了兼容这个"发送端不算"，写成
`crcRx != 0xFFFF` 才校验。两边各自都"对"，合起来的实际效果是**回包这条路
上没有任何一层能发现电台的话被听错了** —— 而 5 Hz 推帧、发射时 USB 与
BK4819 抢总线，掉的恰好是这条方向。

现在 uart.c 真算回包 CRC，这个检查就负责守住两件事：
  1. 别被人改回恒 0xFFFF（那会让 App 的校验静默失效，且屏幕一切正常）；
  2. 三端的 crc16 参数别分叉（init 0 / poly 0x1021 / MSB-first / 无反射）。

跑法：python 多普勒工具/verify_crc.py
退出码：0 一致，1 有漂移或解析不到。

设计原则同 verify_layout.py：解析不到 = 报错，绝不静默通过。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FW_UART = ROOT / "固件源码" / "uvk1-v6.3.0" / "App" / "app" / "uart.c"
FW_CRC_C = ROOT / "固件源码" / "uvk1-v6.3.0" / "App" / "driver" / "crc.c"
KT_LINK = ROOT / "安卓App" / "android-app" / "app" / "src" / "main" / "java" / "com" / "f4hwn" / "doppler" / "RadioLink.kt"

sys.path.insert(0, str(HERE))
import radio_proto as rp        # noqa: E402  三端里的那一端（PC）

BAD: list[str] = []
OK: list[str] = []


def read(p: Path) -> str:
    if not p.exists():
        BAD.append("找不到文件：%s" % p.relative_to(ROOT))
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def ok(msg: str) -> None:
    OK.append(msg)


# ---------------------------------------------------------------- 固件侧
def check_firmware() -> None:
    src = read(FW_UART)
    if not src:
        return

    for fn in ("SendReply_VCP", "SendReply"):
        m = re.search(r"\bstatic void %s\(.*?\n\}" % fn, src, re.S)
        if not m:
            BAD.append("%s：在 uart.c 里找不到这个函数 —— 检查失效了" % fn)
            continue
        body = m.group(0)

        crc_at = body.find("CRC_Calculate(")
        mask_at = body.find("^= Obfuscation[i % 16]")
        if crc_at < 0:
            BAD.append("%s：没有算 CRC —— 回包又是恒 0xFFFF，App 的校验会静默失效" % fn)
            continue
        if mask_at >= 0 and crc_at > mask_at:
            BAD.append("%s：CRC 算在混淆**之后**。那个循环会原地改掉 pReply，"
                       "算出来的是乱码的校验和，App 会拒收每一帧" % fn)
            continue

        foot = body[crc_at:]
        if re.search(r"Obfuscation\[\(Size \+ [01]\) % 16\] \^ 0xFF", foot):
            BAD.append("%s：回包 CRC 又变回常量 0xFF 了" % fn)
            continue
        if "crc & 0xFFu" not in foot and "crc & 0xFF" not in foot:
            BAD.append("%s：CRC 低位没写进尾部两字节 —— 解析不到，不敢当它通过" % fn)
            continue
        ok("%s：算在混淆前、真值写进尾部" % fn)

    # 请求方向一直在校验，这条是回归哨兵：谁把它关了，App 发错帧也没人管
    if "CRC_Calculate(pUART_Command->Buffer, Size) == Crc" not in src:
        BAD.append("请求方向的 CRC 校验语句找不到了（UART_IsCommandAvailable）")
    else:
        ok("请求方向仍真校验")


# ---------------------------------------------------------------- 三端参数
def check_params() -> None:
    c = read(FW_CRC_C)
    kt = read(KT_LINK)

    want = {"init0": False, "poly": False, "norefl": True}
    if "Crc = 0;" in c or re.search(r"Crc\s*=\s*0\s*;", c):
        want["init0"] = True
    if "0x1021" in c:
        want["poly"] = True
    if "reflect" in c.lower() or "0x8408" in c:      # 0x8408 = 反射后的 poly
        want["norefl"] = False

    if not want["init0"]:
        BAD.append("crc.c：初值不是 0（三端约定 init 0x0000）")
    if not want["poly"]:
        BAD.append("crc.c：找不到多项式 0x1021")
    if not want["norefl"]:
        BAD.append("crc.c：出现了反射/0x8408 —— 与 App、PC 两端的 MSB-first 不一致")
    if want["init0"] and want["poly"] and want["norefl"]:
        ok("crc.c：init 0 / poly 0x1021 / MSB-first")

    if "0x1021" not in kt:
        BAD.append("RadioLink.kt：找不到多项式 0x1021")
    else:
        ok("RadioLink.kt：poly 0x1021")

    # App 侧那个 0xFFFF 跳过是**故意**留的兼容口，但它必须还在，
    # 否则接老固件（恒 0xFFFF）会整条链路拒收。
    # 只 grep "0xFFFF" 不算数 —— 文件里别处也有这个字面量，必须比到那句判断本身。
    if not re.search(r"crcRx\s*!=\s*0xFFFF", kt):
        BAD.append("RadioLink.kt：`crcRx != 0xFFFF` 那个兼容口不见了/被改条件了 —— "
                   "接老固件（回包恒 0xFFFF）会一帧都收不下")
    else:
        ok("RadioLink.kt：保留 0xFFFF 兼容口（老固件仍可用）")


# ---------------------------------------------------------------- 行为闭环
def check_roundtrip() -> None:
    """用 Python 复刻固件的新逻辑，喂回真实解析器。"""
    cases = {
        0x0769: bytes([0x04]),                                  # 接管回执，最短
        0x076D: bytes([0x25]),
        0x0775: (670).to_bytes(2, "little") + bytes([0x03]),
        0x0773: b"v6.3.0\x00\x00" + bytes([0, 76, 7, 1]),       # 固件标识
        0x0765: b"\x00\x30\x16\x00" + bytes([0x11]) * 4,        # 表区读回
    }
    for cmd, payload in cases.items():
        inner = rp.msg(cmd, payload)
        replies, _ = rp.parse_replies(bytearray(rp.encode_reply(inner)))
        if not replies or replies[0] is None:
            BAD.append("0x%04X 的好回包被解析器拒了" % cmd)
            continue
        if replies[0].cmd != cmd or replies[0].payload != payload:
            BAD.append("0x%04X 回包解析成 %r" % (cmd, replies[0]))

    # 改坏一 bit 必须被拒。这一条同时证明回包 CRC 是真算的：若固件仍恒填
    # 0xFFFF，解析器会走跳过分支，改坏的帧照样通过。
    bad = bytearray(rp.encode_reply(rp.msg(0x0769, bytes([0x04]))))
    bad[5] ^= 0x01
    replies, _ = rp.parse_replies(bad)
    if replies and replies[0] is not None:
        BAD.append("注入 1 bit 错误的回包竟然被收下 —— 回包没有真校验")
    else:
        ok("改坏 1 bit 的回包被丢弃（CRC 真在起作用）")

    # 老固件那种恒 0xFFFF 的回包必须照样收下，兼容口不能关
    legacy = bytearray(rp.encode_reply(rp.msg(0x0769, bytes([0x04])), crc=0xFFFF))
    legacy[5] ^= 0x01
    replies, _ = rp.parse_replies(legacy)
    if not replies or replies[0] is None:
        BAD.append("0xFFFF（老固件）的回包被拒 —— 接老机器会一帧都收不下")
    else:
        ok("0xFFFF（老固件）仍收下，兼容口没被关掉")

    ok("%d 种回包形状解析闭环" % len(cases))


def main() -> int:
    check_firmware()
    check_params()
    check_roundtrip()

    for m in OK:
        print("  一致  %s" % m)
    for m in BAD:
        print("  漂移  %s" % m)
    print("\n%d 项一致 / %d 项漂移" % (len(OK), len(BAD)))
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
