"""Quick demo to query local API service.
Assumes api_service.py running.
Usage: python demo_query.py RFIDTAG
"""
import sys, json, urllib.request, urllib.error

HOST = '127.0.0.1'
PORT = 8077

if len(sys.argv) < 2:
    print('Usage: python demo_query.py RFIDTAG'); sys.exit(1)
rfid = sys.argv[1]
url = f'http://{HOST}:{PORT}/mouse?rfid={rfid}'
try:
    with urllib.request.urlopen(url, timeout=2) as r:
        print(json.dumps(json.loads(r.read().decode()), indent=2))
except urllib.error.HTTPError as e:
    print('HTTP error', e.code, e.read().decode())
except Exception as e:
    print('Error:', e)
