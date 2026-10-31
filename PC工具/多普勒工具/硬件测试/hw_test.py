# -*- coding: utf-8 -*-
"""真机回归：COM7 上的新固件（0x0772 自述 / REG_3B 守卫 / 0x0770 读回）。

只做四件事，全部可回退：
  T1 0x076A 自检        -> 协议/状态/phase
  T2 0x0772 固件自述     -> 版本 / 生效 ppm / ppm 上限
  T3 REG_3B 守卫        -> 读-写-再读，值必须不变，且链路还活着
  T4 接管 + 推帧 + 读回  -> 0x0770 必须等于刚推下去的 rx10，然后交还
"""
import os, sys, time, struct
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # 上一级就是工具根，项目搬家/改名不再断

import serial
import radio_proto as rp

PORT, BAUD = "COM7", 38400
ok_all = True

def line(s):
    print(s, flush=True)

def ask(ser, cmd, payload=b"", expect=5, timeout=1.2, reply_id=None):
    ser.reset_input_buffer()
    ser.write(rp.request_bytes(cmd, payload))
    ser.flush()
    buf = bytearray()
    end = time.time() + timeout
    while time.time() < end:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
        replies, rest = rp.parse_replies(buf)
        for r in replies:
            if r is not None and r.cmd == (reply_id if reply_id is not None else cmd + 1):
                return r
        buf = bytearray(rest)
    return None

def check(name, cond, detail=""):
    global ok_all
    ok_all = ok_all and bool(cond)
    line(("  [OK] " if cond else "  [!!] ") + name + (("  " + detail) if detail else ""))

with serial.Serial(PORT, BAUD, timeout=0.2) as ser:
    line("== T1 0x076A 自检 ==")
    r = ask(ser, rp.CMD_SELFTEST, b"", 7)
    if r is None:
        check("0x076B 有回包", False, "超时")
    else:
        proto, state, phase = r.payload[0], r.payload[1], r.payload[2]
        line("  proto=%d state=%d phase=%d" % (proto, state, phase))
        check("0x076B 长度 7（老 App 协议不变）", r.paylen == 3 and len(r.payload) == 3)

    line("== T2 0x0772 固件自述 ==")
    r = ask(ser, rp.CMD_FW_TAG, b"", 16)
    if r is None:
        check("0x0773 有回包", False, "超时")
        info = None
    else:
        info = rp.parse_fw_tag(r)
        line("  ver=%s ppm=%+d ppm_max=%d proto=%d phase=%d clamped=%s"
             % (info["ver"], info["ppm"], info["ppm_max"], info["proto"], info["phase"], info["clamped"]))
        check("帧长 16", len(r.payload) == rp.FW_TAG_SIZE)
        check("ppm_max == 76（新固件才有的上限）", info["ppm_max"] == 76)
        check("版本串非空", bool(info["ver"].strip("\x00 ")))

    line("== T3 REG_3B 守卫 ==")
    before = ask(ser, 0x0601, bytes([0x3B]), 7, reply_id=0x0601)
    if before is None:
        check("0x0601 读 REG_3B", False, "超时")
    else:
        v0 = before.payload[1] | (before.payload[2] << 8)
        line("  写之前 REG_3B = 0x%04X" % v0)
        ser.reset_input_buffer(); ser.write(rp.request_bytes(0x0602, bytes([0x3B]) + struct.pack("<H", (v0 ^ 0x0FFF) & 0xFFFF))); ser.flush(); time.sleep(0.5)
        after = ask(ser, 0x0601, bytes([0x3B]), 7, reply_id=0x0601)
        v1 = None if after is None else (after.payload[1] | (after.payload[2] << 8))
        line("  写之后 REG_3B = %s" % ("超时" if v1 is None else "0x%04X" % v1))
        check("REG_3B 未被改写", v1 == v0, "(写被固件拒绝)")
        alive = ask(ser, rp.CMD_FW_TAG, b"", 16)
        check("拒写后链路仍活（心跳没被掐）", alive is not None)

    line("== T4 接管 + 推帧 + 0x0770 读回 ==")
    take = ask(ser, rp.CMD_TAKEOVER, bytes([1]), 5)
    if take is None:
        check("0x0769 接管回包", False, "超时")
    else:
        line("  接管 state=%d" % take.payload[0])
        rx10 = 14580000          # 145.800000 MHz
        tx10 = 14520000
        frame = struct.pack("<IIhHhHh", rx10, tx10, 0, 500, 300, 4500, 0) + bytes([50, 0, 3]) + b"ISS" + bytes(8)
        ack = ask(ser, rp.CMD_PUSH_EXT, frame, 5, 1.5)
        check("0x076D 推帧回包", ack is not None, "" if ack is None else "state=%d" % ack.payload[0])
        time.sleep(0.4)
        back = ask(ser, rp.CMD_RX10, b"", 8)
        if back is None:
            check("0x0771 读回 VFO", False, "超时")
        else:
            got = struct.unpack("<I", back.payload[:4])[0]
            line("  推下去 rx10=%d  读回 rx10=%d" % (rx10, got))
            check("读回 == 推下去的值", got == rx10)
        ask(ser, rp.CMD_TAKEOVER, bytes([0]), 5)
        line("  已交还（0x0768=0）")

line("")
line("结论：" + ("全部通过" if ok_all else "有失败项，见 [!!]"))
sys.exit(0 if ok_all else 2)
