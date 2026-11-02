import json
import os
from pathlib import Path
from threading import Lock


class RunStore:
    """Small durable fallback store used until the PostgreSQL service exists."""

    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = Lock()

    def _read(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def upsert(self, project_id: str, record: dict) -> None:
        with self._lock:
            records = self._read()
            records[project_id] = record
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(records, default=str), encoding="utf-8")
            os.replace(temporary, self.path)

    def get(self, project_id: str) -> dict | None:
        with self._lock:
            return self._read().get(project_id)

    def all(self) -> list[dict]:
        with self._lock:
            return list(self._read().values())