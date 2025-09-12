"""Initialize Postgres schema and (optional) refresh materialized view.

Usage:
  set PG_DSN=postgresql://user:pass@host:port/dbname  (Windows PowerShell: $env:PG_DSN="...")
  python pg_init.py

This script:
  - Connects using PG_DSN or individual env vars (PG_HOST, PG_DB, etc.)
  - Executes pg_schema.sql (idempotent)
  - Optionally performs an initial REFRESH MATERIALIZED VIEW mouse_full if it exists
"""
from __future__ import annotations
import os, psycopg2, pathlib, sys

SCHEMA_FILE = pathlib.Path(__file__).parent / 'pg_schema.sql'


def dsn_from_env() -> str:
    if os.getenv('PG_DSN'):
        return os.environ['PG_DSN']
    host = os.getenv('PG_HOST','localhost')
    port = os.getenv('PG_PORT','5432')
    user = os.getenv('PG_USER','postgres')
    pwd = os.getenv('PG_PASSWORD','postgres')
    db = os.getenv('PG_DB','softmouse')
    return f'postgresql://{user}:{pwd}@{host}:{port}/{db}'


def run_schema(conn):
    with open(SCHEMA_FILE,'r', encoding='utf-8') as f:
        sql = f.read()
    with conn.cursor() as cur:
        # Split cautiously on semicolons that end statements.
        for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
            cur.execute(stmt + ';')
    conn.commit()


def refresh_materialized_view(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_matviews WHERE matviewname='mouse_full'")
        if cur.fetchone():
            print('Refreshing materialized view mouse_full ...')
            cur.execute('REFRESH MATERIALIZED VIEW CONCURRENTLY mouse_full')
            conn.commit()
            print('Refresh complete.')


def main():
    dsn = dsn_from_env()
    print('Connecting to', dsn)
    conn = psycopg2.connect(dsn)
    try:
        run_schema(conn)
        refresh_materialized_view(conn)
        print('Schema initialization done.')
    finally:
        conn.close()

if __name__ == '__main__':
    main()
