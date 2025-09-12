"""FastAPI service exposing Postgres mirror.

Endpoints:
  GET /health
  GET /mouse/{rfid}

Assumes schema + materialized view mouse_full already created (pg_init.py).

Env:
  PG_DSN or PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DB

Run:
  uvicorn pg_api:app --host 127.0.0.1 --port 8090 --reload
"""
from __future__ import annotations
import os, psycopg2, json
from fastapi import FastAPI, HTTPException, Depends, Query
from auth_placeholder import token_dependency
from writeback_queue import load_all
from pydantic import BaseModel

app = FastAPI(title='SoftMouse Postgres Mirror API', version='0.2.0')

_conn = None

def dsn():
    if os.getenv('PG_DSN'):
        return os.environ['PG_DSN']
    host = os.getenv('PG_HOST','localhost')
    port = os.getenv('PG_PORT','5432')
    user = os.getenv('PG_USER','postgres')
    pwd = os.getenv('PG_PASSWORD','postgres')
    db = os.getenv('PG_DB','softmouse')
    return f'postgresql://{user}:{pwd}@{host}:{port}/{db}'


def get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(dsn())
    return _conn

class Mouse(BaseModel):
    rfid: str
    softmouse_id: str | None = None
    sex: str | None = None
    dob: str | None = None
    strain: str | None = None
    status: str | None = None
    cage_id: str | None = None
    genotype_json: list | None = None
    notes: str | None = None
    source: str | None = None
    cage_history: list | None = None
    genotypes: list | None = None
    updated_at: str | None = None

@app.get('/health')
async def health():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
        return {'status':'ok'}
    except Exception as e:
        return {'status':'error','detail': str(e)}

@app.get('/mouse/{rfid}', response_model=Mouse)
async def get_mouse(rfid: str, _ok = Depends(token_dependency)):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute('SELECT * FROM mouse_full WHERE rfid=%s', (rfid,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='not_found')
        cols = [d[0] for d in cur.description]
        rec = dict(zip(cols, row))
        # genotype_json stored as JSON text in table; parse if string
        gj = rec.get('genotype_json')
        if isinstance(gj, str):
            try:
                rec['genotype_json'] = json.loads(gj)
            except Exception:
                pass
        return rec

@app.get('/')
async def root(_ok = Depends(token_dependency)):
    return {'service':'softmouse-pg','endpoints':['/health','/mouse/{rfid}','/refresh','/queue','/queue/{rfid}'],'auth':'token'}

@app.post('/refresh')
async def refresh(_ok = Depends(token_dependency)):
    # Refresh materialized view
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute('REFRESH MATERIALIZED VIEW CONCURRENTLY mouse_full')
    conn.commit()
    return {'refreshed': True}

@app.get('/queue')
async def queue(status: str | None = Query(default=None), _ok = Depends(token_dependency)):
    recs = load_all()
    if status:
        recs = [r for r in recs if r.get('status') == status]
    return {'count': len(recs), 'items': recs[:500]}  # cap to 500 for safety

@app.get('/queue/{rfid}')
async def queue_rfid(rfid: str, _ok = Depends(token_dependency)):
    recs = [r for r in load_all() if r.get('rfid') == rfid]
    if not recs:
        raise HTTPException(status_code=404, detail='no_patches')
    return {'count': len(recs), 'items': recs}
