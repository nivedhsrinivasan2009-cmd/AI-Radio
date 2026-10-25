"""Decide WHAT each slot should say: pick the segment and gather its plain facts.

Rules applied here:
  - Part 2: talk backward confidently, forward cautiously — don't name the next song in the
    last ~10 minutes before the top of the hour (songs there may get dropped).
  - Part 5c: a song not in the song table is never named.
  - Part 5a: break intros never name advertisers; features cycle through the content mix,
    skipping any feed that is down.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .. import config
from ..brain import text as ttext
from ..connectors.feeds import FeedError, get_feeds
from ..connectors.songs import SongTable
from ..connectors.timeline import LogEvent, Timeline
from ..listeners.safety import family_safe_headlines
from ..listeners.shoutouts import ShoutoutPool, for_brain
from . import slots

log = logging.getLogger("airadio.planner")


@dataclass
class Plan:
    segment: str                 # prompt template name, e.g. "song_link", "feature_weather"
    facts: dict
    shoutout_ids: list[str] = field(default_factory=list)


def feature_index(timeline: Timeline, slot: LogEvent) -> int:
    return sum(1 for e in timeline.ai_slots() if e.slot_type == "feature" and e.index < slot.index)


def _minutes_to_next_hour(t: datetime) -> float:
    nxt = slots.hour_of(t) + timedelta(hours=1)
    return (nxt - t).total_seconds() / 60


def _round_to_quarter(t: datetime) -> datetime:
    q = round((t.minute * 60 + t.second) / 900) * 15
    return slots.hour_of(t) + timedelta(minutes=q)


def plan_slot(slot: LogEvent, timeline: Timeline, songs: SongTable, *,
              pool: ShoutoutPool | None = None, with_shoutouts: bool = True) -> Plan:
    stype = slot.slot_type
    facts: dict = {"time_of_day": ttext.tod_word(slot.start)}

    if stype == "top_of_hour":
        facts["time_spoken"] = ttext.spoken_hour_ta(_round_to_quarter(slot.start))
        first_ai = timeline.ai_slots()[0]
        if slot.index == first_ai.index:
            facts["show_start"] = True
            facts["show_end_spoken"] = ttext.spoken_time_ta(_round_to_quarter(timeline.end))
        return Plan("top_of_hour", facts)

    if stype == "break_intro":
        facts["what_follows"] = "a commercial break (do not name any advertiser)"
        return Plan("break_intro", facts)

    if stype == "song_link":
        prev = timeline.previous_song(slot)
        prev_facts = songs.facts(prev.cart) if prev else None
        if prev_facts:
            facts["previous_song"] = prev_facts
        else:
            facts["previous_song"] = "not known — do not name it"
        nxt = timeline.next_song(slot)
        nxt_facts = songs.facts(nxt.cart) if nxt else None
        if nxt_facts and _minutes_to_next_hour(slot.start) > config.NO_NEXT_SONG_LAST_MIN:
            facts["next_song"] = nxt_facts
        else:
            facts["next_song"] = "do not name the next song — just tease that another great song is coming"
        plan = Plan("song_link", facts)
        _attach_shoutouts(plan, pool, config.SHOUTOUTS_PER_LINK if with_shoutouts else 0)
        return plan

    if stype == "feature":
        idx = feature_index(timeline, slot)
        feeds = get_feeds()
        for step in range(len(slots.FEATURE_CYCLE)):
            kind = slots.FEATURE_CYCLE[(idx + step) % len(slots.FEATURE_CYCLE)]
            if kind == "entertainment":
                prev = timeline.previous_song(slot)
                facts["previous_song"] = songs.facts(prev.cart) if prev else None
                plan = Plan("feature_entertainment", facts)
                break
            try:
                data = feeds[kind].get()
                if kind == "news":
                    safe = family_safe_headlines(data["headlines"])
                    if not safe:
                        raise FeedError("no family-safe headlines right now")
                    data = {"headlines": safe[:3]}
                facts[kind] = data
                plan = Plan(f"feature_{kind}", facts)
                break
            except FeedError as e:
                log.warning("feature slot %s: %s feed down (%s) — trying next content type", slot.cart, kind, e)
        _attach_shoutouts(plan, pool, config.SHOUTOUTS_PER_FEATURE if with_shoutouts else 0)
        return plan

    raise ValueError(f"unknown slot type {stype!r}")


def _attach_shoutouts(plan: Plan, pool: ShoutoutPool | None, n: int) -> None:
    if not pool or n <= 0:
        return
    taken = pool.take(n)
    if taken:
        plan.facts["listener_shoutouts"] = for_brain(taken)
        plan.shoutout_ids = [t["id"] for t in taken]
