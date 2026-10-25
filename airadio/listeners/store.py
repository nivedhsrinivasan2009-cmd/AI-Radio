"""Listener records in users.json (compatible with the file try.py writes)."""
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("airadio.store")

HISTORY_CAP = 30


class UserStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.users: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    self.users = json.loads(self.path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as e:
                    log.error("could not read %s (%s); starting empty", self.path, e)
                    self.users = {}
            for u in self.users.values():
                hist = u.get("history") or []
                u["history"] = hist[-HISTORY_CAP:]

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.users, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    def get(self, number: str) -> dict | None:
        with self._lock:
            return self.users.get(number)

    def create(self, number: str) -> dict:
        with self._lock:
            u = self.users.setdefault(number, {"name": None, "history": []})
            self.save()
            return u

    def update(self, number: str, **fields) -> dict:
        with self._lock:
            u = self.users.setdefault(number, {"name": None, "history": []})
            u.update(fields)
            self.save()
            return u

    def add_history(self, number: str, item: str) -> None:
        with self._lock:
            u = self.users.setdefault(number, {"name": None, "history": []})
            u.setdefault("history", []).append(item)
            u["history"] = u["history"][-HISTORY_CAP:]
            self.save()
