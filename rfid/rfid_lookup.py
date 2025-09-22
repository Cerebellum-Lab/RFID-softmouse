"""RFID lookup helper.

Attempts HTTP API first (if local service running) then falls back to local
database access via `db` helper module. Minimal reconstruction of the original
implementation so that acquisition GUI and other tools can resolve metadata
for an RFID tag after repository slimming removed the root file.
"""
from __future__ import annotations
from typing import Optional, Dict
from app_logging import get_logger

API_URL = "http://127.0.0.1:8080"  # Adjust if external service moved
TIMEOUT = 1.5
_log = get_logger('rfid.lookup')


def fetch_mouse(rfid: str) -> Optional[Dict]:
	rfid = (rfid or '').strip()
	if not rfid:
		return None
	# HTTP attempt
	try:
		import requests  # local import to avoid mandatory dependency if unused
		r = requests.get(f"{API_URL}/mouse/{rfid}", timeout=TIMEOUT)
		if r.status_code == 200:
			_log.debug('RFID %s fetched via HTTP', rfid)
			return r.json()
		else:
			_log.info('RFID %s HTTP status %s', rfid, r.status_code)
	except Exception as e:  # pragma: no cover
		_log.debug('HTTP lookup failed for %s: %s', rfid, e)
	# DB fallback
	try:
		import db  # type: ignore
		conn = db.connect()
		rec = db.get_mouse(conn, rfid)
		if rec:
			_log.debug('RFID %s fetched via local DB', rfid)
		else:
			_log.info('RFID %s not found in local DB', rfid)
		return rec
	except Exception as e:  # pragma: no cover
		_log.error('Local DB lookup failed for %s: %s', rfid, e)
		return None


__all__ = ['fetch_mouse']

