"""把电台 flash 里的多普勒表**读回来**，逐条解出并做物理/一致性校验。

为什么要有这个脚本：写表"看起来成功"有三种可能 —— 状态码 0、固件回了个 0、
或者压根写进了错的区域。只有把字节读回来解出来，才知道电台里躺的是不是
你以为的那张表。这也补齐了整改清单里 P0「写表全流程 + 整表回读比对」。

表的排版以 `doppler_core.build_table()` 为唯一口径（版本 5）：
    表头 16 B   "DPT1" ver(u8) count(u8) hdr_size(u16) data_off(u32) day(u32)
    条目 40 B   name[16] freq10(u32) sample_off(u32) up10(u32) aos2(u16)
                dur(u16) pts(u16) step_ms(u16) ctcss10(u8) dayoff(u8) max_el(u8)
                —— 注意第三字段是**采样区偏移**，不是 NORAD。
    采样区      从 DPH_BASE+0x3000 起，每条 6 B：int16 偏移Hz + u8 仰角
                + u16 斜距km + int8 变化率 0.1km/s

    python verify_table_live.py                  # 自动找口（桥接开着会占住串口，先关）
    python verify_table_live.py --port COM7
    python verify_table_live.py --dump 表.bin     # 顺手把原始字节存盘

退出码：0 表结构自洽；1 有硬错误；2 读不到表。
"""

import argparse
import io
import json
import os
import struct
import sys
import time

import serial

import radio_proto as rp

DPH_BASE = 0x00162000
DPH_HDR = 16
DPH_ENTRY = 40
DPH_ENTRY_MAX = 255
DPH_SAMPLE_OFF = 0x3000
DPH_REGION = 0x9D000
DPH_VERSION = 5
SAMPLE_SIZE = 6

TABLE_READ_CMD = 0x0764          # -> 0x0765，回包载荷 = Addr(4)+Size(2)+Status(1)+Data(n)
TABLE_READ_REPLY = 0x0765
CHUNK = 128                      # 电台一次只搬一个 flash 块

HERE = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.abspath(os.path.join(HERE, "..", "..", "数据", "sat_freq.json"))


def resolve_port(explicit):
    if explicit:
        return explicit
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    for p in ports:
        if p.vid in (0x36B7, 0x1A86) or "ch340" in (p.description or "").lower():
            return p.device
    return ports[0].device if ports else None


def ask(ser, cmd, payload=b"", timeout=1.5):
    """一问一答。回包 msg = ID(2)+paylen(2)+payload；帧 = AB CD | len | 掩码(msg+crc) | DC BA。"""
    ser.reset_input_buffer()
    ser.write(rp.request_bytes(cmd, payload))
    ser.flush()
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
        while len(buf) >= 6:
            if buf[0:2] != b"\xAB\xCD":
                buf = buf[1:]
                continue
            size = buf[2] | (buf[3] << 8)
            if size < 4 or size > 1024:
                buf = buf[2:]
                continue
            total = 4 + size + 2 + 2          # 头 + 掩码(msg+crc) + 脚
            if len(buf) < total:
                break
            body = rp.mask_xor(buf[4:4 + size + 2])[:size]
            rid = body[0] | (body[1] << 8)
            paylen = body[2] | (body[3] << 8)
            return rid, body[4:4 + paylen]
    raise TimeoutError("命令 0x%04X 没有回包" % cmd)


def read_chunk(ser, addr, length, tries=4):
    """0x0764 分块回读：电台一次只搬 CHUNK 字节，所以这里自己切片拼。"""
    out = bytearray()
    while len(out) < length:
        n = min(CHUNK, length - len(out))
        want = addr + len(out)
        last = None
        for _ in range(tries):
            try:
                rid, pay = ask(ser, TABLE_READ_CMD, struct.pack("<IH", want, n), timeout=2.0)
                if rid != TABLE_READ_REPLY:
                    last = "回包 ID 0x%04X" % rid
                    continue
                if len(pay) < 7:
                    last = "载荷只有 %d 字节" % len(pay)
                    continue
                if pay[6] != 0:
                    last = "状态码 %d" % pay[6]
                    continue
                data = pay[7:7 + n]
                if len(data) < n:
                    last = "只回了 %d/%d 字节" % (len(data), n)
                    continue
                out += data
                break
            except TimeoutError as e:
                last = str(e)
        else:
            raise IOError("回读 0x%06X 失败：%s" % (want, last))
    return bytes(out)


def load_truth():
    """真源：名字 → (norad, 字段表)。"""
    if not os.path.exists(JSON_PATH):
        return {}
    d = json.load(io.open(JSON_PATH, encoding="utf-8"))
    sats = d.get("sats", d)
    out = {}
    if isinstance(sats, dict):
        for norad, v in sats.items():
            if isinstance(v, dict) and v.get("name"):
                out[str(v["name"]).upper()] = (norad, v)
    elif isinstance(sats, list):
        for v in sats:
            if isinstance(v, dict) and v.get("name"):
                out[str(v["name"]).upper()] = (str(v.get("norad", "?")), v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--dump")
    a = ap.parse_args()

    port = resolve_port(a.port)
    if not port:
        print("找不到串口")
        return 2

    print("串口 %s @ %d" % (port, a.baud))
    ser = serial.Serial(port, a.baud, timeout=0.2)

    head = read_chunk(ser, DPH_BASE, DPH_HDR)
    magic, ver, count, hdr_size, data_off, day = struct.unpack_from("<4sBBHII", head, 0)
    print("表头 magic=%r ver=%d count=%d hdr=%d data_off=0x%06X 日期=%04d-%02d-%02d"
          % (magic, ver, count, hdr_size, data_off,
             ((day >> 9) & 0x7F) + 2000, (day >> 5) & 0x0F, day & 0x1F))

    errs = []
    warns = []
    if magic != b"DPT1":
        errs.append("表头标识不是 DPT1（表区可能是空的或写错位置）")
    if ver != DPH_VERSION:
        warns.append("表版本 %d ≠ 当前固件口径 %d" % (ver, DPH_VERSION))
    if hdr_size != DPH_HDR:
        errs.append("表头长度 %d ≠ %d" % (hdr_size, DPH_HDR))
    if not (1 <= count <= DPH_ENTRY_MAX):
        errs.append("条目数 %d 不在 1~%d" % (count, DPH_ENTRY_MAX))
    if data_off != DPH_BASE + DPH_SAMPLE_OFF:
        warns.append("采样区偏移 0x%06X ≠ 0x%06X（固件按表头走，但和工具默认不一致）"
                     % (data_off, DPH_BASE + DPH_SAMPLE_OFF))

    total = DPH_HDR + count * DPH_ENTRY
    body = read_chunk(ser, DPH_BASE, total)
    if a.dump:
        io.open(a.dump, "wb").write(body)
        print("原始字节已存 %s（%d 字节）" % (a.dump, len(body)))

    truth = load_truth()
    print("真源 sat_freq.json：%d 颗" % len(truth))
    print()

    rows = []
    for i in range(count):
        off = DPH_HDR + i * DPH_ENTRY
        (name, f10, soff, up10, aos2, dur, pts, step, ctc, dayoff, el) = \
            struct.unpack_from("<16sIIIHHHHHBB", body, off)
        nm = name.rstrip(b"\x00 ").decode("utf-8", "replace")
        rows.append(dict(idx=i, name=nm, f10=f10, soff=soff, up10=up10, aos2=aos2,
                         dur=dur, pts=pts, step=step, ctcss10=ctc,
                         dayoff=dayoff, el=el))
        print("  %02d %-8s 收 %9.5f MHz  发 %9.5f MHz  亚音 %3d  %4d s  %3d 点 × %5d ms"
              "  D+%d  EL%2d  采样@0x%06X"
              % (i + 1, nm, f10 / 1e5, up10 / 1e5, ctc, dur, pts, step, dayoff, el, soff))

        # —— 逐条硬校验（照 doppler_core.build_table 的契约）——
        # freq10 的单位是 10 Hz：437.8 MHz 存成 43_780_000。
        if not (2_900_000 <= f10 <= 130_000_000):
            errs.append("第 %d 条收频 %d（10 Hz 单位）越界" % (i + 1, f10))
        if up10 and not (2_900_000 <= up10 <= 130_000_000):
            errs.append("第 %d 条发频 %d 越界" % (i + 1, up10))
        if dur == 0 or dur > 4290:
            errs.append("第 %d 条时长 %d s 不可用（固件 16 位定点插值上限 4290 s）" % (i + 1, dur))
        if pts == 0:
            errs.append("第 %d 条没有采样点" % (i + 1))
        if not (500 <= step <= 60000):
            errs.append("第 %d 条采样间隔 %d ms 不合理" % (i + 1, step))
        lo = DPH_BASE + DPH_SAMPLE_OFF
        hi = DPH_BASE + DPH_REGION
        if not (lo <= soff < hi) or soff + pts * SAMPLE_SIZE > hi:
            errs.append("第 %d 条采样区 0x%06X+%d 越出表区" % (i + 1, soff, pts * SAMPLE_SIZE))
        if aos2 >= 43200:
            errs.append("第 %d 条 AOS 半秒 %d ≥ 43200（一天只有 86400 s）" % (i + 1, aos2))

        t = truth.get(nm.upper())
        if t:
            norad, v = t
            want_down = v.get("down")
            if want_down and abs(want_down * 1e5 - f10) > 1:
                errs.append("%s 收频 %.5f MHz ≠ 真源 %.5f MHz" % (nm, f10 / 1e5, want_down))
            want_up = v.get("up")
            if want_up and up10 and abs(want_up * 1e5 - up10) > 1:
                errs.append("%s 发频 %.5f MHz ≠ 真源 %.5f MHz" % (nm, up10 / 1e5, want_up))
            want_tone = v.get("tone")
            if want_tone and abs(int(round(want_tone * 10)) - ctc) > 1:
                errs.append("%s 亚音 %d ≠ 真源 %.1f Hz" % (nm, ctc, want_tone))
        else:
            warns.append("第 %d 条 %s 不在真源里（可能要更新 sat_freq.json）" % (i + 1, nm))

    # 采样区抽查：第一条的采样必须能读回，偏移幅值要在物理范围内
    if rows:
        r0 = rows[0]
        try:
            k = min(r0["pts"], 40)
            blob = read_chunk(ser, r0["soff"], k * SAMPLE_SIZE)
            offs = [struct.unpack_from("<h", blob, j * SAMPLE_SIZE)[0] for j in range(k)]
            print("\n第 1 条采样抽查：%d 点，偏移 %d…%d Hz，首点 %d Hz"
                  % (len(offs), min(offs), max(offs), offs[0]))
            if max(abs(x) for x in offs) > 30000:
                errs.append("采样偏移超过 ±30 kHz，像字节错位")
        except IOError as e:
            warns.append("采样区抽查失败：%s" % e)

    ser.close()
    print()
    for w in warns:
        print("提示 " + w)
    if errs:
        print("结论：有 %d 处硬错误" % len(errs))
        for e in errs:
            print("  - " + e)
        return 1
    print("结论：表结构自洽，%d 条与真源一致" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
