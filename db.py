"""SQLite helper for local SoftMouse mirror keyed by RFID.

Tables (initial minimal schema):
- mice: core animal info
- genotypes: one-to-many genotype records
- cages: cage assignments (history)
- matings: mating records (breeding info)
- litters: litter records

We keep it small now; can expand as needed. All tables have updated_at for ETL provenance.
"""
from __future__ import annotations
import sqlite3, json, datetime, pathlib, contextlib
from typing import Any, Dict, List, Optional, Tuple

DB_PATH = pathlib.Path(__file__).parent / "softmouse_mirror.sqlite"

DDL = [
    """CREATE TABLE IF NOT EXISTS mice (
        rfid TEXT PRIMARY KEY,
        mouse_id TEXT,
        sex TEXT,
        dob TEXT,
        strain TEXT,
        status TEXT,
        cage_id TEXT,
        notes TEXT,
        source TEXT,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS genotypes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rfid TEXT NOT NULL,
        locus TEXT,
        genotype TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(rfid) REFERENCES mice(rfid) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS cages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cage_id TEXT,
        rfid TEXT,
        start_date TEXT,
        end_date TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(rfid) REFERENCES mice(rfid) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS matings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mating_id TEXT,
        female_rfid TEXT,
        male_rfid TEXT,
        start_date TEXT,
        end_date TEXT,
        notes TEXT,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS litters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        litter_id TEXT,
        dam_rfid TEXT,
        sire_rfid TEXT,
        born_date TEXT,
        count INTEGER,
        notes TEXT,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_genotypes_rfid ON genotypes(rfid)",
    "CREATE INDEX IF NOT EXISTS idx_cages_rfid ON cages(rfid)",
    "CREATE INDEX IF NOT EXISTS idx_matings_female ON matings(female_rfid)",
    "CREATE INDEX IF NOT EXISTS idx_matings_male ON matings(male_rfid)",
    "CREATE INDEX IF NOT EXISTS idx_litters_dam ON litters(dam_rfid)",
    "CREATE INDEX IF NOT EXISTS idx_litters_sire ON litters(sire_rfid)"
]

def connect(db_path: Optional[pathlib.Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn

def init(db_path: Optional[pathlib.Path] = None) -> None:
    with contextlib.closing(connect(db_path)) as conn:
        cur = conn.cursor()
        for stmt in DDL:
            cur.execute(stmt)
        conn.commit()

# Upsert helpers -----------------------------------------------------------

def utcnow() -> str:
    return datetime.datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def upsert_mouse(conn: sqlite3.Connection, rec: Dict[str, Any]):
    rec = {**rec}
    rec['updated_at'] = utcnow()
    cols = [
        'rfid','mouse_id','sex','dob','strain','status','cage_id','notes','source','updated_at'
    ]
    values = [rec.get(c) for c in cols]
    placeholders = ','.join(['?'] * len(cols))
    update_clause = ','.join([f"{c}=excluded.{c}" for c in cols[1:]])
    sql = f"INSERT INTO mice ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT(rfid) DO UPDATE SET {update_clause}"
    conn.execute(sql, values)


def replace_child_table(conn: sqlite3.Connection, table: str, rfid: str, rows: List[Dict[str, Any]], columns: List[str]):
    # Simple strategy: delete then insert.
    conn.execute(f"DELETE FROM {table} WHERE rfid=?", (rfid,)) if 'rfid' in columns else None
    now = utcnow()
    if table == 'genotypes':
        for r in rows:
            conn.execute(
                "INSERT INTO genotypes (rfid,locus,genotype,updated_at) VALUES (?,?,?,?)",
                (rfid, r.get('locus'), r.get('genotype'), now)
            )
    # Extend for other tables as needed.

# Query --------------------------------------------------------------------

def get_mouse(conn: sqlite3.Connection, rfid: str) -> Optional[Dict[str, Any]]:
    m = conn.execute("SELECT * FROM mice WHERE rfid=?", (rfid,)).fetchone()
    if not m:
        return None
    genos = conn.execute("SELECT locus, genotype FROM genotypes WHERE rfid=? ORDER BY locus", (rfid,)).fetchall()
    cages = conn.execute("SELECT cage_id, start_date, end_date FROM cages WHERE rfid=? ORDER BY start_date DESC", (rfid,)).fetchall()
    data = dict(m)
    data['genotypes'] = [dict(g) for g in genos]
    data['cage_history'] = [dict(c) for c in cages]
    return data


def mouse_json(conn: sqlite3.Connection, rfid: str) -> str:
    rec = get_mouse(conn, rfid)
    if not rec:
        return json.dumps({"error": "not_found", "rfid": rfid})
    return json.dumps(rec, indent=2)

if __name__ == '__main__':
    init()
    with connect() as c:
        upsert_mouse(c, {"rfid":"TEST123","mouse_id":"M001","sex":"F","dob":"2024-01-01","strain":"C57BL/6","status":"Alive","cage_id":"C12","notes":"Demo","source":"seed"})
        c.commit()
        print(mouse_json(c, "TEST123"))
