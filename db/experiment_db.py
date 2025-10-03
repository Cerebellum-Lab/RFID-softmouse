"""Local experiment database management using SQLite with optional remote sync.

Schema overview (single local SQLite file):
  table mice (
      rfid TEXT PRIMARY KEY,
      last_softmouse_pull TIMESTAMP NULL,
      softmouse_payload JSON NULL,
      created_utc TIMESTAMP NOT NULL,
      updated_utc TIMESTAMP NOT NULL
  )
  table sessions (
      id TEXT PRIMARY KEY,              -- UUID4
      rfid TEXT NOT NULL,               -- FK to mice.rfid (no cascade needed)
      start_utc TIMESTAMP NOT NULL,
      stop_utc TIMESTAMP NULL,
      prerecord JSON NULL,
      postrecord JSON NULL,
      session_notes JSON NULL,          -- consolidated JSON (mirrors session_notes.json file contents if recording)
      metadata_yaml_path TEXT NULL,     -- path to on-disk yaml (if any)
      session_dir TEXT NULL,            -- raw directory on disk (if a recording took place)
      was_live_only INTEGER NOT NULL DEFAULT 0,
      synced INTEGER NOT NULL DEFAULT 0,
      created_utc TIMESTAMP NOT NULL,
      updated_utc TIMESTAMP NOT NULL
  )
  indexes: sessions (rfid, created_utc desc)

Design principles:
 - All JSON are stored as TEXT (raw json.dumps outputs)
 - API returns and accepts native Python dicts; serialization handled internally
 - Sync flag marks rows pushed to remote. Remote sync code just selects synced=0.

Remote sync placeholder:
 - Implement a push_only sync by POSTing unsynced rows to a remote HTTP endpoint or
   inserting into a remote SQL database. For now we implement a hook interface
   so acquisition GUI can call push_remote() safely.
"""
from __future__ import annotations
import sqlite3, json, uuid, datetime as dt, os, threading, contextlib, typing as t
from typing import Optional, List

DB_NAME = 'experiment_local.sqlite'

class ExperimentDB:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.db_path = os.path.join(root_dir, DB_NAME)
        os.makedirs(root_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    # ---------------- internal helpers ----------------
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys=OFF')  # we manage manually
        return conn

    def _init_schema(self):
        with self._lock, self._connect() as cx:
            cx.executescript(
                """
                CREATE TABLE IF NOT EXISTS mice (
                    rfid TEXT PRIMARY KEY,
                    last_softmouse_pull TIMESTAMP NULL,
                    softmouse_payload TEXT NULL,
                    created_utc TIMESTAMP NOT NULL,
                    updated_utc TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    rfid TEXT NOT NULL,
                    start_utc TIMESTAMP NOT NULL,
                    stop_utc TIMESTAMP NULL,
                    prerecord TEXT NULL,
                    postrecord TEXT NULL,
                    session_notes TEXT NULL,
                    metadata_yaml_path TEXT NULL,
                    session_dir TEXT NULL,
                    was_live_only INTEGER NOT NULL DEFAULT 0,
                    synced INTEGER NOT NULL DEFAULT 0,
                    created_utc TIMESTAMP NOT NULL,
                    updated_utc TIMESTAMP NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_rfid_created ON sessions(rfid, created_utc DESC);
                """
            )

    # ---------------- mice table ops ----------------
    def ensure_mouse(self, rfid: str, softmouse_payload: Optional[dict] = None):
        now = dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        payload_txt = json.dumps(softmouse_payload) if softmouse_payload else None
        with self._lock, self._connect() as cx:
            cur = cx.execute('SELECT rfid FROM mice WHERE rfid=?', (rfid,))
            if cur.fetchone():
                cx.execute('UPDATE mice SET updated_utc=?, last_softmouse_pull=COALESCE(last_softmouse_pull, ?), softmouse_payload=COALESCE(?, softmouse_payload) WHERE rfid=?', (now, now, payload_txt, rfid))
            else:
                cx.execute('INSERT INTO mice (rfid,last_softmouse_pull,softmouse_payload,created_utc,updated_utc) VALUES (?,?,?,?,?)', (rfid, now, payload_txt, now, now))

    def get_mouse_softmouse_payload(self, rfid: str) -> Optional[dict]:
        with self._lock, self._connect() as cx:
            cur = cx.execute('SELECT softmouse_payload FROM mice WHERE rfid=?', (rfid,))
            row = cur.fetchone()
            if not row or row[0] is None:
                return None
            try:
                return json.loads(row[0])
            except Exception:
                return None

    # ---------------- session ops ----------------
    def start_session(self, rfid: str, prerecord: Optional[dict], was_live_only: bool = False, session_dir: Optional[str] = None, metadata_yaml_path: Optional[str] = None) -> str:
        sid = str(uuid.uuid4())
        now = dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        with self._lock, self._connect() as cx:
            cx.execute('INSERT INTO sessions (id,rfid,start_utc,prerecord,was_live_only,session_dir,metadata_yaml_path,created_utc,updated_utc) VALUES (?,?,?,?,?,?,?,?,?)', (
                sid, rfid, now, json.dumps(prerecord) if prerecord else None, 1 if was_live_only else 0, session_dir, metadata_yaml_path, now, now
            ))
        return sid

    def finalize_session(self, sid: str, postrecord: Optional[dict], session_notes: Optional[dict] = None):
        now = dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        with self._lock, self._connect() as cx:
            cx.execute('UPDATE sessions SET stop_utc=?, postrecord=?, session_notes=?, updated_utc=? WHERE id=?', (
                now, json.dumps(postrecord) if postrecord else None, json.dumps(session_notes) if session_notes else None, now, sid
            ))

    def last_session_for_mouse(self, rfid: str) -> Optional[dict]:
        with self._lock, self._connect() as cx:
            cur = cx.execute('SELECT id, start_utc, stop_utc, prerecord, postrecord, session_notes, was_live_only FROM sessions WHERE rfid=? ORDER BY start_utc DESC LIMIT 1', (rfid,))
            row = cur.fetchone()
            if not row:
                return None
            def _load(txt):
                if txt is None: return None
                try: return json.loads(txt)
                except Exception: return None
            return {
                'id': row[0],
                'start_utc': row[1],
                'stop_utc': row[2],
                'prerecord': _load(row[3]),
                'postrecord': _load(row[4]),
                'session_notes': _load(row[5]),
                'was_live_only': bool(row[6])
            }

    def unsynced_sessions(self) -> List[dict]:
        with self._lock, self._connect() as cx:
            cur = cx.execute('SELECT id, rfid, start_utc, stop_utc, prerecord, postrecord, session_notes, was_live_only FROM sessions WHERE synced=0 AND stop_utc IS NOT NULL')
            rows = cur.fetchall()
        out = []
        for r in rows:
            def _load(txt):
                if txt is None: return None
                try: return json.loads(txt)
                except Exception: return None
            out.append({
                'id': r[0], 'rfid': r[1], 'start_utc': r[2], 'stop_utc': r[3],
                'prerecord': _load(r[4]), 'postrecord': _load(r[5]), 'session_notes': _load(r[6]), 'was_live_only': bool(r[7])
            })
        return out

    def mark_synced(self, ids: List[str]):
        if not ids:
            return
        now = dt.datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        with self._lock, self._connect() as cx:
            qmarks = ','.join('?' for _ in ids)
            cx.execute(f'UPDATE sessions SET synced=1, updated_utc=? WHERE id IN ({qmarks})', [now, *ids])

# -------------- Remote sync placeholder --------------
class RemoteSyncClient:
    """Placeholder remote client.

    Replace `push_sessions` with actual network / DB insertion.
    """
    def __init__(self, endpoint: Optional[str] = None):
        self.endpoint = endpoint or 'https://example.invalid/upload'

    def push_sessions(self, sessions: List[dict]) -> bool:
        # For now, just pretend success. Hook for HTTP POST / SQL insert.
        # Implement retry/backoff as needed.
        return True


def push_unsynced(local_db: ExperimentDB, remote: RemoteSyncClient) -> int:
    sessions = local_db.unsynced_sessions()
    if not sessions:
        return 0
    ok = remote.push_sessions(sessions)
    if ok:
        local_db.mark_synced([s['id'] for s in sessions])
        return len(sessions)
    return 0

__all__ = ['ExperimentDB', 'RemoteSyncClient', 'push_unsynced']
