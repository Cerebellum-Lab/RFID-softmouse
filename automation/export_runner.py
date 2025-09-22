"""Background export runner for SoftMouse animals list.

Spawns an asyncio task (in this process) to invoke the existing
export logic from softmouse_export_animals and returns a lightweight
summary via a multiprocessing queue.

Intended to be launched in a separate Process from the GUI to avoid
blocking wx main thread.
"""
from __future__ import annotations
import asyncio, traceback, multiprocessing as mp, json, os, sys, pathlib, time
from typing import Optional, Dict, Any

# Ensure root on path (when launched as child process)
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation.softmouse_export_animals import parse_cli, export_animals  # type: ignore


def _args_for(colony: str, fast: bool, headful: bool, state_file: str, download_dir: str) -> list[str]:
    argv = [
        '--colony-name', colony,
        '--state-file', state_file,
        '--download-dir', download_dir,
        '--parse',  # ensure parsing executed (DataFrame head printed inside)
        '--download-wait', '60',
    ]
    if fast:
        argv.append('--fast-animals')
    if headful:
        argv.append('--headful')
    # rely on existing stored state; no force-login by default
    return argv

async def _run_export(argv: list[str]) -> Dict[str, Any]:
    start = time.time()
    ns = parse_cli(argv)
    try:
        await export_animals(ns)
        # Determine latest file in download_dir
        dl_dir = pathlib.Path(ns.download_dir)
        latest: Optional[pathlib.Path] = None
        if dl_dir.exists():
            for f in dl_dir.glob('*.*'):
                if f.is_file() and f.suffix.lower() in ('.csv', '.xlsx'):
                    if (latest is None) or f.stat().st_mtime > latest.stat().st_mtime:
                        latest = f
        return {
            'ok': True,
            'elapsed': round(time.time()-start, 2),
            'file': str(latest) if latest else None,
            'format': latest.suffix if latest else None,
        }
    except Exception as e:  # pragma: no cover
        return {
            'ok': False,
            'error': str(e),
            'trace': traceback.format_exc(limit=8),
        }

def run_export(colony: str, fast: bool, headful: bool, state_file: str, download_dir: str, q: mp.Queue):
    """Entry point executed in a child process.

    Places exactly one dict onto queue describing result.
    """
    try:
        argv = _args_for(colony, fast, headful, state_file, download_dir)
        res = asyncio.run(_run_export(argv))
    except SystemExit as e:  # argparse may call sys.exit
        res = {'ok': False, 'error': f'SystemExit {e}'}
    except Exception as e:  # pragma: no cover
        res = {'ok': False, 'error': str(e), 'trace': traceback.format_exc(limit=8)}
    try:
        q.put(res)
    except Exception:
        pass

__all__ = ['run_export']
