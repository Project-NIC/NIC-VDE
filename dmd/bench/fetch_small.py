"""
NIC DMD+ — Small Packet Benchmark
==================================
Downloads the same sources as fetch_plus.py but packs each field into the
minimum width that matches the actual API precision. No false zeros, no
wasted int16 padding.

Scaling rules:
  • API value with 1 decimal place → store ×10 in int8/uint8 (capped)
    or int16/uint16 (uncapped), based on the real value range
  • API integer value               → uint8 (0-255) or uint16
  • API value with 2 dec. (AOD)     → ×100 in uint8 (0-2.55)
  • Pressure                        → ×10 in uint16 with offset or
                                       in range 8700-10800 (hPa×10)
  • Coordinates (USGS)              → ×100 in int16 (±327.67° covers the world)

Outputs in real_data_plus_small/:
  • <dataset>.txt        — DMD report per packet (compression, method, % saving)
  • <dataset>.src.txt    — raw packet values with schema-aware columns

Dependencies: pip install requests
"""

import os, sys, struct, math, time, csv, zipfile, io, logging
import requests
from nic_dmd_utils import dmd_analyze_packets as analyze_packets, dmd_print_summary as print_summary
# Import shared fetch helpers
from nic_dmd_fetch import get_session, clamp16

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SESSION = get_session()
OUTPUT_DIR = "real_data_plus_small"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Schemas: [(name, byte_width, signed)]
# Sum of widths = packet length.
# ---------------------------------------------------------------------------

SCHEMA_DWD = [
    ('temp_2m_C_x10',    2, True),
    ('humidity_pct',     1, False),
    ('pressure_hPa_x10', 2, False),   # uint16 ×10 (8700..10800 = 870..1080 hPa)
    ('dew_pt_C_x10',     2, True),
    ('temp_5cm_C_x10',   2, True),
]  # = 9 B

SCHEMA_FORECAST_16B = [
    ('temp_2m_C_x10',    2, True),
    ('humidity_pct',     1, False),
    ('pressure_hPa_x10', 2, False),
    ('wind_ms_x10',      1, False),   # uint8 ×10, cap 25.5 m/s
    ('precip_mm_x10',    1, False),   # uint8 ×10, cap 25.5 mm/h
    ('soil_0cm_C_x10',   2, True),
    ('soil_6cm_C_x10',   2, True),
    ('dew_pt_C_x10',     2, True),
]  # = 13 B

SCHEMA_FORECAST_32B = [
    ('temp_2m_C_x10',        2, True),
    ('humidity_pct',         1, False),
    ('pressure_hPa_x10',     2, False),
    ('wind_ms_x10',          1, False),
    ('wind_dir_deg_x10',     2, False),  # uint16 0-3600
    ('precip_mm_x10',        1, False),
    ('dew_pt_C_x10',         2, True),
    ('apparent_temp_C_x10',  2, True),
    ('cloud_pct',            1, False),
    ('radiation_Wm2',        2, False),  # uint16, max ~1200
    ('UV_idx_x10',           1, False),  # uint8 ×10, cap 25.5
    ('visibility_100m',      2, False),  # uint16, m/100
    ('soil_0cm_C_x10',       2, True),
    ('soil_6cm_C_x10',       2, True),
    ('soil_18cm_C_x10',      2, True),
    ('soil_54cm_C_x10',      2, True),
]  # = 27 B

SCHEMA_AQ = [
    ('PM10_ugm3_x10',        2, False),
    ('PM2_5_ugm3_x10',       2, False),
    ('CO_ppb_x10',           2, False),
    ('NO2_ugm3_x10',         1, False),   # uint8 ×10, cap 25.5
    ('SO2_ugm3_x10',         1, False),
    ('O3_ugm3_x10',          2, False),
    ('AOD_x100',             1, False),   # uint8 ×100 (0-2.55)
    ('dust_ugm3',            1, False),
]  # = 12 B

SCHEMA_USGS = [
    ('lat_deg_x100',    2, True),    # i16 ×100, ±327° covers the world
    ('lon_deg_x100',    2, True),
    ('depth_km_x10',    2, False),   # u16 ×10
    ('magnitude_x100',  2, True),    # i16 ×100 (may be negative)
]  # = 8 B

SCHEMA_NOAA = [
    ('height_mm',    2, True),    # i16 in mm (±32 m, real range ±2 m)
    ('sigma_mm',     1, False),   # u8 in mm (real max ~80 mm)
]  # = 3 B

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def safe(v, default=0.0):
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return default

def clamp(v, width, signed):
    """Clamp v to the range of the type with the given byte width."""
    if signed:
        lo, hi = -(1 << (width*8 - 1)), (1 << (width*8 - 1)) - 1
    else:
        lo, hi = 0, (1 << (width*8)) - 1
    return max(lo, min(hi, int(round(v))))

def pack_fields(values, schema):
    """Pack a list of values according to the schema into bytes."""
    parts = []
    for v, (_name, w, signed) in zip(values, schema):
        v_clamped = clamp(v, w, signed)
        parts.append(int(v_clamped).to_bytes(w, 'big', signed=signed))
    return b''.join(parts)

def save_report(results, filename):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        old = sys.stdout; sys.stdout = f
        try: print_summary(results)
        finally: sys.stdout = old
    print(f"  Report: {path}")

def save_source(packets, timestamps, filename, schema):
    """Raw data before DMD compression: parsed according to schema."""
    path = os.path.join(OUTPUT_DIR, filename)
    total_w = sum(w for _, w, _ in schema)
    with open(path, 'w', encoding='utf-8') as f:
        if not packets:
            f.write("(no data)\n"); print(f"  Source: {path}"); return
        f.write("# Source data before DMD compression (schema-aware, variable field widths)\n")
        f.write(f"# Packets: {len(packets)} | Packet width: {total_w}B | Fields: {len(schema)}\n")
        schema_str = ", ".join(f"{n}({'i' if s else 'u'}{w*8})" for n, w, s in schema)
        f.write(f"# Schema: {schema_str}\n")
        f.write("index\ttimestamp\t" + "\t".join(n for n, _, _ in schema) + "\n")
        for i, (pkt, ts) in enumerate(zip(packets, timestamps)):
            off = 0
            vals = []
            for _name, w, signed in schema:
                vals.append(int.from_bytes(pkt[off:off+w], 'big', signed=signed))
                off += w
            f.write(f"{i+1}\t{ts}\t" + "\t".join(str(v) for v in vals) + "\n")
    print(f"  Source: {path}")

# ---------------------------------------------------------------------------
# 1. DWD SYNOP — 9 B packet
# ---------------------------------------------------------------------------

DWD_STATIONS = {
    '00691': 'Zugspitze (2962m)',
    '05792': 'Fichtelberg (1213m)',
    '01975': 'Helgoland (coast)',
}

def fetch_dwd_small(station_id='00691', limit=10000):
    name = DWD_STATIONS.get(station_id, station_id)
    print(f"\n[DWD small] {name} ({station_id})")
    base = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/recent/"
    url  = base + f"10minutenwerte_TU_{station_id}_akt.zip"
    try:
        r = SESSION.get(url, timeout=30); r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        df = [f for f in z.namelist() if f.startswith('produkt_')][0]
        content = z.read(df).decode('latin-1')
    except Exception:
        logging.warning(f"Failed to download/process DWD for {station_id}", exc_info=True)
        return [], []

    reader = csv.reader(content.strip().split('\n'), delimiter=';')
    hdr    = [h.strip() for h in next(reader)]
    def col(n):
        for i, h in enumerate(hdr):
            if n.upper() in h.upper(): return i
        return None
    idx_ts  = col('MESS_DATUM')
    idx_tt  = col('TT_10');  idx_rf  = col('RF_10')
    idx_td  = col('TD_10');  idx_tm5 = col('TM5_10')
    idx_pp  = col('PP_10')

    packets = []; timestamps = []
    for row in reader:
        if not row or len(row) < 3: continue
        try:
            def sf(idx, d=0.0):
                if idx is None or idx >= len(row): return d
                v = row[idx].strip()
                return d if v in ['-999', '-999.0', '', 'eor'] else safe(v, d)
            vals = [
                sf(idx_tt)  * 10,       # air temp 2m ×10
                sf(idx_rf),              # humidity (0-100)
                sf(idx_pp, 1013) * 10,  # pressure ×10
                sf(idx_td)  * 10,
                sf(idx_tm5) * 10,
            ]
            packets.append(pack_fields(vals, SCHEMA_DWD))
            timestamps.append(row[idx_ts].strip() if idx_ts else str(len(packets)))
            if len(packets) >= limit: break
        except Exception:
            continue
    print(f"  Loaded {len(packets)} samples × {sum(w for _,w,_ in SCHEMA_DWD)}B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 2. Open-Meteo Forecast — 13 B (16 B variant) / 27 B (32 B variant)
# ---------------------------------------------------------------------------

FORECAST_LOCATIONS = [
    ('Praha',      50.0755, 14.4378),
    ('Brno',       49.1951, 16.6068),
    ('Ostrava',    49.8209, 18.2625),
    ('Bratislava', 48.1486, 17.1077),
]

def _forecast_json(lat, lon, hourly, days):
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(hourly),
        "forecast_days": days, "timezone": "UTC", "wind_speed_unit": "ms",
    }
    r = SESSION.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=20)
    r.raise_for_status()
    return r.json()["hourly"]

def fetch_forecast_small(lat, lon, name, limit=10000):
    print(f"\n[Forecast small 13B] {name}")
    hourly = ["temperature_2m","relative_humidity_2m","surface_pressure",
              "wind_speed_10m","precipitation","dew_point_2m",
              "soil_temperature_0cm","soil_temperature_6cm"]
    try:
        h = _forecast_json(lat, lon, hourly, 16)
    except Exception:
        logging.warning(f"Failed to download forecast for {name}", exc_info=True)
        return [], []
    def g(k, d=0.0, i=0):
        v = h.get(k, [d]*999); v = v[i] if i < len(v) else d
        return safe(v, d)
    n = min(limit, len(h.get('time', [])))
    packets = []; timestamps = []
    for i in range(n):
        vals = [
            g('temperature_2m',     0, i)*10,
            g('relative_humidity_2m',0,i),
            g('surface_pressure',1013,i)*10,
            g('wind_speed_10m',     0, i)*10,
            g('precipitation',      0, i)*10,
            g('soil_temperature_0cm',0,i)*10,
            g('soil_temperature_6cm',0,i)*10,
            g('dew_point_2m',       0, i)*10,
        ]
        packets.append(pack_fields(vals, SCHEMA_FORECAST_16B))
        timestamps.append(h['time'][i] if i < len(h.get('time',[])) else str(i))
    print(f"  Loaded {len(packets)} samples × {sum(w for _,w,_ in SCHEMA_FORECAST_16B)}B")
    return packets, timestamps

def fetch_forecast_small_32b(lat, lon, name, limit=10000):
    print(f"\n[Forecast small 27B] {name}")
    hourly = ["temperature_2m","relative_humidity_2m","surface_pressure",
              "wind_speed_10m","wind_direction_10m","precipitation",
              "dew_point_2m","apparent_temperature","cloud_cover",
              "shortwave_radiation","uv_index","visibility",
              "soil_temperature_0cm","soil_temperature_6cm",
              "soil_temperature_18cm","soil_temperature_54cm"]
    try:
        h = _forecast_json(lat, lon, hourly, 16)
    except Exception:
        logging.warning(f"Failed to download forecast 32B for {name}", exc_info=True)
        return [], []
    def g(k, d=0.0, i=0):
        v = h.get(k, [d]*999); v = v[i] if i < len(v) else d
        return safe(v, d)
    n = min(limit, len(h.get('time', [])))
    packets = []; timestamps = []
    for i in range(n):
        vals = [
            g('temperature_2m',     0, i)*10,
            g('relative_humidity_2m',0,i),
            g('surface_pressure',1013,i)*10,
            g('wind_speed_10m',     0, i)*10,
            g('wind_direction_10m', 0, i)*10,
            g('precipitation',      0, i)*10,
            g('dew_point_2m',       0, i)*10,
            g('apparent_temperature',0,i)*10,
            g('cloud_cover',        0, i),
            g('shortwave_radiation',0,i),
            g('uv_index',           0, i)*10,
            g('visibility',     10000,i)/100,
            g('soil_temperature_0cm', 0,i)*10,
            g('soil_temperature_6cm', 0,i)*10,
            g('soil_temperature_18cm',0,i)*10,
            g('soil_temperature_54cm',0,i)*10,
        ]
        packets.append(pack_fields(vals, SCHEMA_FORECAST_32B))
        timestamps.append(h['time'][i] if i < len(h.get('time',[])) else str(i))
    print(f"  Loaded {len(packets)} samples × {sum(w for _,w,_ in SCHEMA_FORECAST_32B)}B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 3. Open-Meteo Air Quality — 12 B packet
# ---------------------------------------------------------------------------

def fetch_aq_small(lat, lon, name, limit=10000):
    print(f"\n[AirQuality small 12B] {name}")
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone,aerosol_optical_depth,dust",
        "forecast_days": 7, "timezone": "UTC",
    }
    try:
        r = SESSION.get("https://air-quality-api.open-meteo.com/v1/air-quality", params=params, timeout=20)
        r.raise_for_status(); h = r.json()["hourly"]
    except Exception:
        logging.warning(f"Failed to download AQ for {name}", exc_info=True)
        return [], []
    def g(k, d=0.0, i=0):
        v = h.get(k, [d]*999); v = v[i] if i < len(v) else d
        return safe(v, d)
    n = min(limit, len(h.get('time', [])))
    packets = []; timestamps = []
    for i in range(n):
        vals = [
            g('pm10',                  0, i)*10,
            g('pm2_5',                 0, i)*10,
            g('carbon_monoxide',       0, i)*10,
            g('nitrogen_dioxide',      0, i)*10,
            g('sulphur_dioxide',       0, i)*10,
            g('ozone',                 0, i)*10,
            g('aerosol_optical_depth', 0, i)*100,
            g('dust',                  0, i),
        ]
        packets.append(pack_fields(vals, SCHEMA_AQ))
        timestamps.append(h['time'][i] if i < len(h.get('time',[])) else str(i))
    print(f"  Loaded {len(packets)} samples × {sum(w for _,w,_ in SCHEMA_AQ)}B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 4. USGS Earthquake — 8 B packet
# ---------------------------------------------------------------------------

def fetch_usgs_small(limit=10000):
    print(f"\n[USGS small 8B] last 30 days")
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.csv"
    try:
        r = SESSION.get(url, timeout=30); r.raise_for_status()
        lines = r.text.strip().split('\n')
    except Exception:
        logging.warning("Failed to download USGS", exc_info=True)
        return [], []
    reader = csv.DictReader(lines)
    packets = []; timestamps = []
    for row in reader:
        try:
            vals = [
                safe(row.get('latitude',  0)) * 100,
                safe(row.get('longitude', 0)) * 100,
                safe(row.get('depth',     0)) * 10,
                safe(row.get('mag',       0)) * 100,
            ]
            packets.append(pack_fields(vals, SCHEMA_USGS))
            timestamps.append(row.get('time', '')[:19])
            if len(packets) >= limit: break
        except Exception:
            continue
    print(f"  Loaded {len(packets)} samples × {sum(w for _,w,_ in SCHEMA_USGS)}B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 5. NOAA Tides — 3 B packet
# ---------------------------------------------------------------------------

NOAA_STATIONS = {
    '8518750': 'New_York',
    '9414290': 'San_Francisco',
}

def fetch_noaa_small(station='8518750', limit=10000):
    from datetime import datetime, timedelta, timezone
    name = NOAA_STATIONS.get(station, station)
    print(f"\n[NOAA Tides small 3B] {name} ({station})")
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=365)
    url   = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    packets = []; timestamps = []
    cur = start
    while cur < end and len(packets) < limit:
        nxt = min(cur + timedelta(days=30), end)
        params = {
            "station": station, "product": "hourly_height",
            "datum": "MLLW", "time_zone": "GMT", "units": "metric",
            "application": "NIC_DMD",
            "begin_date": cur.strftime("%Y%m%d"),
            "end_date":   nxt.strftime("%Y%m%d"),
            "format": "json",
        }
        try:
            r = SESSION.get(url, params=params, timeout=20); r.raise_for_status()
            data = r.json()
            if 'data' not in data: break
            for rec in data['data']:
                v = safe(rec.get('v', 0)) * 1000   # height m → mm
                s = safe(rec.get('s', 0)) * 1000   # sigma m → mm
                packets.append(pack_fields([v, s], SCHEMA_NOAA))
                timestamps.append(rec.get('t', ''))
                if len(packets) >= limit: break
            time.sleep(0.3)
        except Exception:
            logging.warning(f"Failed to download NOAA for {station}", exc_info=True)
            break
        cur = nxt + timedelta(days=1)
    print(f"  Loaded {len(packets)} samples × {sum(w for _,w,_ in SCHEMA_NOAA)}B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    LIMIT = 10000
    print("=" * 70)
    print("NIC DMD+ Small — minimum packet width matching real API precision")
    print("=" * 70)
    print(f"\nOutput: {OUTPUT_DIR}/")
    all_results = {}

    def process_fetch_result(fetch_tuple, name, schema):
        if fetch_tuple:
            pkts, ts = fetch_tuple
            if pkts:
                r = analyze_packets(pkts, ts, name)
                print_summary(r)
                save_report(r, f"{name}.txt")
                save_source(pkts, ts, f"{name}.src.txt", schema)
                all_results[name] = r

    # DWD — dataset names use the station name as-is (matches README tables)
    for sid in ['00691', '05792', '01975']:
        w = sum(x for _,x,_ in SCHEMA_DWD)
        name = f"DWD_{DWD_STATIONS[sid].split('(')[0].strip()}_{w}B"
        process_fetch_result(fetch_dwd_small(sid, LIMIT), name, SCHEMA_DWD)
        time.sleep(1)

    # Forecast 13B (down from original 16B)
    for city, lat, lon in FORECAST_LOCATIONS[:4]:
        w = sum(x for _,x,_ in SCHEMA_FORECAST_16B)
        name = f"Forecast_{city}_{w}B"
        process_fetch_result(fetch_forecast_small(lat, lon, city, LIMIT), name, SCHEMA_FORECAST_16B)
        time.sleep(0.5)

    # Forecast 27B (down from original 32B)
    for city, lat, lon in FORECAST_LOCATIONS[:2]:
        w = sum(x for _,x,_ in SCHEMA_FORECAST_32B)
        name = f"Forecast_{city}_{w}B_full"
        process_fetch_result(fetch_forecast_small_32b(lat, lon, city, LIMIT), name, SCHEMA_FORECAST_32B)
        time.sleep(0.5)

    # AirQuality
    for city, lat, lon in FORECAST_LOCATIONS[:3]:
        w = sum(x for _,x,_ in SCHEMA_AQ)
        name = f"AirQuality_{city}_{w}B"
        process_fetch_result(fetch_aq_small(lat, lon, city, LIMIT), name, SCHEMA_AQ)
        time.sleep(0.5)

    # USGS
    w = sum(x for _,x,_ in SCHEMA_USGS)
    name = f"USGS_Earthquake_{w}B"
    process_fetch_result(fetch_usgs_small(LIMIT), name, SCHEMA_USGS)

    # NOAA Tides
    for sid in ['8518750', '9414290']:
        w = sum(x for _,x,_ in SCHEMA_NOAA)
        name = f"NOAA_{NOAA_STATIONS[sid]}_{w}B"
        process_fetch_result(fetch_noaa_small(sid, LIMIT), name, SCHEMA_NOAA)
        time.sleep(1)

    # Global summary
    print(f"\n{'='*78}")
    print("GLOBAL SUMMARY — minimum schemas per API precision")
    print(f"{'='*78}")
    print(f"{'Dataset':<40} {'Pkts':>5} {'W':>4} {'Comp/pkt':>9} {'Saving%':>8} {'Err':>4}")
    print(f"{'-'*78}")
    for name, r in all_results.items():
        if not r: continue
        pkt_len = r[0]['original_len']
        orig = sum(x['original_len']+1 for x in r)
        comp = sum(x['compressed_len'] for x in r)
        errs = sum(1 for x in r if not x['roundtrip_ok'])
        pct  = (1-comp/orig)*100 if orig > 0 else 0
        print(f"  {name:<38} {len(r):>5} {pkt_len:>3}B {comp/len(r):>8.2f}B {pct:>7.1f}% {errs:>4}")
    print(f"{'='*78}")
    print(f"\nOutputs in: {OUTPUT_DIR}/")
    print("Done!")
