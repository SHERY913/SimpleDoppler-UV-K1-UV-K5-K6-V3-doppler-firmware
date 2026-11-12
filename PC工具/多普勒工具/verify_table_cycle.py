# -*- coding: utf-8 -*-
"""B3 写表全流程真机复验：可辨识改动表 → 整表写入 → 回读比对 → （人看屏）→ 原样恢复。

和 verify_table_write.py 的区别（那支仍可用，但只覆盖一半）：
  * 那支用 `link.write(DPH_BASE, 整段)` 一把写，**表头最先落盘**；
    固件的 DOPPLER_ReloadTable() 挂在「写到 DPH_BASE 那一帧成功」上，
    于是它验的是"半张表也会被当场重载"。GUI 的真实顺序是
    「擦 → 写样点 → 最后写 16 字节表头」，本脚本照 GUI 的顺序走。
  * 那支写回的是同一份字节，屏幕上什么都看不出来。本脚本写**改过的**表：
    第 1 条星名改成 TST1H、第 2 条 TST1H-B，两条 AOS 各后移 1 小时
    （aos2 字段 +1800，单位是 2 秒）。人在屏上能直接看见"表换了"，
    这才是 B3 要的"当场重载"证据。
  * 断电重启那一步脚本做不了：它会先把原表恢复好，再让人断电，
    重启后用 --check 回读一次，与备份比 md5。

用法（默认只读预演，不写 flash）：
    python verify_table_cycle.py                 # 读表 + 造改动表 + 打印计划，不写
    python verify_table_cycle.py --commit        # 真的擦写（会擦表区同区域）
    python verify_table_cycle.py --commit --hold 60   # 写完停 60 s 给人看屏
    python verify_table_cycle.py --restore <备份.bin>  # 出事时手工恢复
    python verify_table_cycle.py --check <备份.bin>    # 断电重启后回读比对

退出码：0 全对；1 有不一致；2 读不到表 / 表不合法。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import doppler_core as core          # noqa: E402
import doppler_tool as dt            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
BK_DIR = os.path.join(ROOT, "verify", "table-backups")


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def resolve_port(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    from serial.tools import list_ports
    for p in list_ports.comports():
        if p.vid in (0x36B7, 0x1A86) or "ch340" in (p.description or "").lower():
            return p.device
    return None


def read_span(link) -> tuple[bytes, int]:
    """读「表头 + 条目 + 实际用到的样点」，按扇区对齐。返回 (blob, span)。"""
    head = link.read(core.DPH_BASE, core.DPH_HDR)
    magic, ver, count, hdr_size, data_off, _day = struct.unpack_from("<4sBBHII", head, 0)
    if magic != b"DPT1" or not (1 <= count <= core.DPH_ENTRY_MAX):
        raise RuntimeError(f"表不合法：magic={magic!r} ver={ver} count={count}")
    total = core.DPH_HDR + count * core.DPH_ENTRY
    entries = link.read(core.DPH_BASE, total)
    blob_len = 0
    for i in range(count):
        f = struct.unpack_from("<16sIIIHHHHHBB", entries, core.DPH_HDR + i * core.DPH_ENTRY)
        blob_len = max(blob_len, (f[2] - core.DPH_BASE - core.DPH_SAMPLE_OFF) + f[6] * core.DPH_PT)
    span = max(total, core.DPH_SAMPLE_OFF + blob_len)
    span = (span + core.DPH_SECTOR - 1) // core.DPH_SECTOR * core.DPH_SECTOR
    return link.read(core.DPH_BASE, span), span


def make_distinct(blob: bytes) -> bytes:
    """把表改得人眼能认出来：星名改掉、两条 AOS 各后移 1 小时。"""
    out = bytearray(blob)
    magic, ver, count, hdr_size, data_off, day = struct.unpack_from("<4sBBHII", out, 0)
    if count < 1:
        raise RuntimeError("表里没有条目，改不了")
    names = ["TST1H", "TST1H-B"]
    for i in range(min(count, len(names))):
        off = core.DPH_HDR + i * core.DPH_ENTRY
        (name, f10, doff, up10, aos2, dur, pts, step, ctc, flags, el) = \
            struct.unpack_from("<16sIIIHHHHHBB", out, off)
        new_aos2 = (aos2 + 1800) % 43200          # +1 小时；字段单位 2 秒
        struct.pack_into("<16sIIIHHHHHBB", out, off,
                         names[i].encode("ascii").ljust(16, b"\x00"),
                         f10, doff, up10, new_aos2, dur, pts, step, ctc, flags, el)
    return bytes(out)


def erase_and_write(link, table: bytes, sectors: int) -> float:
    """照 GUI 的顺序：先擦数据扇区、最后擦表头 → 写样点 → 最后写 16 字节表头。

    **擦除顺序不能反**（2026-11-08 真机踩到的）：固件 `DPH_TableErase` 里有一道
    归属闸门 —— 扇区自己不是全 FF、且表头不是 DPT1 时判 OCCUPIED(4) 拒擦。
    先把 0x162000（表头扇区）擦成空白，后面每个数据扇区就都变成"别人的数据"，
    0x163000 直接回 code=4，表只剩半张。数据扇区在前时表头仍是 DPT1 → own=OURS
    → 整段放行，最后再擦表头。
    """
    t0 = time.time()
    order = list(range(sectors - 1, 0, -1)) + [0]   # 数据扇区 → 表头扇区
    for n, s in enumerate(order, 1):
        link.erase(core.DPH_BASE + s * core.DPH_SECTOR)
        print("    擦 0x%06X (%d/%d)" % (core.DPH_BASE + s * core.DPH_SECTOR, n, sectors), flush=True)
    body = table[core.DPH_HDR:]
    if body:
        link.write(core.DPH_BASE + core.DPH_HDR, body)
        print("    写样点 %d B" % len(body), flush=True)
    link.write(core.DPH_BASE, table[:core.DPH_HDR])
    print("    写表头 %d B（这一帧落盘 = 固件当场重载）" % core.DPH_HDR, flush=True)
    return time.time() - t0


def compare(tag: str, got: bytes, want: bytes) -> bool:
    if got == want:
        print("  [OK] %s：逐字节一致（%d B，md5 %s）" % (tag, len(want), md5(want)), flush=True)
        return True
    n = min(len(got), len(want))
    bad = [i for i in range(n) if got[i] != want[i]]
    print("  [!!] %s：%d 个字节不同" % (tag, len(bad) + abs(len(got) - len(want))), flush=True)
    for i in bad[:8]:
        print("       0x%06X 期望 %02X 实际 %02X" % (core.DPH_BASE + i, want[i], got[i]), flush=True)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--commit", action="store_true", help="真的擦写；不给就是只读预演")
    ap.add_argument("--hold", type=float, default=0.0, help="写完停几秒给人看屏")
    ap.add_argument("--restore", metavar="BACKUP", help="把备份原样写回")
    ap.add_argument("--check", metavar="BACKUP", help="只回读并与备份比对（断电重启后用）")
    a = ap.parse_args()

    port = resolve_port(a.port)
    if not port:
        print("找不到串口（VID 036B7/1A86 或 ch340）")
        return 2
    print("串口 %s @ %d" % (port, a.baud), flush=True)

    if a.check:
        want = open(a.check, "rb").read()
        link = dt.Link(port)
        try:
            got, span = read_span(link)
            print("  期望 %d B md5=%s" % (len(want), md5(want)))
            print("  实际 %d B md5=%s" % (len(got), md5(got)))
            n = min(len(got), len(want))
            ok = got[:n] == want[:n]
            print("  %s" % ("[OK] 断电前后一致" if ok else "[!!] 断电后内容变了"))
            return 0 if ok else 1
        finally:
            link.close()

    link = dt.Link(port)
    try:
        before, span = read_span(link)
        sectors = span // core.DPH_SECTOR
        print("现表 %d B / %d 扇区，md5=%s" % (span, sectors, md5(before)), flush=True)

        if a.restore:
            want = open(a.restore, "rb").read()
            if len(want) > span:
                print("备份 %d B 比现表 %d B 长：按备份的扇区数擦" % (len(want), span))
                sectors = (len(want) + core.DPH_SECTOR - 1) // core.DPH_SECTOR
            print("恢复 %s → 电台" % a.restore, flush=True)
            erase_and_write(link, want, sectors)
            compare("恢复后回读", link.read(core.DPH_BASE, len(want)), want)
            return 0

        patched = make_distinct(before)
        errs = core.validate_table(patched)
        print("改动表 %d B，md5=%s" % (len(patched), md5(patched)), flush=True)
        print("  第 1/2 条星名 → TST1H / TST1H-B，AOS 各 +1 小时", flush=True)
        if errs:
            print("  校验不过，不能写：")
            for e in errs:
                print("    - " + e)
            return 2
        print("  校验通过", flush=True)

        os.makedirs(BK_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bk = os.path.join(BK_DIR, f"table-before-{stamp}.bin")
        with open(bk, "wb") as fh:
            fh.write(before)
        print("  原表备份 → %s" % bk, flush=True)

        if not a.commit:
            print("\n只读预演（没给 --commit）：上面是计划，flash 一个字节没动。", flush=True)
            return 0

        print("\n步 1/3 擦 + 写改动表", flush=True)
        erase_and_write(link, patched, sectors)
        print("步 2/3 整表回读比对", flush=True)
        if not compare("改动表回读", link.read(core.DPH_BASE, len(patched)), patched):
            print("\n回读不符 —— 电台里现在是什么不可信。立刻做：", flush=True)
            print("  python verify_table_cycle.py --restore %s" % bk, flush=True)
            return 1

        print("\n>>> 现在看手台屏：就绪/跟踪页的星名应显示 TST1H（不再显示原来的星名），", flush=True)
        print(">>> 且是「没有退出多普勒页」就变过来的 —— 这就是当场重载。", flush=True)
        if a.hold > 0:
            print(">>> %.0f s 后自动恢复原表，别按键。" % a.hold, flush=True)
            time.sleep(a.hold)
        else:
            print(">>> 恢复原表……", flush=True)

        print("\n步 3/3 恢复原表并回读确认", flush=True)
        erase_and_write(link, before, sectors)
        if not compare("原表恢复回读", link.read(core.DPH_BASE, len(before)), before):
            print("\n恢复回读不符！备份仍在：%s" % bk, flush=True)
            return 1

        print("\n四步里的三步已过：整表写入 / 回读比对 / 当场重载（屏上确认）。", flush=True)
        print("剩断电重启：现在断电、再上电，然后跑", flush=True)
        print("  python verify_table_cycle.py --check %s" % bk, flush=True)
        return 0
    finally:
        link.close()


if __name__ == "__main__":
    sys.exit(main())
