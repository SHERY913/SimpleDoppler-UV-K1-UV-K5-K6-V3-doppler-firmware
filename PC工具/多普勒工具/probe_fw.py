"""直接问电台：固件版本 / 接管状态 / 第二套逻辑有没有进去。

不经过 App、不经过桥，直接开 COM7 发帧，把回包原样打出来。
用途：当"App 显示连上了但电台没反应"时，分清是固件没刷、还是协议没被认。

    python probe_fw.py            # 只读状态
    python probe_fw.py --fw-tag   # 只读：固件标识 + 生效 ppm（0x0772/0x0773）
    python probe_fw.py --take     # 顺带发 0x0768=1 和 0x076C，看 phase 变不变
"""

import sys
import time

import serial

import radio_proto          # 命令 ID 与 0x0773 的解析只在这里写一份

MASK = [0x16, 0x6C, 0x14, 0xE6, 0x2E, 0x91, 0x0D, 0x40,
        0x21, 0x35, 0xD5, 0x40, 0x13, 0x03, 0xE9, 0x80]

PORT = None          # None = 自动挑；--port COMx 可指定
BAUD = 38400

PHASE = {0: "OFF", 1: "READY", 2: "RUN", 3: "DONE", 4: "EMPTY", 5: "EXT(第二套逻辑)",
         6: "PRE-LISTEN(预听)", 7: "POST-LISTEN(尾音)"}

# 表区起点（和 doppler_core.TABLE_START 一致）。本脚本故意不 import
# doppler_core，避免把 numpy/sgp4 拖进来，所以这里单独写一份。
TABLE_START = 0x00162000   # = doppler_core.DPH_BASE


def xor(data: bytes) -> bytes:
    return bytes(b ^ MASK[i % 16] for i, b in enumerate(data))


def crc16(data: bytes) -> int:
    """CCITT，初值 0 —— 和 RadioLink.kt 里一模一样。"""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def frame(cmd: int, pl: bytes = b"") -> bytes:
    """AB CD | n(2, 只算 msg) | msg+crc 逐字节 XOR MASK | DC BA"""
    msg = bytes([cmd & 0xFF, cmd >> 8, len(pl) & 0xFF, (len(pl) >> 8) & 0xFF]) + pl
    if len(msg) % 2:
        msg += bytes([0])
    body = msg + crc16(msg).to_bytes(2, "little")
    body = bytes(b ^ MASK[i % 16] for i, b in enumerate(body))
    return bytes([0xAB, 0xCD]) + len(msg).to_bytes(2, "little") + body + bytes([0xDC, 0xBA])

def read_frame(ser: serial.Serial, deadline: float):
    buf = b""
    while time.time() < deadline:
        chunk = ser.read(1)
        if not chunk:
            continue
        buf += chunk
        if len(buf) >= 4 and buf[0] == 0xAB and buf[1] == 0xCD:
            n = int.from_bytes(buf[2:4], "little")
            while len(buf) < 8 + n and time.time() < deadline:
                buf += ser.read(8 + n - len(buf))
            body = xor(buf[4:4 + n])
            cmd = body[0] | (body[1] << 8)
            plen = body[2] | (body[3] << 8)
            return cmd, body[4:4 + plen]
        buf = buf[-3:]
    return None, None


def ask(ser: serial.Serial, cmd: int, payload: bytes = b"", timeout: float = 1.5):
    ser.reset_input_buffer()
    ser.write(frame(cmd, payload))
    ser.flush()
    return read_frame(ser, time.time() + timeout)


def resolve_port():
    global PORT
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            PORT = sys.argv[i + 1]
            return PORT
    import find_port
    PORT = find_port.pick()
    if PORT is None:
        print("没有可用的真串口：设备被 MuMu 抢走了，宿主上的 COM 口只是它的虚拟壳。")
        print("处理：MuMu 里断开这台电台，或先关掉模拟器，然后再跑一次。")
        for c in find_port.candidates():
            print(f"   现有端口 {c['device']}  {c['why']}  {c['desc']}")
        raise SystemExit(2)
    print(f"使用端口 {PORT}")
    return PORT


def show_fw_tag(ser) -> bool:
    """只读查询 0x0772：固件是谁、版本多少、ppm 实际生效到多少。

    故意只发这一条命令：0x0772 在固件里只读 flash 里的校准结构体和几个
    RAM 变量，不写 PLL、不写 VFO、不动接管状态、不碰 REG_3B。所以它能在
    **任何时刻**跑 —— 包括正接着 App、过境进行中、甚至发射中。
    """
    print("== 0x0772 固件标识（只读） ==")
    c, p = ask(ser, radio_proto.CMD_FW_TAG)
    if c != radio_proto.FW_TAG_REPLY:
        if c is None:
            print("   没有回包 -> 这版固件不认 0x0772（0x076A 还能回、装的是旧固件）。")
        else:
            print(f"   回包 ID 0x{c:04X}，不是 0x0773（回包变成别的命令了，别猜，先查固件）。")
        return False
    try:
        info = radio_proto.parse_fw_tag(p)
    except ValueError as e:
        print(f"   {e}")
        return False

    print(f"   ver      = {info['ver'] or '(空)'}")
    print(f"   ppm      = {info['ppm']:+d}   （固件钳位后真正写进 REG_3B 的值）")
    print(f"   ppm_max  = {info['ppm_max']}   （存储侧与施加侧共用的上限）")
    print(f"   proto    = {info['proto']}   （7 = 0x0775 回亚音自己的 State；"
          f"6 = 带第二套逻辑页的老口径；App 按 ≥6 判有第二套逻辑）")
    print(f"   phase    = {info['phase']} ({PHASE.get(info['phase'], '?')})")
    print(f"   clamped  = {info['clamped']}")
    if info["clamped"]:
        print(f"   注意 ppm 已经顶在 ±{info['ppm_max']}：要么用户确实填了这个值，"
              f"要么填了更大的被钳到这里 —— 0x0772 只回生效值，分不出这两种，"
              f"要原始输入得看 App/PC 那边存的值。")
    return True


def main():
    take = "--take" in sys.argv
    tag_only = "--fw-tag" in sys.argv
    resolve_port()
    ser = None
    for _ in range(10):          # 端口偶尔被上一次的进程抓着，等它松手
        try:
            ser = serial.Serial(PORT, BAUD, timeout=0.05)
            break
        except Exception as e:
            print(f"   {PORT} 打不开（{type(e).__name__}），1 秒后重试……")
            time.sleep(1.0)
    if ser is None:
        print("端口一直被占用。按顺序排除：")
        print("  1) 关掉还在跑的 serial_bridge.py / 上一个 python 窗口")
        print("  2) MuMu 是否把设备抢走（关掉模拟器）")
        print("  3) 拔掉 USB 线，等 3 秒再插回（电台保持开机），重新双击本脚本")
        raise SystemExit(3)
    time.sleep(0.2)

    if tag_only:
        # 只读档：问完就走，后面 0x0768/0x076C 一概不发，免得"只想看一眼版本"
        # 的人顺手把电台推进接管态。
        show_fw_tag(ser)
        ser.close()
        return

    print("== 1) 0x076A 固件自检 ==")
    c, p = ask(ser, 0x076A)
    if c is None:
        # 分不清是"固件旧"还是"电台压根没在听"，用一条**只读**命令当对照组。
        # 以前拿 0x0766 发 days=0/secs=0 当探针：只要固件认这条命令，电台时钟就
        # 被写到 2000-01-01，过境表当场全部对不上（报告 第六部分·回归项 3）。0x0764 只读表头
        # 前 16 字节，回不回包都说明不了"我动了它"。
        print("   0x076A 没回包，用 0x0764（只读表头）做对照……")
        try:
            c2, _ = ask(ser, 0x0764, (TABLE_START).to_bytes(4, "little")
                        + (16).to_bytes(2, "little"))
        except Exception:
            c2 = None
        if c2 is None:
            print("   [结论] 连只读的表头命令都不回 -> 电台没在听。")
            print("          查：电台是否开机、是否停在刷机/DFU 界面、USB 线是否松、")
            print("              是否被模拟器独占（先关 MuMu 或拔插一次）。")
        else:
            print(f"   0x0764 有回包 0x{c2:04X} -> 电台活着，但机内固件不认 0x076A。")
            print("          即：这版固件不是带接管的那版，需要重新刷。")
        return
    print(f"   回包 0x{c:04X} 载荷 {p.hex()}")
    if c == 0x076B and len(p) >= 3:
        proto, state, phase = p[0], p[1], p[2]
        print(f"   proto={proto}  state=0x{state:02X}  phase={phase} ({PHASE.get(phase, '?')})")
        print(f"   接管={'是' if state & 1 else '否'}  过境中={'是' if state & 2 else '否'}"
              f"  表={'有' if state & 4 else '无'}  武装={'是' if state & 8 else '否'}"
              f"  新页开关={'开' if state & 0x10 else '关'}")

    # 顺手问一句版本号（0x0772 也是只读的）。老固件不认这条命令时只多等
    # 一次超时，不会动任何寄存器。
    show_fw_tag(ser)

    if not take:
        return

    if "--ext" in sys.argv:
        print("== 1.5) 0x076E=1 打开第二套逻辑页 ==")
        c, p = ask(ser, 0x076E, b"")
        print(f"   回包 {None if c is None else hex(c)} 载荷 {None if p is None else p.hex()}")

    print("== 2) 0x0768=1 接管 ==")
    c, p = ask(ser, 0x0768, b"\x01")
    print(f"   回包 {None if c is None else hex(c)} 载荷 {None if p is None else p.hex()}")
    if c == 0x0769 and p:
        print(f"   状态 0x{p[0]:02X} 接管={'是' if p[0] & 1 else '否'}")

    print("== 3) 0x076C 推一帧数据 ==")
    payload = (437_800_000 // 10).to_bytes(4, "little")      # rx10
    payload += (145_990_000 // 10).to_bytes(4, "little")     # tx10
    payload += (-1200).to_bytes(2, "little", signed=True)    # dopp
    payload += (9800).to_bytes(2, "little")                  # dist
    payload += (300).to_bytes(2, "little", signed=True)      # remain
    payload += (250).to_bytes(2, "little", signed=True)      # el 25.0
    payload += (-45).to_bytes(2, "little", signed=True)      # rate -0.45
    payload += bytes([42, 0x03, 3]) + b"TST"
    c, p = ask(ser, 0x076C, payload)
    print(f"   回包 {None if c is None else hex(c)} 载荷 {None if p is None else p.hex()}")
    if c == 0x076D and p:
        print(f"   状态 0x{p[0]:02X} 接管={'是' if p[0] & 1 else '否'}")

    print("== 4) 再看 0x076A：phase 应该变成 5 (EXT) ==")
    c, p = ask(ser, 0x076A)
    if c == 0x076B and len(p) >= 3:
        print(f"   proto={p[0]} state=0x{p[1]:02X} phase={p[2]} ({PHASE.get(p[2], '?')})")

    print("== 5) 交还 0x0768=0 ==")
    ask(ser, 0x0768, b"\x00")
    ser.close()


if __name__ == "__main__":
    main()
