"""Patch application job (stub).

Reads patches from writeback_queue.jsonl, then (future):
  - Logs into SoftMouse via Playwright automation
  - Applies validated changes using SoftMouse web forms or import templates
  - Marks patches as applied or errored (extend queue format to include status)

Current placeholder simply lists pending patches.
"""
from __future__ import annotations
import json, pathlib
from writeback_queue import load_all, mark_processed

QUEUE_FILE = pathlib.Path('writeback_queue.jsonl')

def main(dry_run: bool = True):
    patches = [p for p in load_all() if p.get('status') == 'pending']
    if not patches:
        print('No pending patches.')
        return
    print(f'{len(patches)} pending patch(es):')
    for p in patches:
        print(json.dumps(p))
        if not dry_run:
            # Placeholder success path
            success = True
            if success:
                mark_processed(p['rfid'], p['op'], 'done')
            else:
                mark_processed(p['rfid'], p['op'], 'error', 'placeholder failure')
    if dry_run:
        print('\nDry run only. Re-run with PYTHONUNBUFFERED=1 python apply_patches_job.py run to mark processed.')
    else:
        print('Processing complete.')

if __name__ == '__main__':
    import sys
    dry = True
    if len(sys.argv) > 1 and sys.argv[1] == 'run':
        dry = False
    main(dry_run=dry)
