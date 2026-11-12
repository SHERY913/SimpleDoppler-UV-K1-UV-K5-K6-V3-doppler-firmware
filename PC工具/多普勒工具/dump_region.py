"""把电台 flash 的一段原样搬出来 —— B7 那条"真镜像对照"的来源。

为什么需要这个脚本：`verify_table_fit.py` 里三条真镜像对照（`全满.bin` /
`无多系统.bin` / `汉化-02-external-flash.bin`）是**原厂与汉化镜像**，串口 dump
产不出来；本地又早已没有这些文件，于是那三条一直静默跳过 —— 回归网里的一条
假绿。这里把手台自己的表区（连前后各一扇区上下文）dump 成一张按整片 flash
布局的稀疏文件，给回归网补一个"真的、本机的、能复跑"的对照。

    python dump_region.py                 # → ../../verify/fixtures/handset-<日期>.bin
    python dump_region.py --ctx 0         # 只 dump 表区 64 KiB
    python dump_region.py --port COM7 --out X.bin

布局：文件按整片 flash 排，表区落在 DPH_BASE 偏移；其余位置是稀疏空洞
（读出来是 0）。`verify_table_fit.py` 只 seek 到 DPH_BASE 再读 64 KiB，
所以空洞无所谓，但偏移必须对。

注意：**表区外的字节这台固件不给读**。`DPH_TableRangeOk()`（doppler.c:365）
把 0x0764 回读卡在 `[DPH_BASE, DPH_BASE+DPH_REGION)` 内，越界回状态码 1，
所以"表区前后各 dump 一扇区上下文"做不到 —— 也就只能拿 64 KiB 当对照。

顺带写一个同名 `.json`：版本串、proto、ppm、表头解码、dump 时刻。
下次对比"表是这一版还是上一版"只看 md5 + 日期就够。

退出码：0 成功；2 读不到。
"""

import argparse
import hashlib
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import serial                                   # noqa: E402

import verify_table_live as vl                  # noqa: E402  复用 ask/read_chunk
import doppler_core as core                     # noqa: E402
import probe_fw                                 # noqa: E402  复用 0x0772 解析

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.abspath(os.path.join(HERE, "..", "..", "verify", "fixtures"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--ctx", type=int, default=0,
                    help="占位。表区外的字节固件不给读（DPH_TableRangeOk），只能 0")
    ap.add_argument("--out")
    a = ap.parse_args()

    port = a.port or vl.resolve_port(None)
    if not port:
        print("找不到串口")
        return 2

    if a.ctx:
        print("忽略 --ctx：表区外回读会被固件拒（状态码 1），只 dump 64 KiB 表区")
    start = core.DPH_BASE
    length = core.DPH_REGION
    out = a.out or os.path.join(
        FIXTURES, "handset-%s.bin" % time.strftime("%Y-%m-%d"))
    if not os.path.isdir(os.path.dirname(out)):
        os.makedirs(os.path.dirname(out), exist_ok=True)

    print("串口 %s @ %d" % (port, a.baud))
    print("dump 0x%06X..0x%06X（%d B，按 128 B 分块）" % (start, start + length, length))
    ser = serial.Serial(port, a.baud, timeout=0.2)
    t0 = time.time()
    try:
        blob = vl.read_chunk(ser, start, length)
    finally:
        ser.close()
    dt = time.time() - t0
    print("读完 %d B，用时 %.1f s（%.0f B/s）" % (len(blob), dt, len(blob) / max(dt, 1e-9)))

    # 稀疏镜像：把这段放到正确的 flash 偏移上
    if os.path.exists(out):
        os.remove(out)
    with open(out, "wb") as f:
        f.write(b"\x00" * start)
        f.write(blob)
    size = os.path.getsize(out)
    md5 = hashlib.md5(blob).hexdigest()
    print("镜像已存 %s（%d B，其中数据 %d B）" % (out, size, len(blob)))
    print("数据段 md5 %s" % md5)

    hdr = blob[:core.DPH_HDR]
    meta = {"dumped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "port": port, "start": "0x%06X" % start, "length": length,
            "md5": md5, "file": os.path.basename(out)}
    if hdr[:4] == b"DPT1":
        magic, ver, count, hdr_size, data_off, day = struct.unpack_from(
            "<4sBBHII", hdr, 0)
        meta.update({"magic": magic.decode("ascii", "replace"), "ver": ver,
                     "count": count, "data_off": "0x%06X" % data_off,
                     "day": "%04d-%02d-%02d" % (((day >> 9) & 0x7F) + 2000,
                                                (day >> 5) & 0x0F, day & 0x1F)})
        print("表头 ver=%d count=%d data_off=0x%06X 日期=%s"
              % (ver, count, data_off, meta["day"]))
        print("扇区分类：%s" % " ".join(
            "%s=%s" % (("0x%06X" % addr)[-5:], cls)
            for addr, cls in core.classify_region(blob)))
    else:
        meta["magic"] = hdr[:4].hex()
        print("表区不是 DPT1（magic=%s）—— 这张镜像不能当我们的表用" % meta["magic"])

    with open(os.path.splitext(out)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("元数据 %s" % (os.path.splitext(out)[0] + ".json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
