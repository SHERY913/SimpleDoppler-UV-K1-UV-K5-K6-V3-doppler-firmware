"""把多普勒表区那 16 个扇区擦成空，只擦这一块，别的一个字节不碰。

用法（先拔掉其它串口设备，机器开着并停在任意页面）：
    python clear_table.py            # 自动找口
    python clear_table.py COM7       # 指定口

表区 0x162000..0x172000（64 KiB）在原厂镜像上正压在 A-law 语音包中间，所以
第一次在这台机器上装表时，固件的防呆（DPH_TableErase 判 FOREIGN → 拒擦）
会拦下来 —— 那是设计。`--yes` 现在不只是"我确认"，还会先发 0x0776 借一份
擦除额度，让固件知道"这块地是主人自己要清掉的"，然后才逐扇区擦。

擦完回读确认全 FF 才算成功。之后再用 PC 工具/App 写表就不会被闸门拦。
"""
import sys
import time

from doppler_core import DPH_BASE, DPH_REGION, DPH_SECTOR

try:
    from find_port import pick as find_port
except Exception:                                    # 找不到就当没这个辅助
    find_port = None


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else None
    if port is None:
        if find_port is None:
            sys.exit("没给串口号，且 find_port 不可用：python clear_table.py COM7")
        port = find_port()
        if not port:
            sys.exit("没找到串口，手动指定：python clear_table.py COM7")

    from doppler_tool import Link

    link = Link(port)
    try:
        print(f"目标：0x{DPH_BASE:06X}..0x{DPH_BASE + DPH_REGION:06X}"
              f"（{DPH_REGION // DPH_SECTOR} 个扇区，{DPH_REGION // 1024} KiB）")
        print("擦除前先看一眼这块现在是什么：")
        head = link.read(DPH_BASE, 16)
        print("  头 16 字节:", head.hex(" "))
        if head[:4] == b"DPT1":
            print("  → 是我们的旧表，可以擦")
        elif head == b"\xff" * 16:
            print("  → 已经是空的，擦不擦都一样")
        else:
            print("  → 这里现在是别人的东西（原厂 A-law 语音包就落在这段）")
            print("    擦掉不可恢复。确认要擦就加 --yes：")
            print("    python clear_table.py %s --yes" % port)
            if "--yes" not in sys.argv:
                return
            # 固件的擦除防呆会判 FOREIGN 并返回 code=4。借一次额度让它放行；
            # 借不到（老固件不认 0x0776）就直接说清，别让用户对着 code=4 猜。
            try:
                token, credits = link.unlock_region()
            except Exception as e:
                sys.exit(f"解锁失败，一个字节都没擦：{e}\n"
                         "这台机器的固件不支持 0x0776，先刷带受控清地的新固件。")
            if credits == 0:
                sys.exit("固件没认这次解锁（令牌不符或额度已用尽），一个字节都没擦。")
            print(f"  → 已解锁（token=0x{token:08X}，剩余额度 {credits}），开始擦")

        ok = 0
        for i in range(DPH_REGION // DPH_SECTOR):
            addr = DPH_BASE + i * DPH_SECTOR
            link.erase(addr)
            ok += 1
            print(f"  擦 0x{addr:06X} ok")
            time.sleep(0.02)

        time.sleep(0.3)
        bad = 0
        for i in range(DPH_REGION // DPH_SECTOR):
            addr = DPH_BASE + i * DPH_SECTOR
            if link.read(addr, 256) != b"\xff" * 256:
                bad += 1
                print(f"  !! 0x{addr:06X} 回读不是全 FF")
        print("完成：%d 个扇区，回读异常 %d 个" % (ok, bad))
        if bad:
            sys.exit(1)
    finally:
        link.close()


if __name__ == "__main__":
    main()
