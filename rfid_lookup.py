"""Helper for acquisition GUI to look up mouse metadata.
Tries FastAPI HTTP service first, then falls back to direct DB access.
"""
from __future__ import annotations
import requests, db
from typing import Optional, Dict

API_URL = "http://127.0.0.1:8080"  # FastAPI default we suggest
TIMEOUT = 1.5

def fetch_mouse(rfid: str) -> Optional[Dict]:
    rfid = rfid.strip()
    if not rfid:
        return None
    # Try HTTP
    try:
        r = requests.get(f"{API_URL}/mouse/{rfid}", timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    # Fallback direct DB
    try:
        conn = db.connect()
        return db.get_mouse(conn, rfid)
    except Exception:
        return None
