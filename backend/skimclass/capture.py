
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Dict

import mss

from .config import DATA_DIR
from . import db

CAPTURE_DIR = DATA_DIR / "captures"
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)


class CaptureWorker(threading.Thread):
    def __init__(self, session_id: str, interval_sec: int = 30) -> None:
        super().__init__(daemon=True)
        self.session_id = session_id
        self.interval_sec = interval_sec
        self._stop = threading.Event()

    def run(self) -> None:
        with mss.mss() as sct:
            while not self._stop.is_set():
                ts = datetime.utcnow().isoformat()
                session_dir = CAPTURE_DIR / self.session_id
                session_dir.mkdir(parents=True, exist_ok=True)
                filename = session_dir / f"{ts}.png"
                sct.shot(output=str(filename))
                db.add_capture(self.session_id, ts, str(filename))
                self._stop.wait(self.interval_sec)

    def stop(self) -> None:
        self._stop.set()


_workers: Dict[str, CaptureWorker] = {}


def start_capture(session_id: str, interval_sec: int) -> None:
    if session_id in _workers:
        return
    worker = CaptureWorker(session_id, interval_sec)
    _workers[session_id] = worker
    worker.start()


def stop_capture(session_id: str) -> None:
    worker = _workers.pop(session_id, None)
    if worker:
        worker.stop()
