"""ETL loader for Postgres mirror.

Responsibilities:
  - Locate/download SoftMouse export CSVs (download step stubbed here)
  - Validate required columns per entity
  - Upsert into tables (mice, cages, matings, litters) + embed genotypes as JSON array
  - Refresh materialized view mouse_full at end (CONCURRENTLY)

Usage:
  set PG_DSN=postgresql://user:pass@host:5432/dbname
  python pg_etl.py --exports ./exports
"""
from __future__ import annotations
import argparse, csv, json, os, pathlib, sys, psycopg2
from typing import Dict, List, Any

REQUIRED = {
    'mice': ['RFID','MouseID','Sex','DOB','Strain','Status','Cage'],
    'genotypes': ['RFID','Locus','Genotype'],
    'cages': ['CageID','Room','Rack'],
    'matings': ['MatingID','SireRFID','DamRFID','SetupDate','Status'],
    'litters': ['LitterID','MatingID','DOB']
}

FILE_NAMES = {
    'mice': 'mice.csv',
    'genotypes': 'genotypes.csv',
    'cages': 'cages.csv',
    'matings': 'matings.csv',
    'litters': 'litters.csv'
}

def dsn() -> str:
    return os.getenv('PG_DSN') or 'postgresql://postgres:postgres@localhost:5432/softmouse'

def load_csv(path: pathlib.Path):
    with path.open('r', newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {k.strip(): (v.strip() if isinstance(v,str) else v) for k,v in row.items()}

def validate_columns(kind: str, header: List[str]):
    missing = [c for c in REQUIRED[kind] if c not in header]
    if missing:
        raise SystemExit(f"Missing columns for {kind}: {missing}")

def upsert_mice(conn, rows: List[Dict[str,Any]]):
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                """
                INSERT INTO mice (rfid, softmouse_id, sex, dob, strain, status, cage_id, genotype_json, notes, source, updated_at)
                VALUES (%(rfid)s,%(softmouse_id)s,%(sex)s,%(dob)s,%(strain)s,%(status)s,%(cage_id)s,%(genotype_json)s,%(notes)s,%(source)s, now())
                ON CONFLICT (rfid) DO UPDATE SET
                  softmouse_id=excluded.softmouse_id,
                  sex=excluded.sex,
                  dob=excluded.dob,
                  strain=excluded.strain,
                  status=excluded.status,
                  cage_id=excluded.cage_id,
                  genotype_json=excluded.genotype_json,
                  notes=excluded.notes,
                  source=excluded.source,
                  updated_at=now()
                """,
                r
            )

def upsert_simple(table: str, pk: str, rows: List[Dict[str,Any]], conn):
    if not rows:
        return
    with conn.cursor() as cur:
        cols = rows[0].keys()
        col_list = ','.join(cols)
        placeholders = ','.join([f"%({c})s" for c in cols])
        updates = ','.join([f"{c}=excluded.{c}" for c in cols if c != pk])
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT ({pk}) DO UPDATE SET {updates}" if updates else f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        for r in rows:
            cur.execute(sql, r)

def refresh_view(conn):
    with conn.cursor() as cur:
        cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mouse_full")
    conn.commit()


def run(exports: pathlib.Path):
    if not exports.exists():
        raise SystemExit(f"Exports directory not found: {exports}")
    data = {}
    # Load each file if present
    for key, fname in FILE_NAMES.items():
        path = exports / fname
        if path.exists():
            rows = list(load_csv(path))
            if rows:
                validate_columns(key, list(rows[0].keys()))
            data[key] = rows
    # Re-shape mice + attach genotypes
    genotypes_by_rfid = {}
    for g in data.get('genotypes', []):
        genotypes_by_rfid.setdefault(g['RFID'], []).append({'locus': g['Locus'], 'genotype': g['Genotype']})

    mice_rows = []
    for m in data.get('mice', []):
        if not m.get('RFID'):
            continue
        mice_rows.append({
            'rfid': m['RFID'],
            'softmouse_id': m.get('MouseID'),
            'sex': m.get('Sex'),
            'dob': m.get('DOB'),
            'strain': m.get('Strain'),
            'status': m.get('Status'),
            'cage_id': m.get('Cage'),
            'genotype_json': json.dumps(genotypes_by_rfid.get(m['RFID'], [])),
            'notes': m.get('Notes'),
            'source': 'softmouse_export'
        })

    cage_rows = []
    for c in data.get('cages', []):
        if not c.get('CageID'):
            continue
        cage_rows.append({
            'cage_id': c['CageID'],
            'room': c.get('Room'),
            'rack': c.get('Rack'),
            'status': c.get('Status'),
            'notes': c.get('Notes')
        })

    mating_rows = []
    for mt in data.get('matings', []):
        if not mt.get('MatingID'):
            continue
        mating_rows.append({
            'mating_id': mt['MatingID'],
            'sire_rfid': mt.get('SireRFID'),
            'dam_rfid': mt.get('DamRFID'),
            'setup_date': mt.get('SetupDate'),
            'end_date': mt.get('EndDate'),
            'status': mt.get('Status'),
            'notes': mt.get('Notes')
        })

    litter_rows = []
    for lt in data.get('litters', []):
        if not lt.get('LitterID'):
            continue
        litter_rows.append({
            'litter_id': lt['LitterID'],
            'mating_id': lt.get('MatingID'),
            'dob': lt.get('DOB'),
            'wean_date': lt.get('WeanDate'),
            'count': lt.get('Count'),
            'status': lt.get('Status'),
            'notes': lt.get('Notes')
        })

    dsn_str = dsn()
    print('Connecting to', dsn_str)
    conn = psycopg2.connect(dsn_str)
    try:
        upsert_mice(conn, mice_rows)
        upsert_simple('cages','cage_id', cage_rows, conn)
        upsert_simple('matings','mating_id', mating_rows, conn)
        upsert_simple('litters','litter_id', litter_rows, conn)
        conn.commit()
        refresh_view(conn)
        print('ETL complete.')
    finally:
        conn.close()


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--exports', type=pathlib.Path, default=pathlib.Path('exports'))
    args = p.parse_args(argv)
    run(args.exports)

if __name__ == '__main__':
    main()
