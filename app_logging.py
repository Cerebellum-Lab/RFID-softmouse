"""Central logging setup with rotating file handler.

Usage:
    from app_logging import get_logger
    log = get_logger(__name__)
    log.info("message")

Log file: logs/app.log (rotates at ~5MB, keeps 5 backups)
"""
from __future__ import annotations
import logging, logging.handlers, os, pathlib

LOG_DIR = pathlib.Path('logs')
LOG_DIR.mkdir(exist_ok=True)
LOG_PATH = LOG_DIR / 'app.log'

_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'
_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

_handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=5, encoding='utf-8')
_handler.setFormatter(logging.Formatter(_FORMAT))

_root = logging.getLogger('rfidsoftmouse')
if not _root.handlers:
    _root.setLevel(_LEVEL)
    _root.addHandler(_handler)
    # Also echo to stdout for dev
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(_FORMAT))
    _root.addHandler(stream)


from typing import Optional

def get_logger(name = None) -> logging.Logger:  # name: Optional[str]
    if name and name.startswith('rfidsoftmouse.'):
        return logging.getLogger(name)
    if name:
        return logging.getLogger(f'rfidsoftmouse.{name}')
    return _root
