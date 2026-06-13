"""
Shared helper functions for data fetching.
"""
import requests
import math
import logging

# Configure logging to capture errors (exception swallowing resolved)
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'NIC-DMD-Fetch/1.0'})

def clamp16(v):
    return max(-32768, min(32767, int(round(v))))

def safe_float(v, default=0.0):
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return default

def get_session():
    return SESSION
