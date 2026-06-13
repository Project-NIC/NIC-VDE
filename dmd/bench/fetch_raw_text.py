"""
NIC DMD — Raw Text Benchmark
=============================
Downloads data exactly as source agencies deliver it and compresses it
without any modifications — raw JSON/CSV text as bytes.

One time record = one packet. Zero-padded to a fixed length.
Packet length is determined automatically from the first record.

Dependencies: pip install requests
"""

import os, sys, math, time, csv, zipfile, io, json, logging
import requests
from nic_dmd_utils import dmd_analyze_packets as analyze_packets, dmd_print_summary as print_summary
# Import shared fetch helpers
from nic_dmd_fetch import get_session

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SESSION = get_session()
OUTPUT_DIR = "real_data_raw"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def save_report(results, filename):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        old = sys.stdout
        sys.stdout = f
        try: print_summary(results)
        finally: sys.stdout = old
    print(f"  Report: {path}")

def to_packet(text: str, pkt_len: int) -> bytes:
    """Convert text to bytes, truncate or zero-pad to pkt_len."""
    raw = text.encode('utf-8')
    if len(raw) >= pkt_len:
        return raw[:pkt_len]
    return raw + bytes(pkt_len - len(raw))

def detect_pkt_len(samples: list[str]) -> int:
    """Determine packet length from the first few samples — round up to a multiple of 8."""
    if not samples: return 64
    avg = sum(len(s.encode('utf-8')) for s in samples[:10]) // min(10, len(samples))
    pkt_len = min(255, ((avg + 15) // 8) * 8)
    return max(8, pkt_len)

# ---------------------------------------------------------------------------
# 1. DWD SYNOP — raw CSV rows
# ---------------------------------------------------------------------------

DWD_STATIONS = {
    '00691': 'Zugspitze',
    '05792': 'Fichtelberg',
    '01975': 'Helgoland',
}

def fetch_dwd_raw(station_id='00691', limit=10000):
    name = DWD_STATIONS.get(station_id, station_id)
    print(f"\n[DWD raw] {name} ({station_id})")
    base = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/recent/"
    url = base + f"10minutenwerte_TU_{station_id}_akt.zip"
    try:
        r = SESSION.get(url, timeout=30); r.raise_for_status()
    except Exception:
        logging.warning(f"Failed to download DWD raw for {station_id}", exc_info=True)
        return [], []

    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        candidates = [f for f in z.namelist() if f.startswith('produkt_')] or z.namelist()
        if not candidates: return [], []
        content = z.read(candidates[0]).decode('latin-1')
    except Exception:
        logging.warning("Failed to extract DWD archive", exc_info=True)
        return [], []

    lines = content.strip().split('\n')
    data_lines = [l.strip().removesuffix(';eor') for l in lines[1:] if l.strip()]
    pkt_len = detect_pkt_len(data_lines[:10])

    packets = []
    timestamps = []
    for line in data_lines[:limit]:
        parts = line.split(';')
        ts = parts[0].strip() if parts else str(len(packets))
        packets.append(to_packet(line, pkt_len))
        timestamps.append(ts)
    return packets, timestamps

# ---------------------------------------------------------------------------
# 2. Open-Meteo — raw JSON records
# ---------------------------------------------------------------------------

FORECAST_LOCATIONS = [
    ('Praha',      50.0755, 14.4378),
    ('Brno',       49.1951, 16.6068),
    ('Ostrava',    49.8209, 18.2625),
    ('Bratislava', 48.1486, 17.1077),
]

def fetch_meteo_raw(lat, lon, name, limit=10000):
    print(f"\n[Open-Meteo raw] {name}")
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(["temperature_2m", "relative_humidity_2m", "surface_pressure", "wind_speed_10m", "wind_direction_10m", "precipitation", "dew_point_2m", "apparent_temperature"]),
        "forecast_days": 16, "timezone": "UTC", "wind_speed_unit": "ms",
    }
    try:
        r = SESSION.get(url, params=params, timeout=20); r.raise_for_status()
        h = r.json()["hourly"]
    except Exception:
        logging.warning(f"Failed to download Open-Meteo raw for {name}", exc_info=True)
        return [], []

    keys = [k for k in h.keys() if k != 'time']
    n = min(limit, len(h.get('time', [])))
    samples = [json.dumps({k: h[k][i] for k in keys if i < len(h.get(k, []))}, separators=(',', ':')) for i in range(min(10, n))]
    pkt_len = detect_pkt_len(samples)

    packets = []; timestamps = []
    for i in range(n):
        rec = {k: h[k][i] for k in keys if i < len(h.get(k, []))}
        raw = json.dumps(rec, separators=(',', ':'))
        packets.append(to_packet(raw, pkt_len))
        timestamps.append(h['time'][i] if i < len(h.get('time', [])) else str(i))
    return packets, timestamps

# ---------------------------------------------------------------------------
# 3. NOAA Tides — raw JSON records
# ---------------------------------------------------------------------------

NOAA_STATIONS = { '8518750': 'New_York', '9414290': 'San_Francisco' }

def fetch_noaa_raw(station='8518750', limit=10000):
    from datetime import datetime, timedelta
    print(f"\n[NOAA Tides raw] {station}")
    url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    raw_list = []; timestamps = []
    cur, end = datetime.utcnow() - timedelta(days=365), datetime.utcnow()

    while cur < end and len(raw_list) < limit:
        nxt = min(cur + timedelta(days=30), end)
        params = {"station": station, "product": "hourly_height", "datum": "MLLW", "time_zone": "GMT", "units": "metric", "begin_date": cur.strftime("%Y%m%d"), "end_date": nxt.strftime("%Y%m%d"), "format": "json"}
        try:
            r = SESSION.get(url, params=params, timeout=20); r.raise_for_status()
            data = r.json()
            for rec in data.get('data', []):
                raw = json.dumps(rec, separators=(',', ':'))
                raw_list.append(raw); timestamps.append(rec.get('t', str(len(raw_list))))
                if len(raw_list) >= limit: break
            time.sleep(0.3)
        except Exception:
            logging.warning(f"Failed to download NOAA raw for {station}", exc_info=True)
            break
        cur = nxt

    pkt_len = detect_pkt_len(raw_list[:10])
    return [to_packet(r, pkt_len) for r in raw_list], timestamps

# ---------------------------------------------------------------------------
# 4. USGS Earthquake — raw CSV records
# ---------------------------------------------------------------------------

def fetch_usgs_raw(limit=10000):
    print(f"\n[USGS Earthquake raw]")
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.csv"
    try:
        r = SESSION.get(url, timeout=30); r.raise_for_status()
        lines = r.text.strip().split('\n')
    except Exception:
        logging.warning("Failed to download USGS raw", exc_info=True)
        return [], []

    data_lines = [l.strip() for l in lines[1:] if l.strip()]
    pkt_len = detect_pkt_len(data_lines[:10])
    packets = []; timestamps = []
    reader = csv.DictReader(lines)
    for row in reader:
        raw = f"{row.get('time','')},{row.get('latitude','')},{row.get('longitude','')},{row.get('depth','')},{row.get('mag','')},{row.get('place','')}"
        packets.append(to_packet(raw, pkt_len))
        timestamps.append(row.get('time', str(len(packets)))[:19])
        if len(packets) >= limit: break
    return packets, timestamps

if __name__ == "__main__":
    pass
