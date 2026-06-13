"""
Compression method comparison for embedded
===============================================
Fair comparison — every method returns a complete packet ready for transmission.
Same as the NIC protocol — header + everything the decoder needs.

Packet format:
  NIC:        [1B header][payload — everything the decoder needs]
  RAW:        [1B type=0x00][data]
  Heatshrink: [1B type][1B original length][compressed data]
  Huffman:    [1B type][1B original length][1B valid bits in last byte][bits]

Fallback: if compressed packet >= RAW → send RAW

Dependencies: pip install requests heatshrink2
"""

import os
import struct, math, heapq, random, sys
import requests
import heatshrink2
from collections import defaultdict

# Allow running from the repo root: make the Python reference
# implementation in ../python importable.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

from nic_dmd import (
    _delta_encode_zz,
    _zigzag_enc,
    DMD_DELTA_FULL,
    DmdEncoder,
    DmdDecoder,
)
from nic_dmd_utils import dmd_analyze_packets as analyze_packets, dmd_print_summary

    
# ---------------------------------------------------------------------------
# Data conversion
# ---------------------------------------------------------------------------

def pack_16b(row):
    return struct.pack('>8h', *[max(-32768,min(32767,v)) for v in row])

def pack_32b(row):
    return struct.pack('>16h', *[max(-32768,min(32767,v)) for v in row])

def fv(v, scale, default=0.0):
    return int(round((v if v is not None else default) * scale))

def fetch_open_meteo(lat, lon, start, end):
    import time
    from datetime import datetime, timedelta
    variables = [
        "temperature_2m","relative_humidity_2m","surface_pressure",
        "wind_speed_10m","wind_direction_10m","precipitation",
        "dew_point_2m","apparent_temperature","cloud_cover",
        "shortwave_radiation","uv_index","visibility",
        "soil_temperature_0cm","soil_temperature_6cm",
        "soil_temperature_18cm","soil_temperature_54cm",
    ]
    url = "https://archive-api.open-meteo.com/v1/archive"
    session = requests.Session()
    session.headers.update({'User-Agent': 'DMD-Benchmark/1.0'})
    dt_s = datetime.strptime(start, "%Y-%m-%d")
    dt_e = datetime.strptime(end,   "%Y-%m-%d")
    chunks, cur = [], dt_s
    while cur < dt_e:
        nxt = min(cur + timedelta(days=60), dt_e)
        chunks.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        cur = nxt + timedelta(days=1)
    all_data = {}
    for i, (s, e) in enumerate(chunks):
        params = {"latitude":lat,"longitude":lon,"start_date":s,"end_date":e,
                  "hourly":",".join(variables),"timezone":"UTC","wind_speed_unit":"ms"}
        for attempt in range(3):
            try:
                r = session.get(url, params=params, timeout=30)
                r.raise_for_status()
                chunk = r.json()["hourly"]
                for key, vals in chunk.items():
                    if key not in all_data: all_data[key] = []
                    all_data[key].extend(vals)
                time.sleep(1)
                break
            except Exception as ex:
                if attempt < 2: time.sleep(5)
                else: raise
    return all_data

def to_packets(h, bits=16, limit=2000):
    n = min(limit, len(h['time']))
    def g(key, d=0.0, i=0):
        v = h.get(key,[d]*n)[i]
        return v if v is not None else d

    packets = []
    for i in range(n):
        if bits == 16:
            row = [fv(g('temperature_2m',0,i),100),
                   fv(g('relative_humidity_2m',0,i),100),
                   fv(g('surface_pressure',1013,i)-900,10),
                   fv(g('wind_speed_10m',0,i),100),
                   fv(g('precipitation',0,i),100),
                   fv(g('soil_temperature_0cm',0,i),100),
                   fv(g('soil_temperature_6cm',0,i),100),
                   fv(g('dew_point_2m',0,i),100)]
            packets.append(pack_16b(row))
        else:
            row = [fv(g('temperature_2m',0,i),100),
                   fv(g('relative_humidity_2m',0,i),100),
                   fv(g('surface_pressure',1013,i)-900,10),
                   fv(g('wind_speed_10m',0,i),100),
                   fv(g('wind_direction_10m',0,i),10),
                   fv(g('precipitation',0,i),100),
                   fv(g('dew_point_2m',0,i),100),
                   fv(g('apparent_temperature',0,i),100),
                   fv(g('cloud_cover',0,i),100),
                   fv(g('shortwave_radiation',0,i),1),
                   fv(g('uv_index',0,i),100),
                   fv(g('visibility',10000,i)/100,10),
                   fv(g('soil_temperature_0cm',0,i),100),
                   fv(g('soil_temperature_6cm',0,i),100),
                   fv(g('soil_temperature_18cm',0,i),100),
                   fv(g('soil_temperature_54cm',0,i),100)]
            packets.append(pack_32b(row))
    return packets

# ---------------------------------------------------------------------------
# Huffman (adaptive, trained on a static baseline dataset)
# ---------------------------------------------------------------------------

def build_huffman(packets_sample):
    freq = defaultdict(int)
    prev = packets_sample[0]
    for pkt in packets_sample[1:]:
        for b in _zigzag_enc(_delta_encode_zz(pkt, prev, DMD_DELTA_FULL)):
            freq[b] += 1
        prev = pkt
    heap = [[f,[s,""]] for s,f in freq.items()]
    heapq.heapify(heap)
    if len(heap)==1: heapq.heappush(heap,[1,[256,""]])
    while len(heap)>1:
        lo,hi = heapq.heappop(heap), heapq.heappop(heap)
        for p in lo[1:]: p[1]='0'+p[1]
        for p in hi[1:]: p[1]='1'+p[1]
        heapq.heappush(heap,[lo[0]+hi[0]]+lo[1:]+hi[1:])
    return {s:c for s,c in heap[0][1:]}

# Static table trained on neutral data
random.seed(999)
_sp = []
_t,_h,_p,_w = -500,7000,3850,800
for _ in range(500):
    _t+=random.randint(-50,50); _h+=random.randint(-200,200)
    _p+=random.randint(-20,20); _w+=random.randint(-300,300)
    _sp.append(pack_16b([_t,max(0,min(10000,_h)),max(0,_p),
                          max(0,_w),0,_t-500,_t-300,_t-400]))
STATIC_CODES = build_huffman(_sp)
STATIC_TABLE_ROM_BYTES = sum(math.ceil(len(c)/8)+1 for c in STATIC_CODES.values())


def huffman_encode_packet(pkt, prev, codes, typ_byte):
    """
    Encode one packet with Huffman into the transmission format.
    Format: [1B type][1B original length][1B valid bits][bits in bytes]
    Fallback: [1B type=0xFF][data] if larger than RAW
    """
