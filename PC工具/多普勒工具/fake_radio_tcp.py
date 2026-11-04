"""假电台：在 TCP 7878 上按真固件的帧格式回包，用来把"App ↔ 桥"和"桥 ↔ 串口"分开。

    python fake_radio_tcp.py [--listen 7878]

协议照 radio_proto：请求 AB CD len(2) | 掩码(msg+CRC) | DC BA；
回包同构，ID = 请求 ID + 1。0x076A→0x076B(3 B)、0x076E→0x076F(1)、
0x0768→0x0769(1)、0x076C→0x076D(1)、0x0770→0x0771(4)、0x0774→0x0775(3)。
"""
import argparse
import socket
import sys
import threading
import time

sys.path.insert(0, ".")
import radio_proto as rp

LOG = False


def handle(conn, addr):
    print("client", addr, flush=True)
    buf = bytearray()
    while True:
        try:
            d = conn.recv(4096)
        except OSError:
            break
        if not d:
            break
        buf += d
        while True:
            if len(buf) < 4:
                break
            if buf[0] != 0xAB or buf[1] != 0xCD:
                del buf[0]
                continue
            n = buf[2] | (buf[3] << 8)
            total = 4 + n + 2 + 2
            if len(buf) < total:
                break
            frame = bytes(buf[:total])
            del buf[:total]
            body = rp.mask_xor(frame[4:4 + n + 2])
            cmd = body[0] | (body[1] << 8)
            plen = body[2] | (body[3] << 8)
            payload = body[4:4 + plen]
            print("  cmd 0x%04X payload=%s" % (cmd, payload.hex()), flush=True)
            if cmd == 0x076A:
                out = bytes([0x6B, 0x07, 3, 0, 7, 0, 0])
            elif cmd == 0x076E:
                out = bytes([0x6F, 0x07, 1, 0, 0x30 if payload and payload[0] else 0x10])
            elif cmd == 0x0768:
                out = bytes([0x69, 0x07, 1, 0, 0x31 if payload and payload[0] else 0x10])
            elif cmd == 0x076C:
                out = bytes([0x6D, 0x07, 1, 0, 0x33])
            elif cmd == 0x0770:
                out = bytes([0x71, 0x07, 4, 0]) + (116_999_000).to_bytes(4, "little")
            elif cmd == 0x0774:
                t = int.from_bytes(payload[:2], "little") if len(payload) >= 2 else 0
                out = bytes([0x75, 0x07, 3, 0]) + t.to_bytes(2, "little") + bytes([0x03])
            else:
                out = bytes([(cmd + 1) & 0xFF, (cmd + 1) >> 8, 0, 0])
            conn.sendall(rp.encode_reply(out))
    print("client gone", addr, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=7878)
    a = ap.parse_args()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    srv.bind(("127.0.0.1", a.listen))
    srv.listen(4)
    print("fake radio on %d" % a.listen, flush=True)
    while True:
        c, addr = srv.accept()
        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=handle, args=(c, addr), daemon=True).start()


if __name__ == "__main__":
    main()
