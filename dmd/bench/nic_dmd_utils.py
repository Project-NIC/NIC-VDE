# SPDX-License-Identifier: MIT

"""
NIC DMD — Utils
===============
Helper functions for compression analysis and result reporting.
Import alongside nic_dmd.py.

License: MIT
NIC — Native Intellect Community
https://github.com/Project-NIC
"""

import os
import sys

from collections import Counter

# Allow running the benchmark tooling from the repo root: make the
# Python reference implementation in ../python importable.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from nic_dmd import (
    dmd_compress, dmd_decompress,
    parse_header, DMD_DELTA_NONE, DMD_KEYFRAME_EVERY,
)



def dmd_analyze_packets(packets: list,
                        timestamps: list = None,
                        source_name: str = "DMD") -> list:
    """
    Compress packets and return per-packet results.

    Returns a list of dicts with keys:
      index, timestamp, source, sample_num, method,
      delta_type, use_flag, use_ans,
      original_len, compressed_len, saving_pct,
      zero_count, roundtrip_ok
    """
    if not packets:
        return []

    pkt_len    = len(packets[0])
    previous   = bytes(pkt_len)
    sample_num = 0
    results    = []

    for i, packet in enumerate(packets):
        compressed   = dmd_compress(packet, previous, sample_num)
        decompressed = dmd_decompress(compressed, previous)
        ok           = (decompressed == packet)

        h        = parse_header(compressed[0])
        orig_len = len(packet)
        comp_len = len(compressed)
        # Saving relative to "bytes on the wire": baseline = orig_len + 1 B (the
        # hypothetical header even for an uncompressed RAW transmission), so the
        # comparison is fair against comp_len which always includes the 1 B DMD header.
        saving   = round((1 - comp_len / (orig_len + 1)) * 100, 1)

        dt = h['delta_type']
        if h['use_huf'] and dt != DMD_DELTA_NONE:
            method = f"DELTA{dt}+ZZ+HUF"
        elif h['use_ans'] and dt != DMD_DELTA_NONE:
            method = f"DELTA{dt}+ZZ+ANS"
        elif h['use_flag'] and dt != DMD_DELTA_NONE:
            method = f"DELTA{dt}+ZZ+FLAG"
        elif h['use_huf']:
            method = "HUF"
        elif h['use_ans']:
            method = "ANS"
        elif h['use_flag']:
            method = "FLAG"
        elif dt != DMD_DELTA_NONE:
            method = f"DELTA{dt}+ZZ+RAW"
        else:
            method = "RAW"

        results.append({
            'index':          i,
            'timestamp':      timestamps[i] if timestamps else i,
            'source':         source_name,
            'sample_num':     sample_num,
            'method':         method,
            'delta_type':     dt,
            'use_huf':        h['use_huf'],
            'use_flag':       h['use_flag'],
            'use_ans':        h['use_ans'],
            'original_len':   orig_len,
            'compressed_len': comp_len,
            'saving_pct':     saving,
            'zero_count':     packet.count(0),
            'roundtrip_ok':   ok,
        })

        previous   = packet
        sample_num = (sample_num + 1) % DMD_KEYFRAME_EVERY

    return results


def dmd_print_summary(results: list) -> None:
    """Print a summary table of compression results."""
    if not results:
        return

    src = results[0]['source']
    w   = results[0]['original_len']

    print(f"\n{'='*105}")
    print(f"[DMD] Source: {src} | Packets: {len(results)} | Width: {w}B")
    print(f"{'='*105}")
    print(f"{'#':>6} | {'Time':<22} | {'Smp':>3} | {'Method':<30} | "
          f"{'Orig':>5} | {'Comp':>5} | {'Sav%':>6} | {'Zeros':>5} | OK")
    print(f"{'-'*105}")

    for r in results:
        ts = str(r['timestamp'])[:22]
        print(f"{r['index']+1:>6} | {ts:<22} | {r['sample_num']:>3} | "
              f"{r['method']:<30} | {r['original_len']:>5} | "
              f"{r['compressed_len']:>5} | {r['saving_pct']:>5.1f}% | "
              f"{r['zero_count']:>5} | {'✓' if r['roundtrip_ok'] else '✗'}")

    total_orig = sum(r['original_len'] + 1 for r in results)
    total_comp = sum(r['compressed_len'] for r in results)
    overall    = round((1 - total_comp / total_orig) * 100, 1)
    errors     = sum(1 for r in results if not r['roundtrip_ok'])
    methods    = Counter(r['method'] for r in results)

    print(f"{'='*105}")
    print(f"Total: {total_orig}B → {total_comp}B | "
          f"Saving: {total_orig - total_comp}B ({overall}%) | "
          f"Errors: {errors}")
    print(f"\nMethod usage:")
    for method, count in methods.most_common():
        pct = round(count / len(results) * 100, 1)
        bar = '█' * int(pct / 2)
        print(f"  {pct:>5.1f}% {bar:<50} {method}")
    print(f"{'='*105}\n")
