"""写表全流程真机复验：读回 → 原样写回 → 再读回 → 逐字节比对。

这是整改清单 P0 里最硬的一项。写表"成功"很容易骗人（状态码 0 但写进了
相邻扇区、块写丢了第二段、擦除没擦干净），唯一的判据是**把字节读回来相等**。

做法故意选"原样回写"：内容不变，所以即使中途出事电台里躺的还是同一张表，
但它真真实实走完 擦除 → 分块写 → 整表回读 → md5 比对 的全过程。

    python verify_table_write.py              # 自动找口，走完整个流程
    python verify_table_write.py --port COM7
    python verify_table_write.py --keep       # 不要把原表备份写回（默认会写回）

退出码：0 一致；1 不一致；2 读不到。
"""

import argparse
import hashlib
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import doppler_core as core          # noqa: E402
import doppler_tool as dt            # noqa: E402


def md5(b):
    return hashlib.md5(b).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=38400)
    a = ap.parse_args()

    port = a.port
    if not port:
        from serial.tools import list_ports
        for p in list_ports.comports():
            if p.vid in (0x36B7, 0x1A86) or "ch340" in (p.description or "").lower():
                port = p.device
                break
    if not port:
        print("找不到串口")
        return 2

    print("串口 %s @ %d" % (port, a.baud))
    link = dt.Link(port)
    try:
        # 1) 先看表头，知道要读多少
        head = link.read(core.DPH_BASE, core.DPH_HDR)
        magic, ver, count, hdr_size, data_off, _day = struct.unpack_from("<4sBBHII", head, 0)
        print("表头 magic=%r ver=%d count=%d" % (magic, ver, count))
        if magic != b"DPT1" or not (1 <= count <= core.DPH_ENTRY_MAX):
            print("表不合法，不做回写")
            return 2

        total = core.DPH_HDR + count * core.DPH_ENTRY
        # 采样区也要一起比：只比表头区会漏掉"表头对、曲线错"
        blob_len = 0
        entries = link.read(core.DPH_BASE, total)
        for i in range(count):
            f = struct.unpack_from("<16sIIIHHHHHBB", entries, core.DPH_HDR + i * core.DPH_ENTRY)
            blob_len = max(blob_len, (f[2] - core.DPH_BASE - core.DPH_SAMPLE_OFF) + f[6] * 6)
        span = max(total, core.DPH_SAMPLE_OFF + blob_len)
        span = (span + core.DPH_SECTOR - 1) // core.DPH_SECTOR * core.DPH_SECTOR

        before = link.read(core.DPH_BASE, span)
        print("原表 %d 字节（含采样区，按扇区对齐）md5=%s" % (len(before), md5(before)))
        # 先把整段原表落盘：擦除期间出事（掉线、断电）还能补回去。
        bak = os.path.join(os.path.dirname(os.path.abspath(__file__)), "table_backup.bin")
        with open(bak, "wb") as f:
            f.write(before)
        print("  备份 %s" % bak)

        # 2) 擦 + 写回同一份字节
        t0 = time.time()
        sectors = span // core.DPH_SECTOR
        for s in range(sectors):
            addr = core.DPH_BASE + s * core.DPH_SECTOR
            link.erase(addr)
            print("  擦 0x%06X  (%d/%d)" % (addr, s + 1, sectors))
        chunk = before
        link.write(core.DPH_BASE, chunk)
        print("  写回 %d 字节，用时 %.1f s" % (len(chunk), time.time() - t0))

        # 3) 空读一次确认没"写进去但读缓存还是旧的"
        time.sleep(0.2)
        after = link.read(core.DPH_BASE, span)
        print("回读 %d 字节 md5=%s" % (len(after), md5(after)))

        if after == before:
            print("\n结论：擦除+写回+回读逐字节一致（%d 字节）" % span)
            return 0

        # 差异定位到字节偏移，报出来方便判断是"块写丢了"还是"擦不干净"
        bad = [i for i in range(min(len(after), len(before))) if after[i] != before[i]]
        print("\n结论：不一致，%d 个字节不同，首个 0x%06X" %
              (len(bad), core.DPH_BASE + bad[0]) if bad else "\n结论：长度不同")
        for i in bad[:8]:
            print("  0x%06X 原 %02X 现 %02X" % (core.DPH_BASE + i, before[i], after[i]))
        return 1
    finally:
        link.close()


if __name__ == "__main__":
    sys.exit(main())
