"""找电台那个串口，并且说清楚为什么选了它。

这台机器上 MuMu 会插一个 VID 36B7 的虚拟串口进来，所以不能只看 VID：
实测过 36B7 的口可能就是电台本身（VID 判据不可信），也见过它一个字节都
不转发的情况。可靠的办法只有一个 —— **打开发一帧 0x076A，看有没有回包**。

     python find_port.py          # 打印候选和选中的口
     python find_port.py --json   # 给别的脚本用
"""

import json
import sys

import serial.tools.list_ports as lp

# 以前这里叫 VIRTUAL_VIDS，直接当成"模拟器虚拟口"降权。实测 36B7 的口
# 照样能跟电台通话，所以只当作"信息"，选口看下面的探针结果。
SUSPECT_VIDS = {0x36B7}

# 常见 USB 串口芯片，只用来显示和排序，不再是唯一判据
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
    if vid in KNOWN:
        return 100, KNOWN[vid]
    if vid in SUSPECT_VIDS:
        return 40, "VID 36B7(可能是模拟器口，需探针确认)"
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


def probe(device, timeout=0.8):
    """发 0x076A，收到 0x076B 就算这台是电台。返回 True/False。"""
    try:
        import radio_proto as rp
        import serial
        import time
    except Exception:
        return False
    try:
        s = serial.Serial(device, 38400, timeout=0.05)
    except Exception:
        return False
    try:
        s.reset_input_buffer()
        s.write(rp.request_bytes(0x076A))
        s.flush()
        buf = b""
        end = time.time() + timeout
        while time.time() < end:
            c = s.read(256)
            if c:
                buf += c
                for r in rp.parse_replies(buf):
                    rr = r[0] if isinstance(r, list) else r
                    if rr is not None and rr.cmd == 0x076B:
                        return True
            else:
                time.sleep(0.01)
        return False
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def pick(use_probe=True):
    """先按芯片排序挑，挑不到就挨个探针；探针通了才敢用。"""
    cands = candidates()
    if not cands:
        return None
    # 探针优先，分数只用来排顺序。
    # 以前是"分数够高就直接用"，而 scores 只看 VID/PID —— CH340 全家都是
    # 1A86:7523，插着鼠标/USB 转 TTL/另一台手台时，分数高的那个可能是错的，
    # 于是写表写到别的串口上去（真机上表现为"电台没响应"）。
    # 探针（0x076A 有没有 0x076B 回包）才是"这台设备是手台"的唯一证据。
    ordered = sorted(cands, key=lambda c: -c["score"])
    if use_probe:
        for c in ordered:
            if probe(c["device"]):
                return c["device"]
    for c in ordered:
        if c["score"] >= 50:
            return c["device"]
    return ordered[0]["device"] if ordered else None


def main():
    if "--only-device" in sys.argv:
        # 给批处理脚本用：只吐一个设备名（找不到就沉默）。
        # 老脚本按 VID==0x36B7 硬筛端口，CH340 转接线全是别的 VID，
        # 于是"手台插着却报找不到端口"。
        d = pick()
        if d:
            print(d)
        return
    cands = candidates()
    if "--json" in sys.argv:
        chosen = pick()
        for c in cands:
            c["probe"] = probe(c["device"]) if c["device"] != chosen else True
        print(json.dumps({"chosen": chosen, "candidates": cands}, ensure_ascii=False))
        return
    if not cands:
        print("一个串口都没有。电台插上了吗？驱动装了吗？")
        return
    chosen = pick()
    for c in cands:
        mark = "->" if c["device"] == chosen else "  "
        ok = "探针通" if probe(c["device"]) else "探针无回包"
        print(f"{mark} {c['device']:<6} {c['why']:<34} {c['desc']}  [{ok}]")
    if chosen:
        print(f"\n用 {chosen}")
    else:
        print("\n没有哪个口回 0x076B，不敢自动选：")
        print("常见原因：① 电台没开机 / 不在 UART 模式；② 口被 MuMu 抢走且不转发；")
        print("③ 波特率不对（本固件固定 38400）。可在 doppler_tool.py 下拉框里手动指定。")


if __name__ == "__main__":
    main()
