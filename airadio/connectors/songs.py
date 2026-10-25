"""Song info table (Part 5c): cart -> clean, speakable facts about each song.

CSV columns: cart,title,film,music_director,singers,title_ta,film_ta,music_director_ta,singers_ta,reviewed
Kuyil only says what is in this table. A song that isn't here is never named.
"""
import csv
import logging
from pathlib import Path

log = logging.getLogger("airadio.songs")

FIELDS = ["cart", "title", "film", "music_director", "singers",
          "title_ta", "film_ta", "music_director_ta", "singers_ta", "reviewed"]


class SongTable:
    def __init__(self, rows: list[dict] | None = None, path: Path | None = None):
        self.path = path
        self.rows = rows or []
        self._by_cart = {r["cart"]: r for r in self.rows if r.get("cart")}

    @classmethod
    def load(cls, path: Path) -> "SongTable":
        path = Path(path)
        if not path.exists():
            log.warning("song table %s not found — songs will not be named on air", path)
            return cls([], path)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]
        rows = [r for r in rows if r.get("cart") and r.get("title")]
        log.info("song table: %d songs from %s", len(rows), path.name)
        return cls(rows, path)

    def save(self, path: Path | None = None) -> None:
        path = Path(path or self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows Tamil correctly
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in self.rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})

    def get(self, cart: str) -> dict | None:
        return self._by_cart.get(cart)

    def facts(self, cart: str) -> dict | None:
        """Only the non-empty fields, ready to hand to the brain."""
        r = self.get(cart)
        if not r:
            return None
        return {k: v for k, v in r.items() if v and k not in ("cart", "reviewed")}

    def numbered(self) -> list[dict]:
        """Stable order for the WhatsApp numbered list."""
        return list(self.rows)

    def __len__(self) -> int:
        return len(self.rows)
