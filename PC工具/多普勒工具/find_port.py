"""找电台那个串口，并且说清楚为什么选了它。

MuMu 会把 USB 设备抢进模拟器，宿主这边留下一个 VID 36B7 的虚拟串口：
它照样能打开，但一个字节也转不过去。所以不能只看"有没有 COMx"，
得按 VID 排序，把真设备排在前面、把模拟器的虚拟口排到最后。

    python find_port.py          # 打印候选和选中的口
    python find_port.py --json   # 给别的脚本用
"""

import json
import sys

import serial.tools.list_ports as lp

# MuMu / 网易的虚拟串口，看着像设备，实际不转发数据
VIRTUAL_VIDS = {0x36B7}

# 常见 USB 串口芯片，优先级从高到低
KNOWN = {
    0x1A86: "CH340/CH343",
    0x10C4: "CP210x",
    0x0403: "FTDI",
    0x067B: "PL2303",
    0x2341: "Arduino",
    0x0483: "STM32 CDC",
    0x2E8A: "RP2040 CDC",
}


def score(port):
    vid = port.vid or 0
    if vid in VIRTUAL_VIDS:
        # 注意：这台机器的电台之前就是从 VID 36B7 这个口通过话的，
        # 所以不能直接排除，只是排在真芯片后面、并且提示一句。
        return 5, "疑似模拟器虚拟口(仍会尝试)"
    if vid in KNOWN:
        return 100, KNOWN[vid]
    desc = (port.description or "").lower()
    if "serial" in desc or "串行" in desc:
        return 50, "USB 串口"
    return 10, "未知设备"


def candidates():
    out = []
    for p in lp.comports():
        s, why = score(p)
        out.append({"device": p.device, "score": s, "why": why,
                    "desc": p.description, "vid": p.vid, "pid": p.pid})
    out.sort(key=lambda x: -x["score"])
    return out


# 低于这个分就不要自动选：兜底"未知设备"是 10 分，虚拟口 5 分。
# 以前 pick() 判的是 score > 0，最低也是 10，于是恒返回第一个口，
# main() 里"没有可用真串口"的提示永远走不到（报告 第六部分·回归项 4）。
MIN_SCORE = 50


def pick():
    for c in candidates():
        if c["score"] >= MIN_SCORE:
            return c["device"]
    return None


def main():
    cands = candidates()
    if "--json" in sys.argv:
        print(json.dumps({"chosen": pick(), "candidates": cands}, ensure_ascii=False))
        return
    if not cands:
        print("一个串口都没有。电台插上了吗？驱动装了吗？")
        return
    for c in cands:
        mark = "->" if c["device"] == pick() else "  "
        print(f"{mark} {c['device']:<6} {c['why']:<22} {c['desc']}")
    chosen = pick()
    if chosen:
        print(f"\n用 {chosen}")
    else:
        # 以前这里永远走不到；现在没有 >= MIN_SCORE 的口才提示，并把候选列出来
        # 让人自己判断（报告 第六部分·回归项 4）。
        print(f"\n没有可信的真串口（都低于 {MIN_SCORE} 分），不敢自动选：")
        for c in cands:
            print(f"   {c['device']:<6} {c['score']:>3} 分  {c['why']}")
        print("常见原因：设备被 MuMu 的虚拟口抢走了。")
        print("在 MuMu 的 USB/设备面板里把这台电台断开（或直接关掉模拟器），")
        print("Windows 就会重新出现一个 COM 口，再跑一次这个脚本；")
        print("也可以在 doppler_tool.py 的下拉框里手动指定。")


if __name__ == "__main__":
    main()
