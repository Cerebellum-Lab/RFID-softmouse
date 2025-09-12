"""ETL stub for populating the local SoftMouse mirror.

Workflow expectation:
1. Export CSVs from SoftMouse (manually first; later via automation) into an exports/ directory.
2. Run: python etl_softmouse.py --exports ./exports
3. Script loads mice.csv, genotypes.csv, etc., upserts into SQLite.

You can safely re-run; upserts are idempotent.
"""
from __future__ import annotations
import argparse, csv, pathlib, sqlite3, sys
from typing import Dict, Any
import db

EXPECTED_FILES = {
    'mice': 'mice.csv',
    'genotypes': 'genotypes.csv',
    # extend later: cages.csv, matings.csv, litters.csv
}


def load_csv(path: pathlib.Path):
    with path.open('r', newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {k.strip(): (v.strip() if isinstance(v, str) else v) for k,v in row.items()}


def etl(exports_dir: pathlib.Path):
    if not exports_dir.exists():
        raise SystemExit(f"Exports directory not found: {exports_dir}")
    db.init()
    with db.connect() as conn:
        mice_path = exports_dir / EXPECTED_FILES['mice']
        if mice_path.exists():
            for row in load_csv(mice_path):
                # Map/rename source columns to our schema as needed.
                rec = {
                    'rfid': row.get('RFID') or row.get('Transponder') or row.get('AltID'),
                    'mouse_id': row.get('MouseID') or row.get('ID'),
                    'sex': row.get('Sex'),
                    'dob': row.get('DOB') or row.get('BirthDate'),
                    'strain': row.get('Strain'),
                    'status': row.get('Status'),
                    'cage_id': row.get('Cage'),
                    'notes': row.get('Notes'),
                    'source': 'softmouse_export'
                }
                if rec['rfid']:
                    db.upsert_mouse(conn, rec)
        genos_path = exports_dir / EXPECTED_FILES['genotypes']
        if genos_path.exists():
            # For now assume columns: RFID,Locus,Genotype
            # We'll bulk load per RFID (delete+insert strategy)
            temp: Dict[str, list] = {}
            for row in load_csv(genos_path):
                rfid = row.get('RFID') or row.get('Transponder')
                if not rfid:
                    continue
                temp.setdefault(rfid, []).append({'locus': row.get('Locus'), 'genotype': row.get('Genotype')})
            for rfid, rows in temp.items():
                db.replace_child_table(conn, 'genotypes', rfid, rows, ['rfid','locus','genotype'])
        conn.commit()


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--exports', type=pathlib.Path, default=pathlib.Path('exports'), help='Directory holding exported CSVs')
    args = p.parse_args(argv)
    etl(args.exports)
    print('ETL complete.')

if __name__ == '__main__':
    main()
