"""Simple local JSONL-backed patch queue for write-back operations.

Each patch is an instruction describing a desired mutation in SoftMouse terms.
We don't directly write to SoftMouse here; another job will process the queue.

Patch schema (example):
{
    "op": "update_mouse",
    "rfid": "ABC123",
    "changes": {"cage_id": "C-120"},
    "created_at": "2025-09-12T12:00:00Z",
    "status": "pending",            # pending | processing | done | error
    "processed_at": null,            # timestamp when done/error
    "error": null                    # error message if status=error
}

Usage:
  python writeback_queue.py enqueue --op update_mouse --rfid ABC123 --change cage_id=C-120
  python writeback_queue.py list
"""
from __future__ import annotations
import argparse, json, pathlib, datetime
from typing import Dict, Any, List, Iterable

QUEUE_FILE = pathlib.Path('writeback_queue.jsonl')


def utcnow():
    return datetime.datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def enqueue(op: str, rfid: str, changes: Dict[str, Any]):
    rec = {"op": op, "rfid": rfid, "changes": changes, "created_at": utcnow(), "status": "pending", "processed_at": None, "error": None}
    with QUEUE_FILE.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec) + '\n')
    return rec


def load_all() -> List[Dict[str,Any]]:
    if not QUEUE_FILE.exists():
        return []
    out = []
    with QUEUE_FILE.open('r', encoding='utf-8') as f:
        for line in f:
            line=line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out

def write_all(recs: Iterable[Dict[str,Any]]):
    tmp = QUEUE_FILE.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r) + '\n')
    tmp.replace(QUEUE_FILE)

def mark_processed(rfid: str, op: str, status: str, error: str | None = None):
    recs = load_all()
    changed = False
    now = utcnow()
    for r in recs:
        if r.get('rfid') == rfid and r.get('op') == op and r.get('status') in ('pending','processing'):
            r['status'] = status
            r['processed_at'] = now
            if error:
                r['error'] = error
            changed = True
            break
    if changed:
        write_all(recs)
    return changed


def parse_changes(pairs: list[str]) -> Dict[str,Any]:
    changes = {}
    for p in pairs:
        if '=' not in p:
            raise SystemExit(f'Invalid change spec: {p} (expected key=value)')
        k,v = p.split('=',1)
        changes[k] = v
    return changes


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd')
    ep = sub.add_parser('enqueue')
    ep.add_argument('--op', required=True)
    ep.add_argument('--rfid', required=True)
    ep.add_argument('--change', action='append', default=[], help='key=value (repeatable)')

    sub.add_parser('list')
    mp = sub.add_parser('mark')
    mp.add_argument('--op', required=True)
    mp.add_argument('--rfid', required=True)
    mp.add_argument('--status', required=True, choices=['pending','processing','done','error'])
    mp.add_argument('--error', required=False)

    args = ap.parse_args(argv)
    if args.cmd == 'enqueue':
        rec = enqueue(args.op, args.rfid, parse_changes(args.change))
        print('Enqueued:', json.dumps(rec))
    elif args.cmd == 'list':
        for r in load_all():
            print(json.dumps(r))
    elif args.cmd == 'mark':
        ok = mark_processed(args.rfid, args.op, args.status, args.error)
        print('Updated' if ok else 'No matching record')
    else:
        ap.print_help()

if __name__ == '__main__':
    main()
