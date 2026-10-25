"""Pool of listener messages that passed the safety filter and may be mentioned on air (5b-5).

Rules: freshest first, at most N per slot, one per listener per hour, expire after ~30 min.
A render *reserves* shout-outs; they become *used* only if that audio is actually delivered,
otherwise they are released back for the next slot.
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .. import config

log = logging.getLogger("airadio.shoutouts")


class ShoutoutPool:
    def __init__(self, path: Path | None = None, *, now=datetime.now):
        self.path = Path(path) if path else None
        self.now = now
        self._lock = threading.RLock()
        self.items: list[dict] = []
        self._load()

    # ---- persistence (so a restart mid-show doesn't lose messages) ----
    def _load(self) -> None:
        if self.path and self.path.exists():
            try:
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
                for it in self.items:
                    if it["status"] == "reserved":
                        it["status"] = "pending"
            except (json.JSONDecodeError, OSError, KeyError):
                self.items = []

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cutoff = (self.now() - timedelta(days=2)).isoformat()
        self.items = [i for i in self.items if i["received_at"] >= cutoff]
        self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- API ----
    def add(self, *, number: str, name: str, summary: str, kind: str, area: str = "") -> dict:
        item = {
            "id": uuid.uuid4().hex[:10], "number": number, "name": name, "area": area,
            "kind": kind, "summary": summary, "received_at": self.now().isoformat(timespec="seconds"),
            "status": "pending", "used_at": None,
        }
        with self._lock:
            self.items.append(item)
            self._save()
        log.info("shout-out queued from %s (%s): %s", name, kind, summary)
        return item

    def take(self, n: int) -> list[dict]:
        """Reserve up to n shout-outs for one render."""
        if n <= 0:
            return []
        now = self.now()
        expiry = (now - timedelta(minutes=config.SHOUTOUT_EXPIRY_MIN)).isoformat()
        hour_ago = (now - timedelta(hours=1)).isoformat()
        with self._lock:
            for it in self.items:
                if it["status"] == "pending" and it["received_at"] < expiry:
                    it["status"] = "expired"
            recently_on_air: dict[str, int] = {}
            for it in self.items:
                if it["status"] in ("used", "reserved") and (it["used_at"] or it["received_at"]) >= hour_ago:
                    recently_on_air[it["number"]] = recently_on_air.get(it["number"], 0) + 1
            chosen, numbers = [], set()
            for it in sorted((i for i in self.items if i["status"] == "pending"),
                             key=lambda i: i["received_at"], reverse=True):
                if it["number"] in numbers:
                    continue
                if recently_on_air.get(it["number"], 0) >= config.SHOUTOUT_PER_LISTENER_PER_HOUR:
                    continue
                it["status"] = "reserved"
                chosen.append(it)
                numbers.add(it["number"])
                if len(chosen) >= n:
                    break
            if chosen:
                self._save()
            return [dict(c) for c in chosen]

    def commit(self, ids: list[str]) -> None:
        self._set_status(ids, "used", used_at=self.now().isoformat(timespec="seconds"))

    def release(self, ids: list[str]) -> None:
        self._set_status(ids, "pending")

    def _set_status(self, ids: list[str], status: str, used_at: str | None = None) -> None:
        if not ids:
            return
        with self._lock:
            for it in self.items:
                if it["id"] in ids:
                    it["status"] = status
                    if used_at:
                        it["used_at"] = used_at
            self._save()

    def mark_notified(self, ids: list[str]) -> list[dict]:
        """Mark used shout-outs as 'listener told it aired'; returns only those not told before."""
        out = []
        with self._lock:
            for it in self.items:
                if it["id"] in ids and it["status"] == "used" and not it.get("notified_at"):
                    it["notified_at"] = self.now().isoformat(timespec="seconds")
                    out.append(dict(it))
            if out:
                self._save()
        return out

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for i in self.items if i["status"] == "pending")


def for_brain(items: list[dict]) -> list[dict]:
    """Only what the brain may see: first name, area, the safe paraphrase. Never raw text or numbers."""
    return [{"name": i["name"], **({"area": i["area"]} if i.get("area") else {}), "message_summary": i["summary"]}
            for i in items]
