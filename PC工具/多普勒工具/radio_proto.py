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

def encode_reply(msg: bytes) -> bytes:
    """回包 = AB CD len(2 LE) | 掩码(msg) | 掩码(FF FF) | DC BA。

    uart.c:302-303 / :296-299（VCP 同一段）：固件对回包**根本不计算 CRC**，
    那两字节恒是 `Obfuscation[(Size+i)%16] ^ 0xFF`。App 侧 RadioLink.kt:129-136
    就是照这个实测结果放过它的。模型要是不一样，就等于测了个假电台。
    """
    n = len(msg)
    body = mask_xor(msg + bytes([0xFF, 0xFF]))
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


# ── 传输：数据口一个客户端，控制口随时能连 ──
