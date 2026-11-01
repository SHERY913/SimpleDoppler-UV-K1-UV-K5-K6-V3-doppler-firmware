#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电台 UART 帧格式：一台设备与所有客户端共用的那一套字节规则。

本来这五样东西（掩码、CRC、加掩、组帧、解回包）躺在 fakeradio.py 里；假电台按用户
要求删掉之后，它们得有个不依附于任何"假设备"的家 —— 真机脚本（test_watchdog.py）
和 PC 工具都要用，而规则本身抄自 uart.c 与 RadioLink.kt：

  帧 = AB CD len(2 LE) | 掩码(msg + crc) | DC BA，msg = cmd(2)+paylen(2)+payload
  回包的 crc 字段固件**根本不计算**，恒是掩码过的 0xFFFF（uart.c:302-303），
  所以客户端只在"不是 0xFFFF"时才比对 CRC（RadioLink.kt:129-136）。
"""
from __future__ import annotations

class Request:
    """一条已解掩的帧：操作码 + 声明的载荷长度 + 载荷本身。"""

    def __init__(self, cmd: int, paylen: int, payload: bytes):
        self.cmd = cmd
        self.paylen = paylen
        self.payload = payload

    def __repr__(self) -> str:
        return f"<{self.cmd:#05x} paylen={self.paylen} {self.payload.hex()}>"

MASK = [0x16, 0x6C, 0x14, 0xE6, 0x2E, 0x91, 0x0D, 0x40,
        0x21, 0x35, 0xD5, 0x40, 0x13, 0x03, 0xE9, 0x80]

# ── 命令 ID ─────────────────────────────────────────────────────────
# 每一条都对应固件 App/app/uart.c 里一个 `case 0x....:`。偶数是请求、
# 奇数是回包（固件里回包 ID 一律是请求 +1）。命名沿用 look4sat_bridge.py
# 和 probe_fw.py 的叫法，别在这里另起一套名字。
CMD_CLOCK         = 0x0766   # -> 0x0767  Seconds + Status
CMD_TAKEOVER      = 0x0768   # -> 0x0769  State：接管/交还频率
CMD_SELFTEST      = 0x076A   # -> 0x076B  Proto/State/Phase，回包**固定 7 字节**
CMD_PUSH_EXT      = 0x076C   # -> 0x076D  State：推一帧（rx/tx/多普勒/…）
CMD_EXT_PAGE      = 0x076E   # -> 0x076F  State：第二套逻辑页开关
CMD_RX10          = 0x0770   # -> 0x0771  uint32 rx10，**单位 10 Hz**（与 0x076C 推帧同单位）
CMD_FW_TAG        = 0x0772   # -> 0x0773  固件标识 + 实测 ppm（见 parse_fw_tag）

# 0x0601 读 BK4819 寄存器：旧固件把回包 ID 写成 0x0601（等于请求 ID），
# 按"请求+1"等回包的主机一律超时（真机上踩过一次）。已改成 0x0602。
CMD_BK_READ       = 0x0601   # -> 0x0602  [reg:u8][value:u16]
CMD_BK_WRITE      = 0x0602   # 无回包；写 REG_3B 会被固件拒掉，但心跳照收

FW_TAG_REPLY = 0x0773
FW_TAG_SIZE = 12        # Ver[8] + Ppm(int8) + PpmMax(u8) + Proto(u8) + Phase(u8)

# 0x076B 为什么永远是 7 字节：App 那头 fwInfo 的 expect 就是 7（Header 4 +
# Proto/State/Phase 3），回包一长老 App 直接判"长度不合理"，连连接都保不住。
# 所以固件版本号这类**新**信息只能走 0x0772/0x0773 这种"新开一条命令"的路
# （uart.c 里 0x0770 就是这么加的）。别往 0x076B 后面追加字段。
SELFTEST_REPLY_SIZE = 7

def crc16(data: bytes) -> int:
    """poly 0x1021 / init 0 / MSB-first / 无反射（driver/crc.c:24-47）。"""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc

def mask_xor(data: bytes) -> bytes:
    return bytes(b ^ MASK[i % 16] for i, b in enumerate(data))

def encode_reply(msg: bytes, crc: int | None = None) -> bytes:
    """回包 = AB CD len(2 LE) | 掩码(msg) | 掩码(crc16 LE) | DC BA。

    固件对**请求**一直真算 CRC，对**回包**以前恒填 0xFFFF（等于不算），App 看到
    0xFFFF 就跳过校验 —— 那是照当时的 uart.c 抄的，不是协议本意。现在 uart.c 的
    `SendReply`/`SendReply_VCP` 已经真算，这里跟着改，否则模型是个假电台。
    CRC 算在混淆之前，位置偏移量是 Size 与 Size+1（见 verify_crc.py）。
    传 `crc=0xFFFF` 可以模拟老固件的回包。
    """
    n = len(msg)
    c = crc16(msg) if crc is None else crc
    body = mask_xor(msg + bytes([c & 0xFF, c >> 8]))
    return bytes([0xAB, 0xCD, n & 0xFF, n >> 8]) + body + bytes([0xDC, 0xBA])

def msg(cmd: int, payload: bytes = b"") -> bytes:
    return bytes([cmd & 0xFF, cmd >> 8, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF]) + payload

def request_bytes(cmd: int, payload: bytes = b"") -> bytes:
    """客户端→电台的一帧：msg 补齐偶数长度，尾部带真 CRC（RadioLink.kt:50-64）。"""
    m = msg(cmd, payload)
    if len(m) % 2:
        m += b"\x00"
    body = mask_xor(m + crc16(m).to_bytes(2, "little"))
    return bytes([0xAB, 0xCD, len(m) & 0xFF, len(m) >> 8]) + body + bytes([0xDC, 0xBA])

def parse_replies(buf: bytearray) -> tuple[list[Request | None], bytearray]:
    """电台→客户端的流式解析，规则抄 RadioLink.kt:117-140。

    和固件侧 `drain_frames` 唯一的区别就在 CRC：回包那两字节固件恒填 0xFF，
    所以客户端只在"真填了别的值"时才比对。把这两套判据混用，模型会拒掉自己
    发出的每一个回包。
    """
    out: list[Request | None] = []
    while True:
        i = buf.find(bytes([0xAB, 0xCD]))
        if i < 0:
            return out, buf[-3:]
        if i + 4 > len(buf):
            return out, buf[i:]
        size = buf[i + 2] | (buf[i + 3] << 8)
        if size < 4 or size > 1024:                   # RadioLink.kt:124 的长度上限
            buf = bytearray(buf[i + 4:])
            continue
        if i + size + 8 > len(buf):
            return out, buf[i:]
        body = mask_xor(bytes(buf[i + 4:i + 4 + size + 2]))
        foot = bytes(buf[i + 4 + size + 2:i + size + 8])
        crc_rx = body[size] | (body[size + 1] << 8)
        if foot != bytes([0xDC, 0xBA]) or (crc_rx != 0xFFFF and crc_rx != crc16(body[:size])):
            out.append(None)
            buf = bytearray(buf[i + size + 8:])
            continue
        paylen = body[2] | (body[3] << 8)
        out.append(Request(body[0] | (body[1] << 8), paylen, body[4:4 + paylen]))
        buf = bytearray(buf[i + size + 8:])


def parse_fw_tag(resp) -> dict:
    """解析 0x0773 回包载荷 —— 固件标识 + 实测生效的校准 ppm。

    返回 {ver, ppm, ppm_max, proto, phase, clamped}：
      ver      固件标识（DPH_FW_TAG，例如 "v6.3.0"；固件最多塞 7 字符 + NUL）
      ppm      真正写进 BK4819 REG_3B 的偏移量（已按 PpmMax 钳过）
      ppm_max  DPH_PPM_MAX，即"存储侧与施加侧共用的那个上限"（当前 76）
      proto    0x076A 回包里那个 proto（6 = 带第二套逻辑页）
      phase    0..7，口径与 0x076A 的第三字节、信息页 $? 完全一致
               （6=预听 PRE-LISTEN，7=尾音 POST-LISTEN，只能往后追加）
      clamped  abs(ppm) == ppm_max。**这只是"到轨"的判断，不是悔改记录**：
               用户填的 76 与填了 200 被钳成 76，在这里长得一模一样，所以
               真正的结论要拿用户输入去比（0x0772 只回生效值，不回原始值）。
               App/PC 侧校验范围时按 ppm_max 拦，别等固件钳。

    resp 收几种形态都行：ask() 直接返回的载荷 bytes（最常用）、
    parse_replies() 给的 Request、或者任何带 .payload 的对象。长度不足一律
    抛 ValueError —— 返回半截字段的解析器比没有解析器更坏。
    """
    if resp is None:
        raise ValueError("0x0773 没有回包（resp=None）")
    payload = resp.payload if hasattr(resp, "payload") else bytes(resp)
    if len(payload) < FW_TAG_SIZE:
        raise ValueError(f"0x0773 载荷只有 {len(payload)} 字节，应≥{FW_TAG_SIZE}："
                         f"{payload.hex()}（老固件不认 0x0772 时压根不回来）")
    ver_raw = payload[0:8]
    ver = ver_raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
    ppm = int.from_bytes(payload[8:9], "little", signed=True)
    ppm_max = payload[9]
    return {
        "ver": ver,
        "ppm": ppm,
        "ppm_max": ppm_max,
        "proto": payload[10],
        "phase": payload[11],
        "clamped": ppm_max != 0 and abs(ppm) == ppm_max,
    }


# ── 传输：数据口一个客户端，控制口随时能连 ──
