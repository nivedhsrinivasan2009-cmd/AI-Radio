"""The show runner — the orchestrator (Parts 3, 4, 6).

Timeline of one show:
  1. Prime: any AI cart with no audio gets the safe default clip.
  2. Drafts: ~20 min before the first slot, render every slot from log + feeds only, and
     deliver straight away. Every cart now holds something sensible.
  3. Fresh: for each slot, when ~3–5 minutes of audio remain before it, re-render with the
     latest WhatsApp shout-outs and replace the draft. If the fresh render fails or is late,
     the draft airs.
  4. Lock: no replacing within 60 s of air time.
  5. After the show: every AI cart is reset to the safe default clip.

We are never in the station's audio path: if this process dies, AudioVAULT keeps playing
and the pre-rendered drafts carry the night.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from .. import config, station
from ..connectors.delivery import FolderDelivery
from ..connectors.songs import SongTable
from ..connectors.timeline import LogEvent, Timeline
from ..listeners import notify
from ..listeners.notify import Alerter
from ..listeners.shoutouts import ShoutoutPool
from ..voice import audio
from ..voice.tts import Voice
from . import composer, planner

log = logging.getLogger("airadio.runner")

ON_AIR_MESSAGE_TA = ("🎙️ இப்போதான் Kuyil உங்களை 89.4 Tamil FM-ல mention பண்ணாங்க! "
                     "கேட்டதுக்கு நன்றி 💛")


def ensure_safe_default(voice: Voice, path: Path = None) -> Path:
    path = Path(path or config.SAFE_DEFAULT_PATH)
    if not path.exists():
        log.info("creating safe default clip at %s", path)
        seg = audio.finish(voice.synthesize(station.SAFE_DEFAULT_TEXT_TA, lang="ta"))
        audio.write_wav(seg, path)
    return path


class ShowRunner:
    def __init__(self, timeline: Timeline, songs: SongTable, pool: ShoutoutPool, delivery: FolderDelivery,
                 voice: Voice, *, now_playing=None, alerter: Alerter | None = None,
                 now=datetime.now, sleep=time.sleep, safe_default: Path | None = None):
        self.tl = timeline
        self.songs = songs
        self.pool = pool
        self.delivery = delivery
        self.voice = voice
        self.now_playing = now_playing
        self.alerter = alerter or Alerter()
        self.now = now
        self.sleep = sleep
        self.safe_default = Path(safe_default or config.SAFE_DEFAULT_PATH)
        self.stop_event = threading.Event()
        self.drift = timedelta(0)
        self.consecutive_fresh_failures = 0
        self.status: dict[str, str] = {}       # cart -> "default" | "draft" | "fresh"
        self.records: dict[str, dict] = {}     # cart -> what's in it now: file, kind, shout-out ids
        self.confirmed_on_air: dict[str, datetime] = {}
        self._aired: set[tuple[str, str]] = set()
        self._pool_exec = ThreadPoolExecutor(max_workers=2, thread_name_prefix="render")

    # ------------------------------------------------------------------ timing
    def expected_start(self, ev: LogEvent) -> datetime:
        return ev.start + self.drift

    def lock_time(self, slot: LogEvent) -> datetime:
        return self.expected_start(slot) - timedelta(seconds=config.LOCK_BEFORE_AIR_SEC)

    def fresh_time(self, slot: LogEvent) -> datetime:
        """Start of the latest item before the slot that still leaves >= FRESH_MIN_LEAD of audio."""
        target = self.expected_start(slot) - timedelta(seconds=config.FRESH_MIN_LEAD_SEC)
        for ev in reversed(self.tl.events[:slot.index]):
            if self.expected_start(ev) <= target:
                return self.expected_start(ev)
        return self.expected_start(slot) - timedelta(seconds=config.FRESH_FALLBACK_LEAD_SEC)

    def draft_time(self) -> datetime:
        return self.expected_start(self.tl.ai_slots()[0]) - timedelta(minutes=config.DRAFT_LEAD_MIN)

    def _poll_now_playing(self) -> None:
        if not self.now_playing:
            return
        np = self.now_playing.current()
        if not np:
            return
        ev = self.tl.find_cart(np.cart, near=np.started_at)
        if not ev:
            return
        new_drift = np.started_at - ev.start
        if abs((new_drift - self.drift).total_seconds()) >= 5:
            log.info("now-playing: %s started %+.0fs vs plan — shifting timing", ev.cart, new_drift.total_seconds())
            self.drift = new_drift
        key = (ev.cart, ev.start.isoformat())
        if ev.is_ai and key not in self._aired:
            self._aired.add(key)
            log.info("AIRED %s (%s, %s)", ev.cart, ev.slot_type, self.status.get(ev.cart, "?"))

    def _wait_until(self, when: datetime) -> bool:
        """Sleep until `when`. Returns False if the runner was stopped."""
        while not self.stop_event.is_set():
            self._poll_now_playing()
            remaining = (when - self.now()).total_seconds()
            if remaining <= 0:
                return True
            self.sleep(min(remaining, 2.0))
        return False

    # ------------------------------------------------------------------ actions
    def _deliver(self, cart: str, wav: Path, kind: str, shoutout_ids: list[str] | None = None) -> None:
        self.delivery.deliver(cart, wav)
        self.status[cart] = kind
        self.records[cart] = {"wav": Path(wav), "kind": kind, "shoutout_ids": list(shoutout_ids or [])}

    def prime_carts(self) -> None:
        for slot in self.tl.ai_slots():
            path = self.delivery.cart_path(slot.cart)
            if not path.exists():
                self._deliver(slot.cart, self.safe_default, "default")
            else:
                self.records.setdefault(slot.cart, {"wav": path, "kind": "existing", "shoutout_ids": []})

    def reset_carts(self) -> None:
        for slot in self.tl.ai_slots():
            try:
                self._deliver(slot.cart, self.safe_default, "default")
            except Exception as e:
                self.alerter.alert("reset", f"could not reset {slot.cart} to the safe clip: {e}")
        log.info("all %d AI carts reset to the safe default clip", len(self.tl.ai_slots()))

    def _render(self, slot: LogEvent, kind: str) -> tuple[composer.RenderResult, planner.Plan]:
        plan = planner.plan_slot(slot, self.tl, self.songs, pool=self.pool, with_shoutouts=(kind == "fresh"))
        try:
            out = composer.archive_path(slot.cart, kind, self.now())
            result = composer.render(plan, slot.slot_type, self.voice, out)
            return result, plan
        except Exception:
            self.pool.release(plan.shoutout_ids)
            raise

    def render_drafts(self) -> int:
        failures = 0
        for slot in self.tl.ai_slots():
            if self.stop_event.is_set():
                break
            if self.now() >= self.lock_time(slot):
                continue
            try:
                result, _ = self._render(slot, "draft")
                if self.now() < self.lock_time(slot):
                    self._deliver(slot.cart, result.wav_path, "draft")
                    log.info("draft %s (%s) %.1fs [%s]: %s", slot.cart, slot.slot_type, result.seconds,
                             result.source, result.script[:90])
            except Exception as e:
                failures += 1
                log.exception("draft %s failed: %s", slot.cart, e)
        if failures:
            self.alerter.alert("drafts", f"{failures} of {len(self.tl.ai_slots())} draft segments failed before the show "
                                         f"(those carts keep the safe station-ID clip).")
        return failures

    def render_fresh(self, slot: LogEvent) -> bool:
        lock_at = self.lock_time(slot)
        if self.now() >= lock_at:
            log.info("fresh %s skipped — already locked", slot.cart)
            return False
        future = self._pool_exec.submit(self._render, slot, "fresh")
        while not future.done() and self.now() < lock_at and not self.stop_event.is_set():
            self._poll_now_playing()
            lock_at = self.lock_time(slot)
            self.sleep(0.5)
        if not future.done():
            log.warning("fresh %s not ready by lock time — the draft airs", slot.cart)
            future.add_done_callback(lambda f: self._release_late(f))
            self._fresh_failed(slot, "too slow")
            return False
        try:
            result, plan = future.result()
        except Exception as e:
            log.exception("fresh %s failed: %s", slot.cart, e)
            self._fresh_failed(slot, str(e))
            return False
        if self.now() >= self.lock_time(slot):
            self.pool.release(plan.shoutout_ids)
            self._fresh_failed(slot, "finished after lock")
            return False
        try:
            self._deliver(slot.cart, result.wav_path, "fresh", plan.shoutout_ids)
        except Exception as e:
            self.pool.release(plan.shoutout_ids)
            self._fresh_failed(slot, f"delivery: {e}")
            return False
        self.pool.commit(plan.shoutout_ids)
        self.consecutive_fresh_failures = 0
        log.info("fresh %s (%s) %.1fs [%s, %d shout-outs]: %s", slot.cart, slot.slot_type, result.seconds,
                 result.source, len(plan.shoutout_ids), result.script[:90])
        return True

    def _release_late(self, future) -> None:
        try:
            _, plan = future.result()
            self.pool.release(plan.shoutout_ids)
        except Exception:
            pass

    def _fresh_failed(self, slot: LogEvent, why: str) -> None:
        self.consecutive_fresh_failures += 1
        log.warning("fresh %s failed (%s); cart keeps its %s", slot.cart, why, self.status.get(slot.cart, "previous audio"))
        if self.consecutive_fresh_failures >= 2:
            self.alerter.alert("fresh", f"{self.consecutive_fresh_failures} fresh segments in a row failed "
                                        f"(latest {slot.cart}: {why}). Drafts are airing.")

    # ------------------------------------------------------------------ off-air monitor hooks
    def expected_on_air(self, now: datetime):
        """Kuyil segments due around now, and the exact file each cart holds (for the off-air monitor)."""
        from ..monitor.monitor import ExpectedClip
        clips = []
        for slot in self.tl.ai_slots():
            due = self.expected_start(slot)
            rec = self.records.get(slot.cart)
            if rec and due - timedelta(minutes=2) <= now <= due + timedelta(minutes=3, seconds=30):
                clips.append(ExpectedClip(key=f"{slot.cart}|{slot.start.isoformat()}", cart=slot.cart,
                                          expected_at=due, wav=rec["wav"], info=dict(rec)))
        return clips

    def on_aired(self, clip, when: datetime, score: float) -> None:
        self.confirmed_on_air[clip.key] = when
        ids = clip.info.get("shoutout_ids") or []
        if clip.info.get("kind") == "fresh" and ids:
            for item in self.pool.mark_notified(ids):
                notify.send_whatsapp(item["number"], ON_AIR_MESSAGE_TA)
                log.info("told %s their shout-out aired", item["name"])

    def on_missing(self, clip) -> None:
        self.alerter.alert("not-heard", f"{clip.cart} (due {clip.expected_at:%H:%M}) was not heard on air by the "
                                        f"off-air monitor. Check AudioVAULT picked up the file.")

    # ------------------------------------------------------------------ main loop
    def run(self) -> None:
        slots_ = self.tl.ai_slots()
        if not slots_:
            log.warning("no AI slots in the log — nothing to do")
            return
        if self.now() >= self.tl.end:
            log.warning("this show already ended at %s", self.tl.end)
            return
        log.info("show %s → %s, %d AI slots; drafts at %s", self.tl.start.strftime("%H:%M"),
                 self.tl.end.strftime("%H:%M"), len(slots_), self.draft_time().strftime("%H:%M:%S"))
        self.prime_carts()

        if not self._wait_until(self.draft_time()):
            return
        log.info("— rendering drafts —")
        self.render_drafts()

        for slot in slots_:
            if not self._wait_until(self.fresh_time(slot)):
                return
            self.render_fresh(slot)

        if not self._wait_until(self.tl.end + timedelta(seconds=60)):
            return
        log.info("— show over —")
        self.reset_carts()
        self._pool_exec.shutdown(wait=False)

    def stop(self) -> None:
        self.stop_event.set()

