"""
NIC-KSF — Demo
SPECK-128 CTR encryption library

Shows the basic flow: data generator → encrypt → decrypt → write

MIT License
Copyright (c) 2026 NIC — Native Intellect Community

★ Viva La Resistánce ★
"""

import os
import struct
import time
from nic_ksf import ksf_encrypt, ksf_decrypt

# ================================================================
# Configuration
# ================================================================

FIXED_HALF  = bytes([0x4E, 0x49, 0x43, 0x00])   # "NIC\x00"
RANDOM_HALF = os.urandom(4)
MASTER_KEY  = FIXED_HALF + RANDOM_HALF           # 8B master key
KEY         = MASTER_KEY + MASTER_KEY            # 16B key (cold start)

NUM_PACKETS = 8
OUTPUT_FILE = "ksf_demo.bin"

# ================================================================
# Data generator
# ================================================================

def generate(idx: int) -> bytes:
    """Simulated packet: [4B timestamp][2B value A][2B value B][8B data]"""
    ts = int(time.time()) + idx * 1800
    a  = 215 + idx
    b  = 10130 + (idx % 5)
    return struct.pack(">IHH", ts, a, b) + bytes(range(idx, idx + 8))

# ================================================================
# Demo
# ================================================================

def main():
    print("=" * 52)
    print("  NIC-KSF — Demo")
    print("=" * 52)
    print(f"  Key (fixed):  {FIXED_HALF.hex()}")
    print(f"  Key (random): {RANDOM_HALF.hex()}")
    print(f"  Key (full):   {KEY.hex()}")
    print("=" * 52)
    print()

    log    = []
    all_ok = True

    for i in range(NUM_PACKETS):
        pkt = generate(i)
        enc = ksf_encrypt(KEY, pkt)
        dec = ksf_decrypt(KEY, bytes(enc))
        ok  = (bytes(dec) == pkt)
        all_ok = all_ok and ok
        print(f"  [{i}] plain={pkt.hex()}  cipher={bytes(enc).hex()}  {'OK' if ok else 'FAIL'}")
        log.append(bytes(enc))

    with open(OUTPUT_FILE, "wb") as f:
        f.write(b"NICKSF")
        f.write(bytes([1, 0]))
        f.write(KEY)
        f.write(struct.pack(">H", len(log)))
        for pkt in log:
            f.write(struct.pack(">H", len(pkt)))
            f.write(pkt)

    print()
    print(f"  Wrote {len(log)} packets → {OUTPUT_FILE} ({os.path.getsize(OUTPUT_FILE)} B)")
    print()
    print("=" * 52)
    print(f"  {'ALL OK ✓' if all_ok else 'FAIL!'}")
    print("=" * 52)


if __name__ == "__main__":
    main()
