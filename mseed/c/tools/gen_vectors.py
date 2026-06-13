#!/usr/bin/env python3
"""Regenerate c/test/vectors.h from the Python reference. Run from the nic-mseed
package root:  python3 c/tools/gen_vectors.py"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/../.."))
from nic_mseed.steim import encode_record, STEIM1, STEIM2
from nic_mseed.mseed import write_stream
A = [0,3,-3,7,-8,15,-16,50,-64,200,-256,1000,-2000,32000,-40000,500000,
     -1000000,5000000,-9000000,0,0,0,1,2,3,4,5,6,7,8,9,10,9,8,7,6,5,4,3,2,1,
     0,-1,-2,-3,-4,-5,100,100,100,100]
B = [((i*i*7 + i*13) % 2000) - 1000 for i in range(300)]
recS2,_ = encode_record(A, STEIM2, 7, 0); recS1,_ = encode_record(A, STEIM1, 7, 0)
mseed = write_stream(B, start_unix=1700000000, start_frac=0.25, sample_rate_hz=100.0,
                     network="NQ", station="ST01", location="", channel="HHZ",
                     version=STEIM2, reclen=512)
def carr(n,b):
    s=f"static const unsigned char {n}[{len(b)}] = {{\n"
    for i in range(0,len(b),16): s+="  "+",".join(map(str,b[i:i+16]))+",\n"
    return s+"};\n"
def iarr(n,a):
    s=f"static const int {n}[{len(a)}] = {{\n"
    for i in range(0,len(a),12): s+="  "+",".join(map(str,a[i:i+12]))+",\n"
    return s+"};\n"
with open(os.path.dirname(__file__)+"/../test/vectors.h","w") as f:
    f.write("/* Auto-generated from the Python reference (nic_mseed) — do not edit. */\n")
    f.write("#ifndef NIC_MSEED_TEST_VECTORS_H\n#define NIC_MSEED_TEST_VECTORS_H\n\n")
    f.write(iarr("VEC_A",A)); f.write(iarr("VEC_B",B))
    f.write(carr("REC_S2",recS2)); f.write(carr("REC_S1",recS1)); f.write(carr("MSEED_S2",mseed))
    f.write("\n#endif\n")
print("regenerated vectors.h")
