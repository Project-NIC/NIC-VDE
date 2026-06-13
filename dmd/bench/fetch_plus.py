"""
NIC DMD — Real-data fetcher
================================
Multiple data sources for benchmarking without dependency on archive-api.open-meteo.com

Sources:
  1. DWD SYNOP       — German weather stations, 10-min data (verified working)
  2. Open-Meteo      — forecast API (more reliable than archive)
  3. USGS Earthquake — seismic data (small coordinate deltas)
  4. NOAA Tides      — sea level height (slow changes)
  5. Open-Meteo AQ   — air quality (PM2.5, NO2, O3...)
  6. GPS synthetic   — Alps trek (offline, always available)
  7. Smart meter syn.— consumption simulation (offline, always available)
  8. IoT sensor net  — IoT sensor simulation (offline)

Dependencies: pip install requests
"""

import os, sys, struct, math, random, time, csv, zipfile, io, logging
import requests
from nic_dmd_utils import dmd_analyze_packets as analyze_packets, dmd_print_summary as print_summary
# Import shared fetch helpers
from nic_dmd_fetch import clamp16, safe_float, get_session

OUTPUT_DIR = "real_data_plus"
os.makedirs(OUTPUT_DIR, exist_ok=True)

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SESSION = get_session()

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def save_report(results, filename):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        old = sys.stdout; sys.stdout = f
        try: print_summary(results)
        finally: sys.stdout = old
    print(f"  Report: {path}")

# ---------------------------------------------------------------------------
# 1. DWD SYNOP — German weather stations
# ---------------------------------------------------------------------------

DWD_STATIONS = {
    '00691': 'Zugspitze (2962m)',
    '05792': 'Fichtelberg (1213m)',
    '01975': 'Helgoland (coast)',
    '03456': 'Munich',
    '00433': 'Berlin-Tempelhof',
}

def fetch_dwd_synop(station_id='00691', limit=10000):
    name = DWD_STATIONS.get(station_id, station_id)
    print(f"\n[DWD] {name} ({station_id})")
    base = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/air_temperature/recent/"
    url  = base + f"10minutenwerte_TU_{station_id}_akt.zip"
    try:
        r = SESSION.get(url, timeout=30); r.raise_for_status()
    except Exception as e:
        logging.warning(f"Failed to download DWD data for {station_id}", exc_info=True)
        return None

    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        df = [f for f in z.namelist() if f.startswith('produkt_')][0]
        content = z.read(df).decode('latin-1')
    except Exception as e:
        logging.warning(f"Failed to extract DWD archive", exc_info=True)
        return None

    reader = csv.reader(content.strip().split('\n'), delimiter=';')
    hdr    = [h.strip() for h in next(reader)]
    def col(n):
        for i,h in enumerate(hdr):
            if n.upper() in h.upper(): return i
        return None

    idx_ts  = col('MESS_DATUM')
    idx_tt  = col('TT_10')
    idx_tm5 = col('TM5_10')
    idx_rf  = col('RF_10')
    idx_td  = col('TD_10')
    idx_pp  = col('PP_10')

    packets = []; timestamps = []
    for row in reader:
        if not row or len(row) < 3: continue
        try:
            def sf(idx, d=0.0):
                if idx is None or idx >= len(row): return d
                v = row[idx].strip()
                return d if v in ['-999','-999.0','','eor'] else safe_float(v, d)

            pkt = struct.pack('>8h',
                clamp16(sf(idx_tt)  * 100),            # air temp 2m °C×100
                clamp16(sf(idx_rf)  * 100),            # humidity %×100
                clamp16((sf(idx_pp, 1013.0)-900)*10), # pressure (hPa-900)×10
                clamp16(sf(idx_td)  * 100),            # dew point °C×100
                clamp16(sf(idx_tm5) * 100),            # temp 5cm °C×100
                0, 0, 0
            )
            packets.append(pkt)
            timestamps.append(row[idx_ts].strip() if idx_ts else str(len(packets)))
            if len(packets) >= limit: break
        except Exception: 
            continue

    print(f"  Loaded {len(packets)} samples × 16B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 2. Open-Meteo FORECAST — more reliable than archive
# ---------------------------------------------------------------------------

FORECAST_LOCATIONS = [
    ('Praha',      50.0755, 14.4378),
    ('Brno',       49.1951, 16.6068),
    ('Ostrava',    49.8209, 18.2625),
    ('Bratislava', 48.1486, 17.1077),
    ('Vienna',     48.2082, 16.3738),
    ('Munich',     48.1351, 11.5820),
    ('Warsaw',     52.2297, 21.0122),
    ('Budapest',   47.4979, 19.0402),
]

def fetch_open_meteo_forecast(lat, lon, name, limit=10000):
    print(f"\n[Open-Meteo forecast] {name} ({lat},{lon})")
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":        lat,
        "longitude":       lon,
        "hourly":          ",".join([
            "temperature_2m", "relative_humidity_2m", "surface_pressure",
            "wind_speed_10m", "wind_direction_10m", "precipitation",
            "dew_point_2m",   "apparent_temperature", "cloud_cover",
            "shortwave_radiation", "uv_index", "visibility",
            "soil_temperature_0cm", "soil_temperature_6cm",
            "soil_temperature_18cm", "soil_temperature_54cm",
        ]),
        "forecast_days":   16,
        "timezone":        "UTC",
        "wind_speed_unit": "ms",
    }
    try:
        r = SESSION.get(url, params=params, timeout=20); r.raise_for_status()
        h = r.json()["hourly"]
    except Exception as e:
        logging.warning(f"Failed to download Open-Meteo forecast for {name}", exc_info=True)
        return None

    def g(key, d=0.0, i=0):
        v = h.get(key, [d]*999)
        v = v[i] if i < len(v) else d
        return safe_float(v, d)

    n = min(limit, len(h.get('time', [])))
    packets = []; timestamps = []
    for i in range(n):
        pkt16 = struct.pack('>8h',
            clamp16(g('temperature_2m',0,i)       * 100),
            clamp16(g('relative_humidity_2m',0,i)  * 100),
            clamp16((g('surface_pressure',1013,i)-900) * 10),
            clamp16(g('wind_speed_10m',0,i)        * 100),
            clamp16(g('precipitation',0,i)         * 100),
            clamp16(g('soil_temperature_0cm',0,i)  * 100),
            clamp16(g('soil_temperature_6cm',0,i)  * 100),
            clamp16(g('dew_point_2m',0,i)          * 100),
        )
        packets.append(pkt16)
        timestamps.append(h['time'][i] if i < len(h.get('time',[])) else str(i))

    print(f"  Loaded {len(packets)} samples × 16B")
    return packets, timestamps

def fetch_open_meteo_forecast_32b(lat, lon, name, limit=10000):
    print(f"\n[Open-Meteo forecast 32B] {name}")
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude":        lat,
        "longitude":       lon,
        "hourly":          ",".join([
            "temperature_2m", "relative_humidity_2m", "surface_pressure",
            "wind_speed_10m", "wind_direction_10m", "precipitation",
            "dew_point_2m",   "apparent_temperature", "cloud_cover",
            "shortwave_radiation", "uv_index", "visibility",
            "soil_temperature_0cm", "soil_temperature_6cm",
            "soil_temperature_18cm", "soil_temperature_54cm",
        ]),
        "forecast_days":   16,
        "timezone":        "UTC",
        "wind_speed_unit": "ms",
    }
    try:
        r = SESSION.get(url, params=params, timeout=20); r.raise_for_status()
        h = r.json()["hourly"]
    except Exception as e:
        logging.warning(f"Failed to download Open-Meteo forecast 32B for {name}", exc_info=True)
        return None

    def g(key, d=0.0, i=0):
        v = h.get(key, [d]*999)
        v = v[i] if i < len(v) else d
        return safe_float(v, d)

    n = min(limit, len(h.get('time', [])))
    packets = []; timestamps = []
    for i in range(n):
        pkt32 = struct.pack('>16h',
            clamp16(g('temperature_2m',0,i)        * 100),
            clamp16(g('relative_humidity_2m',0,i)   * 100),
            clamp16((g('surface_pressure',1013,i)-900) * 10),
            clamp16(g('wind_speed_10m',0,i)         * 100),
            clamp16(g('wind_direction_10m',0,i)     * 10),
            clamp16(g('precipitation',0,i)          * 100),
            clamp16(g('dew_point_2m',0,i)           * 100),
            clamp16(g('apparent_temperature',0,i)   * 100),
            clamp16(g('cloud_cover',0,i)            * 100),
            clamp16(g('shortwave_radiation',0,i)    * 1),
            clamp16(g('uv_index',0,i)               * 100),
            clamp16(g('visibility',10000,i) / 100   * 10),
            clamp16(g('soil_temperature_0cm',0,i)   * 100),
            clamp16(g('soil_temperature_6cm',0,i)   * 100),
            clamp16(g('soil_temperature_18cm',0,i)  * 100),
            clamp16(g('soil_temperature_54cm',0,i)  * 100),
        )
        packets.append(pkt32)
        timestamps.append(h['time'][i] if i < len(h.get('time',[])) else str(i))

    print(f"  Loaded {len(packets)} samples × 32B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 3. Open-Meteo Air Quality
# ---------------------------------------------------------------------------

def fetch_open_meteo_airquality(lat, lon, name, limit=10000):
    print(f"\n[Open-Meteo AQ] {name}")
    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    params = {
        "latitude":     lat,
        "longitude":    lon,
        "hourly":       ",".join([
            "pm10", "pm2_5", "carbon_monoxide", "nitrogen_dioxide",
            "sulphur_dioxide", "ozone", "aerosol_optical_depth",
            "dust",
        ]),
        "forecast_days": 7,
        "timezone":      "UTC",
    }
    try:
        r = SESSION.get(url, params=params, timeout=20); r.raise_for_status()
        h = r.json()["hourly"]
    except Exception as e:
        logging.warning(f"Failed to download Open-Meteo AirQuality for {name}", exc_info=True)
        return None

    def g(key, d=0.0, i=0):
        v = h.get(key, [d]*999)
        v = v[i] if i < len(v) else d
        return safe_float(v, d)

    n = min(limit, len(h.get('time', [])))
    packets = []; timestamps = []
    for i in range(n):
        pkt = struct.pack('>8h',
            clamp16(g('pm10',0,i)               * 100),  # µg/m³ × 100
            clamp16(g('pm2_5',0,i)              * 100),
            clamp16(g('carbon_monoxide',0,i)    * 10),   # ppb × 10
            clamp16(g('nitrogen_dioxide',0,i)   * 100),
            clamp16(g('sulphur_dioxide',0,i)    * 100),
            clamp16(g('ozone',0,i)              * 100),
            clamp16(g('aerosol_optical_depth',0,i) * 1000),
            clamp16(g('dust',0,i)               * 100),
        )
        packets.append(pkt)
        timestamps.append(h['time'][i] if i < len(h.get('time',[])) else str(i))

    print(f"  Loaded {len(packets)} samples × 16B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 4. USGS Earthquake — seismic data
# ---------------------------------------------------------------------------

def fetch_usgs_earthquakes(limit=10000):
    print(f"\n[USGS Earthquake] last 30 days")
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_month.csv"
    try:
        r = SESSION.get(url, timeout=30); r.raise_for_status()
        lines = r.text.strip().split('\n')
    except Exception as e:
        logging.warning("Failed to download USGS Earthquakes", exc_info=True)
        return None

    reader = csv.DictReader(lines)
    packets = []; timestamps = []

    for row in reader:
        try:
            lat  = safe_float(row.get('latitude',  0))
            lon  = safe_float(row.get('longitude', 0))
            dep  = safe_float(row.get('depth',     0))
            mag  = safe_float(row.get('mag',       0))
            ts   = row.get('time', '')

            lat_int  = int(lat); lat_frac = int((abs(lat) - abs(lat_int)) * 10000)
            lon_int  = int(lon); lon_frac = int((abs(lon) - abs(lon_int)) * 10000)

            pkt = struct.pack('>8h',
                clamp16(lat * 100),
                clamp16(lon * 100),
                clamp16(dep * 10),
                clamp16(mag * 100),
                clamp16(lat_frac),
                clamp16(lon_frac),
                0, 0
            )
            packets.append(pkt)
            timestamps.append(ts[:19])
            if len(packets) >= limit: break
        except Exception: 
            continue

    print(f"  Loaded {len(packets)} samples × 16B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 5. NOAA Tides — sea level height, hourly data
# ---------------------------------------------------------------------------

NOAA_STATIONS = {
    '8518750': 'New York',
    '9414290': 'San Francisco',
    '8771450': 'Galveston TX',
    '8443970': 'Boston',
    '8726520': 'St. Petersburg FL',
}

def fetch_noaa_tides(station='8518750', limit=10000):
    name = NOAA_STATIONS.get(station, station)
    print(f"\n[NOAA Tides] {name} ({station})")

    from datetime import datetime, timedelta
    end   = datetime.utcnow()
    start = end - timedelta(days=365)

    url = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
    packets = []; timestamps = []

    cur = start
    while cur < end and len(packets) < limit:
        nxt = min(cur + timedelta(days=30), end)
        params = {
            "station":     station,
            "product":     "hourly_height",
            "datum":       "MLLW",
            "time_zone":   "GMT",
            "units":       "metric",
            "application": "NIC_DMD",
            "begin_date":  cur.strftime("%Y%m%d"),
            "end_date":    nxt.strftime("%Y%m%d"),
            "format":      "json",
        }
        try:
            r = SESSION.get(url, params=params, timeout=20); r.raise_for_status()
            data = r.json()
            if 'data' not in data:
                print(f"  Empty response: {data.get('error',{}).get('message','?')}")
                break
            for rec in data['data']:
                v  = safe_float(rec.get('v', 0))
                s  = safe_float(rec.get('s', 0))   # sigma (std dev)
                ts = rec.get('t', '')
                pkt = struct.pack('>8h',
                    clamp16(v * 1000),   # height mm
                    clamp16(s * 1000),   # sigma mm
                    0, 0, 0, 0, 0, 0
                )
                packets.append(pkt)
                timestamps.append(ts)
                if len(packets) >= limit: break
            time.sleep(0.3)
        except Exception as e:
            logging.warning(f"Failed to download NOAA chunk for {station}", exc_info=True)
            break
        cur = nxt + timedelta(days=1)

    print(f"  Loaded {len(packets)} samples × 16B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 6. GPS synthetic
# ---------------------------------------------------------------------------

def generate_gps_trek(count=10000):
    print(f"\n[GPS synthetic] Trek Chamonix→Zermatt ({count} points)")
    waypoints = [
        (45.9237,6.8694,1035),(45.9500,6.9000,2000),(45.9800,7.0000,2800),
        (46.0200,7.1000,2200),(46.0500,7.2000,2500),(45.9800,7.4000,1900),
        (45.9500,7.5500,2100),(46.0000,7.6500,1500),(46.0200,7.7500,1200),
        (46.0400,7.7800,1300),(46.0000,7.7500,2100),(45.9833,7.7500,1620),
    ]
    n_wps = len(waypoints)
    packets = []; timestamps = []
    prev_lat, prev_lon = waypoints[0][0], waypoints[0][1]

    for i in range(count):
        t = (i/count)*(n_wps-1); wi = min(int(t), n_wps-2); frac = t-wi
        lat1,lon1,ele1 = waypoints[wi]; lat2,lon2,ele2 = waypoints[wi+1]
        lat = lat1+(lat2-lat1)*frac+0.0003*math.sin(i*0.3)
        lon = lon1+(lon2-lon1)*frac+0.0003*math.cos(i*0.27)
        ele = ele1+(ele2-ele1)*frac+20*math.sin(i*0.1)
        dlat = (lat-prev_lat)*111000
        dlon = (lon-prev_lon)*111000*math.cos(math.radians(lat))
        dist = math.sqrt(dlat**2+dlon**2)
        speed   = min(9999, int(dist*360*10))
        heading = int(math.degrees(math.atan2(dlon,dlat))%360*10)
        lat_int=int(lat); lat_frac=int((abs(lat)-abs(lat_int))*10000)
        lon_int=int(lon); lon_frac=int((abs(lon)-abs(lon_int))*10000)
        packets.append(struct.pack('>hHhHhHHBB',
            lat_int, min(9999,lat_frac), lon_int, min(9999,lon_frac),
            max(-32768,min(32767,int(ele))), min(65535,speed),
            min(65535,heading), 12, 15))
        timestamps.append(f"trek_{i:05d}")
        prev_lat,prev_lon = lat,lon

    print(f"  Generated {len(packets)} samples × 16B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 7. Smart meter synthetic
# ---------------------------------------------------------------------------

def generate_smartmeter(count=10000):
    print(f"\n[Smart meter synthetic] ({count} samples)")
    random.seed(1234)
    packets = []; timestamps = []

    profile = [0.3,0.2,0.2,0.2,0.2,0.3,0.8,1.5,1.2,1.0,1.1,1.0,
               1.2,1.0,0.9,0.8,1.0,1.5,2.0,1.8,1.5,1.2,0.9,0.5]

    e  = 0
    for n in range(count):
        hour   = n % 24
        base   = int(profile[hour] * 1000)
        p      = base + random.randint(-50, 50)
        v      = 2300 + random.randint(-30, 30)
        i_val  = int(p * 10 / max(v, 1))
        f      = 5000 + random.randint(-3, 3)
        pf     = 95 + random.randint(-3, 3)
        e     += p // 100

        pkt = struct.pack('>8h',
            clamp16(p), clamp16(v), clamp16(i_val), clamp16(f),
            clamp16(pf), clamp16(e % 32767), 0, 0
        )
        packets.append(pkt)
        timestamps.append(f"meter_{n:05d}")

    print(f"  Generated {len(packets)} samples × 16B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 8. IoT sensor network
# ---------------------------------------------------------------------------

def generate_iot_building(count=10000):
    print(f"\n[IoT building synthetic] ({count} samples, 8 rooms)")
    random.seed(5678)
    packets = []; timestamps = []

    rooms = [
        {'base_t': 2100, 'base_h': 4500, 'base_co2': 400, 'base_lux': 0},
        {'base_t': 2200, 'base_h': 4200, 'base_co2': 500, 'base_lux': 200},
        {'base_t': 2050, 'base_h': 5000, 'base_co2': 420, 'base_lux': 100},
        {'base_t': 2150, 'base_h': 4800, 'base_co2': 600, 'base_lux': 300},
        {'base_t': 2300, 'base_h': 3800, 'base_co2': 450, 'base_lux': 500},
        {'base_t': 1950, 'base_h': 5500, 'base_co2': 380, 'base_lux': 0},
        {'base_t': 2250, 'base_h': 4100, 'base_co2': 550, 'base_lux': 400},
        {'base_t': 2100, 'base_h': 4600, 'base_co2': 410, 'base_lux': 150},
    ]

    state = [dict(r) for r in rooms]
    for n in range(count):
        hour = (n // 6) % 24
        occupied = 8 <= hour <= 18

        vals = []
        for s in state:
            s['base_t']   += random.randint(-5, 5)
            s['base_h']   += random.randint(-30, 30);  s['base_h']  = max(2000, min(8000, s['base_h']))
            s['base_co2'] += random.randint(-10, 20) if occupied else random.randint(-5, 5)
            s['base_co2']  = max(350, min(2000, s['base_co2']))
            s['base_lux']  = s['base_lux'] + random.randint(-20,20) if occupied else 0
            s['base_lux']  = max(0, s['base_lux'])
            vals.append(clamp16(s['base_t']))
            vals.append(clamp16(s['base_h']))

        pkt = struct.pack('>8h', *[clamp16(v) for v in vals[:8]])
        packets.append(pkt)
        timestamps.append(f"iot_{n:05d}")

    print(f"  Generated {len(packets)} samples × 16B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 9. Complex station 64B
# ---------------------------------------------------------------------------

def generate_complex_64b(count=10000):
    print(f"\n[Complex station 64B] ({count} samples)")
    random.seed(9999)
    packets = []; timestamps = []

    t, h, p, ws, wd, rain, dp, at, cc, sr, uv, vis = 2000, 6000, 3800, 500, 1800, 0, 1500, 1900, 5000, 200, 30, 10000
    st0, st6, st18, st54 = 1800, 1700, 1600, 1500
    lat, lon, ele, spd, hdg = 4592, 1443, 250, 0, 0
    bat, rssi, temp_mcu = 3700, -80, 2500
    pm10, pm25, co, no2, o3, so2, aod, dust = 1500, 800, 200, 150, 4000, 50, 100, 50

    for n in range(count):
        t    += random.randint(-20,20)
        h    += random.randint(-50,50);   h    = max(0,min(10000,h))
        p    += random.randint(-10,10)
        ws   += random.randint(-50,50);   ws   = max(0,min(5000,ws))
        wd    = (wd + random.randint(-50,50)) % 3600
        rain  = max(0, rain + random.randint(-10,30))
        dp   += random.randint(-15,15)
        at   += random.randint(-25,25)
        cc   += random.randint(-200,200); cc   = max(0,min(10000,cc))
        sr   += random.randint(-30,30);   sr   = max(0,min(1200,sr))
        uv   += random.randint(-5,5);     uv   = max(0,min(1100,uv))
        vis  += random.randint(-100,100); vis  = max(100,min(10000,vis))
        st0  += random.randint(-5,5)
        st6  += random.randint(-3,3)
        st18 += random.randint(-2,2)
        st54 += random.randint(-1,1)
        lat  += random.randint(-2,2)
        lon  += random.randint(-2,2)
        ele  += random.randint(-5,5)
        spd   = max(0,spd+random.randint(-10,10))
        hdg   = (hdg + random.randint(-20,20)) % 3600
        bat  += random.randint(-5,2);     bat  = max(3000,min(4200,bat))
        rssi += random.randint(-3,3);     rssi = max(-120,min(-40,rssi))
        temp_mcu += random.randint(-10,10)
        pm10 += random.randint(-50,100); pm10 = max(0,min(50000,pm10))
        pm25 += random.randint(-30,60);  pm25 = max(0,min(25000,pm25))
        co   += random.randint(-10,20);  co   = max(0,min(10000,co))
        no2  += random.randint(-10,15);  no2  = max(0,min(5000,no2))
        o3   += random.randint(-20,20);  o3   = max(0,min(10000,o3))
        so2  += random.randint(-5,8);    so2  = max(0,min(2000,so2))
        aod  += random.randint(-10,10);  aod  = max(0,min(5000,aod))
        dust += random.randint(-5,10);   dust = max(0,min(5000,dust))

        pkt = struct.pack('>32h',
            clamp16(t), clamp16(h), clamp16(p), clamp16(ws),
            clamp16(wd), clamp16(rain), clamp16(dp), clamp16(at),
            clamp16(cc), clamp16(sr), clamp16(uv), clamp16(vis),
            clamp16(st0), clamp16(st6), clamp16(st18), clamp16(st54),
            clamp16(lat), clamp16(lon), clamp16(ele), clamp16(spd),
            clamp16(hdg), clamp16(bat), clamp16(rssi), clamp16(temp_mcu),
            clamp16(pm10), clamp16(pm25), clamp16(co), clamp16(no2),
            clamp16(o3), clamp16(so2), clamp16(aod), clamp16(dust),
        )
        packets.append(pkt)
        timestamps.append(f"complex_{n:05d}")

    print(f"  Generated {len(packets)} samples × 64B")
    return packets, timestamps

# ---------------------------------------------------------------------------
# 10. Industrial sensor 128B
# ---------------------------------------------------------------------------

def generate_industrial_128b(count=10000):
    print(f"\n[Industrial sensor 128B] ({count} samples)")
    random.seed(7777)
    packets = []; timestamps = []

    acc = [random.randint(-500,500) for _ in range(12)]
    temps = [random.randint(2000,8000) for _ in range(16)]
    press = [random.randint(1000,30000) for _ in range(8)]
    amps  = [random.randint(0,5000) for _ in range(8)]
    rpm   = 15000
    power = 7500
    cycles= 0
    state = 1
    extra = [random.randint(0,10000) for _ in range(20)]

    for n in range(count):
        acc = [a + random.randint(-100,100) for a in acc]
        temps = [max(1500,min(15000,t + random.randint(-10,10))) for t in temps]
        press = [max(500,min(40000,p + random.randint(-50,50))) for p in press]
        amps  = [max(0,min(8000,a + random.randint(-20,20))) for a in amps]
        rpm  += random.randint(-100,100); rpm  = max(0,min(30000,rpm))
        power+= random.randint(-200,200); power= max(0,min(50000,power))
        cycles= (cycles + 1) % 32767
        state = random.choice([1,1,1,1,2])
        extra = [max(0,min(32767,e + random.randint(-50,50))) for e in extra]

        vals = (acc + temps + press + amps +
                [clamp16(rpm), clamp16(power), clamp16(cycles), clamp16(state)] +
                extra[:16])
        pkt = struct.pack(f'>64h', *[clamp16(v) for v in vals])
        packets.append(pkt)
        timestamps.append(f"cnc_{n:05d}")

    print(f"  Generated {len(packets)} samples × 128B")
    return packets, timestamps

if __name__ == "__main__":
    LIMIT = 10000

    print("=" * 70)
    print("NIC DMD+ — Fetching and analysing real-world data")
    print("=" * 70)

    all_results = {}

    def run_analysis(fetch_result, name):
        if fetch_result:
            pkts, ts = fetch_result
            if pkts:
                r = analyze_packets(pkts, ts, name)
                print_summary(r)
                save_report(r, f"{name}.txt")
                all_results[name] = r

    for sid in ['00691', '05792', '01975']:
        run_analysis(fetch_dwd_synop(sid, LIMIT), f"DWD_{DWD_STATIONS[sid].split('(')[0].strip()}_16B")
        time.sleep(1)

    for city, lat, lon in FORECAST_LOCATIONS[:4]:
        run_analysis(fetch_open_meteo_forecast(lat, lon, city, LIMIT), f"Forecast_{city}_16B")
        time.sleep(0.5)

    for city, lat, lon in FORECAST_LOCATIONS[:2]:
        run_analysis(fetch_open_meteo_forecast_32b(lat, lon, city, LIMIT), f"Forecast_{city}_32B")
        time.sleep(0.5)

    for city, lat, lon in FORECAST_LOCATIONS[:3]:
        run_analysis(fetch_open_meteo_airquality(lat, lon, city, LIMIT), f"AirQuality_{city}_16B")
        time.sleep(0.5)

    run_analysis(fetch_usgs_earthquakes(LIMIT), "USGS_Earthquake_16B")

    for sid in ['8518750', '9414290']:
        run_analysis(fetch_noaa_tides(sid, LIMIT), f"NOAA_{NOAA_STATIONS[sid].replace(' ','_')}_16B")
        time.sleep(1)

    for gen_fn, name in [
        (lambda: generate_gps_trek(LIMIT),        "GPS_Trek_16B"),
        (lambda: generate_smartmeter(LIMIT),       "SmartMeter_16B"),
        (lambda: generate_iot_building(LIMIT),     "IoT_Building_16B"),
        (lambda: generate_complex_64b(LIMIT),      "Complex_Station_64B"),
        (lambda: generate_industrial_128b(LIMIT),  "Industrial_Sensor_128B"),
    ]:
        run_analysis(gen_fn(), name)

    print(f"\n{'='*70}")
    print("GLOBAL SUMMARY")
    print(f"{'='*70}")
    print(f"{'Dataset':<35} {'Packets':>7} {'Saving%':>8} {'Errors':>6}")
    print(f"{'-'*70}")
    for name, r in all_results.items():
        if not r: continue
        orig  = sum(x['original_len']+1 for x in r)
        comp  = sum(x['compressed_len'] for x in r)
        errs  = sum(1 for x in r if not x['roundtrip_ok'])
        pct   = (1-comp/orig)*100 if orig > 0 else 0
        print(f"  {name:<33} {len(r):>7} {pct:>7.1f}% {errs:>6}")
    print(f"{'='*70}")
    print(f"\nReports saved to: {OUTPUT_DIR}/")
    print("Done!")
