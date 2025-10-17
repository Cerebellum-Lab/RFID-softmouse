#!/usr/bin/env python
"""Dummy / disposable ExperimentDB exercise script.

Creates a temporary root directory (and optional mirror) to test:
  - ensure_mouse
  - start_session (live-only + recorded)
  - finalize_session
  - list_sessions_for_mouse
  - unsynced_sessions + push_unsynced (remote sync placeholder)
  - mark_synced via push_unsynced

Removal:
  By default the temporary directory is deleted on successful completion.
  Use --keep to retain it for inspection.

Examples (PowerShell / CMD):
  python db/dummy_db_demo.py                  # auto-generate RFID
  python db/dummy_db_demo.py --rfid 123456789012345
  python db/dummy_db_demo.py --mirror --keep

You can inspect the resulting SQLite file with any SQLite browser.
"""
from __future__ import annotations
import argparse, os, json, random, string, sys, time, shutil, tempfile, datetime as dt
from pathlib import Path

# Local imports
from experiment_db import ExperimentDB, RemoteSyncClient, push_unsynced, DB_NAME  # type: ignore

TAG_LEN = 15

def gen_rfid() -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=TAG_LEN))

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Disposable ExperimentDB test harness")
    ap.add_argument('--rfid', help=f'RFID tag ({TAG_LEN} alnum); auto-generated if omitted')
    ap.add_argument('--mirror', action='store_true', help='Create a mirror temp directory to exercise mirroring logic')
    ap.add_argument('--keep', action='store_true', help='Keep temp directories (do NOT delete)')
    ap.add_argument('--sessions', type=int, default=2, help='Number of test sessions to create (default 2)')
    ap.add_argument('--live-only', action='store_true', help='Mark all sessions as live-only (no session_dir)')
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    rfid = args.rfid or gen_rfid()
    rfid = ''.join(ch for ch in rfid if ch.isalnum())
    if len(rfid) != TAG_LEN:
        print(f"ERROR: RFID must be {TAG_LEN} alphanumeric chars (got '{rfid}')")
        return 2

    root_dir = Path(tempfile.mkdtemp(prefix='expdb_root_'))
    mirror_dir = Path(tempfile.mkdtemp(prefix='expdb_mirror_')) if args.mirror else None
    print(f"Root dir:   {root_dir}")
    if mirror_dir:
        print(f"Mirror dir: {mirror_dir}")

    db = ExperimentDB(str(root_dir), mirror_dir=str(mirror_dir) if mirror_dir else None)
    print(f"Primary DB path: {db.db_path}")
    if mirror_dir:
        print(f"Mirror DB path:  {mirror_dir / DB_NAME}")

    # 1. ensure_mouse
    softmouse_payload = {
        'Name': 'TestMouse',
        'Sex': random.choice(['M','F']),
        'DOB': '2024-01-01',
        'Notes': 'Synthetic payload for DB dummy test.'
    }
    db.ensure_mouse(rfid, softmouse_payload=softmouse_payload)
    print(f"ensure_mouse OK for RFID {rfid}")

    session_ids = []
    # 2. start_session loop
    for i in range(args.sessions):
        prerecord_ctx = {
            'operator': 'tester',
            'seq': i + 1,
            'started_at_local': dt.datetime.now().isoformat(timespec='seconds')
        }
        session_dir = None
        yaml_path = None
        if not args.live_only:
            session_dir = str(root_dir / f"raw_session_{i+1:02d}")
            os.makedirs(session_dir, exist_ok=True)
            # fake yaml metadata file
            yaml_path = str(Path(session_dir) / 'session_meta.yaml')
            with open(yaml_path, 'w', encoding='utf-8') as fh:
                fh.write('session: dummy\n')
        sid = db.start_session(rfid, prerecord_ctx, was_live_only=args.live_only, session_dir=session_dir, metadata_yaml_path=yaml_path)
        session_ids.append(sid)
        print(f"Started session {sid} (live_only={args.live_only})")
        # Simulate activity
        time.sleep(0.1)
        post_ctx = {
            'trials_completed': random.randint(5, 25),
            'errors': random.randint(0, 3)
        }
        session_notes = {
            'observer': 'dummy',
            'comments': 'Synthetic end-of-session notes.'
        }
        db.finalize_session(sid, post_ctx, session_notes=session_notes)
        print(f"Finalized session {sid}")

    # 3. list sessions for mouse
    sessions = db.list_sessions_for_mouse(rfid, limit=10)
    print(f"list_sessions_for_mouse returned {len(sessions)} sessions")
    for s in sessions:
        print('  -', s['id'], 'start:', s['start_utc'], 'stop:', s['stop_utc'], 'live_only:', s['was_live_only'])

    # 4. unsynced + remote sync simulation
    unsynced = db.unsynced_sessions()
    print(f"Unsynced sessions before push: {len(unsynced)}")
    if unsynced:
        remote = RemoteSyncClient()  # placeholder
        pushed_ct = push_unsynced(db, remote)
        print(f"push_unsynced returned {pushed_ct} (marked synced)")
    unsynced_after = db.unsynced_sessions()
    print(f"Unsynced sessions after push: {len(unsynced_after)}")

    # 5. Mirror verification (simple size check)
    if mirror_dir:
        try:
            primary_size = Path(db.db_path).stat().st_size
            mirror_size = (mirror_dir / DB_NAME).stat().st_size
            print(f"Mirror size check: primary={primary_size} bytes mirror={mirror_size} bytes")
        except Exception as e:
            print(f"Mirror check failed: {e}")

    # Optionally keep or remove directories
    if args.keep:
        print("--keep supplied: NOT removing temp directories.")
    else:
        try:
            shutil.rmtree(root_dir)
            if mirror_dir:
                shutil.rmtree(mirror_dir)
            print("Temporary directories removed.")
        except Exception as e:
            print(f"Failed cleaning up temp dirs: {e}")

    print("Dummy DB test complete.")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
