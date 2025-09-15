"""Helper for acquisition GUI to look up mouse metadata.
Tries FastAPI HTTP service first, then falls back to direct DB access.
"""
from __future__ import annotations
import requests, db
from app_logging import get_logger
from typing import Optional, Dict

API_URL = "http://127.0.0.1:8080"  # FastAPI default we suggest
TIMEOUT = 1.5
_log = get_logger('rfid_lookup')

def fetch_mouse(rfid: str) -> Optional[Dict]:
    rfid = rfid.strip()
    if not rfid:
        return None
    # Try HTTP
    try:
        r = requests.get(f"{API_URL}/mouse/{rfid}", timeout=TIMEOUT)
        if r.status_code == 200:
            _log.debug('RFID %s fetched via HTTP', rfid)
            return r.json()
        else:
            _log.info('RFID %s not found via HTTP status=%s', rfid, r.status_code)
    except Exception as e:
        _log.debug('HTTP lookup failed for %s: %s', rfid, e)
    # Fallback direct DB
    try:
        conn = db.connect()
        rec = db.get_mouse(conn, rfid)
        if rec:
            _log.debug('RFID %s fetched via local DB', rfid)
        else:
            _log.info('RFID %s not found in local DB', rfid)
        return rec
    except Exception as e:
        _log.error('Local DB lookup failed for %s: %s', rfid, e)
        return None
