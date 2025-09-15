"""FastAPI-based RFID metadata service.
Run with: uvicorn fastapi_service:app --host 127.0.0.1 --port 8080 --reload

Endpoints:
GET /health -> {status: ok}
GET /mouse/{rfid} -> mouse record or 404
POST /reload -> re-run ETL (optional: path to exports) and return {reloaded:true}
"""
from __future__ import annotations
from fastapi import FastAPI, HTTPException, Depends
from auth_placeholder import token_dependency
from pydantic import BaseModel
import db, etl_softmouse, pathlib
from app_logging import get_logger

log = get_logger('fastapi_service')

app = FastAPI(title="RFID SoftMouse Mirror API", version="0.1.0")
log.info('Starting fastapi_service')

db.init()
_conn = db.connect()

class Mouse(BaseModel):
    rfid: str
    mouse_id: str | None = None
    sex: str | None = None
    dob: str | None = None
    strain: str | None = None
    status: str | None = None
    cage_id: str | None = None
    notes: str | None = None
    source: str | None = None
    genotypes: list | None = None
    cage_history: list | None = None

class ReloadRequest(BaseModel):
    exports: str | None = None

@app.get('/health')
async def health():
    return {"status": "ok"}

@app.get('/mouse/{rfid}', response_model=Mouse)
async def get_mouse(rfid: str, _ok = Depends(token_dependency)):
    log.debug(f'Lookup RFID {rfid}')
    rec = db.get_mouse(_conn, rfid)
    if not rec:
        log.warning(f'RFID {rfid} not found')
        raise HTTPException(status_code=404, detail="not_found")
    log.info(f'RFID {rfid} served')
    return rec  # FastAPI + Pydantic will filter/validate

@app.post('/reload')
async def reload_data(req: ReloadRequest, _ok = Depends(token_dependency)):
    exports_dir = pathlib.Path(req.exports) if req.exports else pathlib.Path('exports')
    log.info(f'Reload requested for exports dir {exports_dir}')
    try:
        etl_softmouse.etl(exports_dir)
        log.info('Reload complete')
    except Exception as e:
        log.exception('Reload failed')
        raise HTTPException(status_code=500, detail=str(e))
    return {"reloaded": True, "exports": str(exports_dir)}

# Convenience root
@app.get('/')
async def root(_ok = Depends(token_dependency)):
    return {"service": "rfid-softmouse", "endpoints": ["/health", "/mouse/{rfid}", "/reload"], "auth":"token"}
