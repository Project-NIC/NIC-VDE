"""
NIC-KSF — Test suite
SPECK-128 CTR encryption library

MIT License
Copyright (c) 2026 NIC — Native Intellect Community

★ Viva La Resistánce ★
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from nic_ksf import ksf_encrypt, ksf_decrypt

KEY = bytes(range(1, 17))  # 0x01 .. 0x10


def test(name: str, ok: bool):
    print(f"  {name}: {'OK' if ok else 'FAIL'}")
    return ok


def main():
    print("=== NIC-KSF test ===\n")
    passed = True

    # Test 1: 16B symmetry
    data = b"Hello, KSF!     "
    ct   = ksf_encrypt(KEY, data)
    pt   = ksf_decrypt(KEY, ct)
    passed &= test("16B symmetry", pt == bytearray(data))

    # Test 2: various lengths 1-255B
    ok = True
    for n in range(1, 256):
        d  = bytes(i % 256 for i in range(n))
        ct = ksf_encrypt(KEY, d)
        pt = ksf_decrypt(KEY, ct)
        if pt != bytearray(d):
            ok = False
            print(f"    FAIL at {n}B")
            break
    passed &= test("Lengths 1-255B", ok)

    # Test 3: different keys -> different ciphertext
    key2 = bytes(range(16, 0, -1))
    data = b"Hello, KSF!     "
    c1   = ksf_encrypt(KEY,  data)
    c2   = ksf_encrypt(key2, data)
    passed &= test("Different keys -> different ciphertext", c1 != c2)

    # Test 4: cross-check against the C implementation
    # Expected output verified against nic_ksf_32.c and nic_ksf_64.c
    ct = ksf_encrypt(KEY, b"Hello, KSF!     ")
    passed &= test("Cross-check C<->Python", ct.hex() == "0a1d4bb0b0775eb9ad647df6cd1cc8ae")

    print(f"\n{'All OK ✓' if passed else 'FAIL!'}")


if __name__ == "__main__":
    main()
