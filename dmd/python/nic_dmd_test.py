# SPDX-License-Identifier: MIT

"""
NIC DMD — Python tests
Run via: make python  or  python3 nic_dmd_test.py
"""

import random, struct, sys
sys.path.insert(0, '.')
from nic_dmd import DmdEncoder, DmdDecoder, dmd_compress, dmd_decompress

errors = 0
total  = 0

def check(name, ok):
    global errors, total
    total += 1
    if not ok:
        errors += 1
        print(f"  FAIL: {name}")

print("\n=== NIC DMD — Python tests ===\n")

# Test 1: round-trip various lengths
print("Test 1: round-trip (random data)")
random.seed(42)
for pkt_len in [8, 16, 32, 64, 128, 255]:
    enc = DmdEncoder(pkt_len)
    dec = DmdDecoder(pkt_len)
    e   = 0
    for i in range(500):
        data   = bytes(random.randint(0, 255) for _ in range(pkt_len))
        comp   = enc.compress(data)
        decomp = dec.decompress(comp)
        if decomp != data:
            e += 1
    print(f"  pkt_len={pkt_len:3d}: 500 packets: {'OK' if e==0 else f'ERRORS={e}'}")
    check(f"round-trip pkt_len={pkt_len}", e == 0)

# Test 2: all-zeros
print("\nTest 2: all-zeros")
for pkt_len in [16, 64, 128]:
    enc = DmdEncoder(pkt_len)
    dec = DmdDecoder(pkt_len)
    e   = 0
    for _ in range(8):
        data   = bytes(pkt_len)
        comp   = enc.compress(data)
        decomp = dec.decompress(comp)
        if decomp != data: e += 1
    print(f"  pkt_len={pkt_len:3d}: all-zeros: {'OK' if e==0 else f'ERRORS={e}'}")
    check(f"all-zeros pkt_len={pkt_len}", e == 0)

# Test 3: keyframe
print("\nTest 3: keyframe (sample=0)")
random.seed(123)
for pkt_len in [16, 64, 255]:
    data     = bytes(random.randint(0, 255) for _ in range(pkt_len))
    previous = bytes(pkt_len)
    comp     = dmd_compress(data, previous, 0)
    decomp   = dmd_decompress(comp, previous)
    ok       = decomp == data
    print(f"  pkt_len={pkt_len:3d}: {'OK' if ok else 'FAIL'} (comp {len(comp)}B)")
    check(f"keyframe pkt_len={pkt_len}", ok)

# Test 4: meteo data
print("\nTest 4: meteo data (gradual changes)")
enc  = DmdEncoder(16)
dec  = DmdDecoder(16)
t    = -800
e    = 0
s_o  = 0; s_c = 0
random.seed(42)
for _ in range(100):
    t += random.randint(-20, 20)
    data   = struct.pack('>8h', t, 8500, 385, 1230, 0, -900, -700, -1000)
    comp   = enc.compress(data)
    decomp = dec.decompress(comp)
    if decomp != data: e += 1
    s_o += 17; s_c += len(comp)
saving = (1 - s_c / s_o) * 100
print(f"  100 packets: {'OK' if e==0 else f'ERRORS={e}'} (saving {saving:.1f}%)")
check("meteo", e == 0)

# Test 5: Reserved protocol version
print("\nTest 5: Reserved protocol version (sample_num=7)")
total += 1
reserved_header = bytes([7])  # Sets sample_num = 7
dummy_payload = bytes(16)
try:
    dmd_decompress(reserved_header + dummy_payload, dummy_payload)
    print("  FAIL: Decoder incorrectly accepted a packet with reserved protocol version!")
    errors += 1
except ValueError:
    print("  OK (ValueError correctly raised for unsupported version)")

# Test 6: method coverage — exercise the RAW / ANS / HUF / FLAG / FLAG+HUF paths
print("\nTest 6: method coverage (every encode path round-trips)")
from nic_dmd import parse_header


def _method_name(h):
    p = parse_header(h)
    if p['use_flag'] and p['use_huf']:
        return 'FLAG+HUF'
    if p['use_huf']:
        return 'HUF'
    if p['use_ans']:
        return 'ANS'
    if p['use_flag']:
        return 'FLAG'
    return 'RAW'


random.seed(7)
corpus = []
# incompressible random -> RAW / ANS
for _ in range(40):
    corpus.append((bytes(random.randint(0, 255) for _ in range(32)), bytes(32)))
# few changed bytes vs previous -> FLAG
for _ in range(40):
    prev = bytes(random.randint(0, 255) for _ in range(32))
    data = bytearray(prev)
    for _ in range(random.randint(0, 3)):
        data[random.randrange(32)] ^= random.randint(1, 255)
    corpus.append((bytes(data), prev))
# low-nibble structured data -> HUF
for _ in range(40):
    corpus.append((bytes(random.randint(0, 15) for _ in range(32)), bytes(32)))
# constant / zeros -> FLAG
for n in (16, 64, 128):
    corpus.append((bytes(n), bytes(n)))

mc_errors = 0
seen = set()
for data, prev in corpus:
    comp = dmd_compress(data, prev, 1)        # sample_num=1 (delta record)
    if dmd_decompress(comp, prev) != data:
        mc_errors += 1
    seen.add(_method_name(comp[0]))
print(f"  {len(corpus)} packets round-trip: "
      f"{'OK' if mc_errors == 0 else f'ERRORS={mc_errors}'}; methods seen: {sorted(seen)}")
check("method coverage round-trip", mc_errors == 0)
check("method coverage exercises >=3 methods", len(seen) >= 3)

# Test 7: C <-> Python byte-for-byte parity (ctypes; skips if it cannot build)
print("\nTest 7: C<->Python parity (ctypes)")


def _cross_check():
    import os
    import ctypes
    import subprocess
    import tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    cdir = os.path.join(here, '..', 'c')
    csrc = os.path.join(cdir, 'nic_dmd.c')
    if not os.path.isfile(csrc):
        return None, "c/nic_dmd.c not found"
    so = os.path.join(tempfile.gettempdir(), 'libnicdmd_xcheck.so')
    try:
        subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-std=c99',
                        '-I', cdir, csrc, '-o', so],
                       check=True, capture_output=True)
        lib = ctypes.CDLL(so)
    except Exception as ex:                       # no compiler / build error
        return None, f"build skipped ({ex.__class__.__name__})"

    BUF = 255

    class Enc(ctypes.Structure):
        _fields_ = [("pkt_len", ctypes.c_uint8), ("sample_num", ctypes.c_uint8),
                    ("previous", ctypes.c_uint8 * BUF)]

    class Dec(ctypes.Structure):
        _fields_ = [("pkt_len", ctypes.c_uint8), ("previous", ctypes.c_uint8 * BUF)]

    lib.dmd_compress.restype = ctypes.c_uint16
    lib.dmd_decompress.restype = ctypes.c_int

    pkt_len = 16
    enc, dec = Enc(), Dec()
    lib.dmd_encoder_init(ctypes.byref(enc), pkt_len)
    lib.dmd_decoder_init(ctypes.byref(dec), pkt_len)
    py_enc, py_dec = DmdEncoder(pkt_len), DmdDecoder(pkt_len)

    out = (ctypes.c_uint8 * 256)()
    random.seed(99)
    t = 0
    mism = 0
    for _ in range(200):
        t += random.randint(-15, 15)
        data = struct.pack('>8h', t, 8500, 385, 1230, 0, -900, -700, -1000)
        cur = (ctypes.c_uint8 * pkt_len).from_buffer_copy(data)
        clen = lib.dmd_compress(ctypes.byref(enc), cur, out)
        c_comp = bytes(out[:clen])
        py_comp = py_enc.compress(data)
        if c_comp != py_comp:                     # C and Python must agree byte-for-byte
            mism += 1
        if py_dec.decompress(c_comp) != data:     # Python decodes C output
            mism += 1
    return mism, "200 packets compared"


_res, _msg = _cross_check()
if _res is None:
    print(f"  SKIP ({_msg})")
else:
    print(f"  {_msg}: {'OK (byte-identical)' if _res == 0 else f'MISMATCH={_res}'}")
    check("C<->Python parity", _res == 0)

print(f"\n{'='*50}")
print(f"TOTAL: {total} tests, {errors} failures")
print(f"RESULT: {'✓ ALL OK' if errors == 0 else '✗ FAILURES!'}")
print(f"{'='*50}\n")

sys.exit(0 if errors == 0 else 1)
