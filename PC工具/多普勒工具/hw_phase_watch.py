# -*- coding: utf-8 -*-
"""真机 phase 电子见证：以 3 Hz 轮询 0x0772，把「页面状态 / ppm / proto」随时间的变化打出来。

为什么需要它：B1 那 17 条里有相当一部分现象是屏上的，而手台没有截屏协议；
但 0x0772 回包里的 phase（0 OFF / 1 READY / 2 RUN / 3 DONE / 4 EMPTY /
5 EXT / 6 预听 / 7 尾音）就是 $? 那一格的口径。人在按，我在记 ——
按下的瞬间到底进没进那一页，不再靠回忆。

只读，不发任何写命令：0x0772 在固件里只读 flash 校准结构体与几个 RAM 变量，
不写 PLL、不写 VFO、不动接管状态、不碰 REG_3B，所以过境中也能跑。

用法：
    python hw_phase_watch.py                 # 一直跑到 Ctrl-C
    python hw_phase_watch.py --secs 120      # 跑 2 分钟
    python hw_phase_watch.py --csv out.csv   # 同时落 CSV（默认自动落盘）
    python hw_phase_watch.py --port COM7
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import radio_proto as rp

PHASE = {0: "OFF", 1: "READY", 2: "RUN", 3: "DONE", 4: "EMPTY",
         5: "EXT(第二套逻辑)", 6: "PRE-LISTEN(预听)", 7: "POST-LISTEN(尾音)"}


def ask(ser, cmd, timeout=0.8):
    ser.reset_input_buffer()
    ser.write(rp.request_bytes(cmd))
    ser.flush()
    buf = bytearray()
    end = time.time() + timeout
    while time.time() < end:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
        replies, rest = rp.parse_replies(buf)
        for r in replies:
            if r is not None and r.cmd == cmd + 1:
                return r
        buf = bytearray(rest)
    return None


def resolve_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        import find_port
        cands = [c for c in find_port.candidates()]
        for c in cands:
            dev = c["device"]
            if "COM" in dev.upper():
                return dev
    except Exception:
        pass
    return "COM7"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--secs", type=float, default=0.0, help="0 = 一直跑到 Ctrl-C")
    ap.add_argument("--csv")
    ap.add_argument("--baud", type=int, default=38400)
    a = ap.parse_args()

    import serial  # 延迟导入，--help 不需要 pyserial

    port = resolve_port(a.port)
    out = a.csv or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "verify",
                                "phase-%s.csv" % time.strftime("%Y%m%d-%H%M%S"))
    out = os.path.normpath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    with serial.Serial(port, a.baud, timeout=0.15) as ser, open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "phase", "phase_name", "ppm", "proto", "clamped", "ver", "state_note"])

        print(f"== phase watch on {port} -> {out} ==", flush=True)
        print("   Ctrl-C 结束。页面变化会打一行，其余静默（心跳 30 s 一记）。", flush=True)

        last = None
        t0 = time.time()
        last_beat = 0.0
        try:
            while True:
                t = time.time() - t0
                if a.secs and t > a.secs:
                    break
                r = ask(ser, rp.CMD_FW_TAG)
                if r is None:
                    note = "no-reply"
                    key = ("no-reply",)
                else:
                    try:
                        info = rp.parse_fw_tag(r)
                    except ValueError as e:
                        note = f"parse:{e}"
                        key = ("bad",)
                    else:
                        key = (info["phase"], info["ppm"], info["proto"], info["clamped"])
                        note = ""
                if key != last:
                    if r is not None and not note:
                        print("  %7.2fs  phase=%d %-18s ppm=%+d proto=%d clamped=%s"
                              % (t, info["phase"], PHASE.get(info["phase"], "?"),
                                 info["ppm"], info["proto"], info["clamped"]), flush=True)
                        w.writerow(["%.2f" % t, info["phase"], PHASE.get(info["phase"], "?"),
                                    "%+d" % info["ppm"], info["proto"], info["clamped"], info["ver"], ""])
                    else:
                        print("  %7.2fs  %s" % (t, note), flush=True)
                        w.writerow(["%.2f" % t, "", note, "", "", "", "", note])
                    fh.flush()
                    last = key
                    last_beat = t
                elif t - last_beat > 30:
                    last_beat = t
                    print("  %7.2fs  (心跳，无变化)" % t, flush=True)
                time.sleep(0.3)
        except KeyboardInterrupt:
            print("\n  收到 Ctrl-C，停。", flush=True)
        finally:
            print(f"  轨迹已写入 {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
