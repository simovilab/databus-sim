"""Lake watcher — watchdog on parquet output directory."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..config import Config

logger = logging.getLogger(__name__)


@dataclass
class ParquetFile:
    path: Path
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class LakeWatcher:
    def __init__(self, config: Config) -> None:
        self._lake_dir = Path(config.lake_dir)
        self._files: list[ParquetFile] = []
        self._lock = threading.Lock()
        self._observer: Observer | None = None
        self._listeners: list[Callable[[ParquetFile], None]] = []

    def start(self) -> None:
        if not self._lake_dir.exists():
            logger.warning("Lake dir %s does not exist — LakeWatcher inactive", self._lake_dir)
            return
        handler = _Handler(self._on_file)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._lake_dir), recursive=True)
        self._observer.start()
        logger.debug("LakeWatcher watching %s", self._lake_dir)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()

    def files_since(self, ts: datetime) -> list[ParquetFile]:
        with self._lock:
            return [f for f in self._files if f.detected_at >= ts]

    def wait_for_parquet(self, *, since: datetime, timeout_ms: int, path_contains: str = "") -> ParquetFile | None:
        deadline = since.timestamp() + timeout_ms / 1000
        while time.time() < deadline:
            with self._lock:
                for f in self._files:
                    if f.detected_at >= since and path_contains in str(f.path):
                        return f
            time.sleep(0.1)
        return None

    def add_listener(self, fn: Callable[[ParquetFile], None]) -> None:
        self._listeners.append(fn)

    def _on_file(self, path: Path) -> None:
        pf = ParquetFile(path=path)
        with self._lock:
            self._files.append(pf)
        for fn in self._listeners:
            try:
                fn(pf)
            except Exception as exc:
                logger.warning("LakeWatcher listener error: %s", exc)


class _Handler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[Path], None]) -> None:
        self._callback = callback

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and str(event.src_path).endswith(".parquet"):
            self._callback(Path(str(event.src_path)))
