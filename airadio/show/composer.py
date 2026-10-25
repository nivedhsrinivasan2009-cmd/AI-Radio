"""Render one slot: plan -> script -> voice -> broadcast finishing -> length check -> WAV file.

Length rule (5a): measure the real audio; if too long, regenerate once asking for shorter;
if still too long, cut at a sentence boundary. Too short is fine.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import config
from ..brain import text as ttext
from ..brain import writer
from ..voice import audio
from ..voice.tts import Voice
from . import slots
from .planner import Plan

log = logging.getLogger("airadio.composer")


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    wav_path: Path
    script: str
    seconds: float
    source: str        # "llm" or "template"
    attempts: int


def _speak(voice: Voice, script: str):
    return audio.finish(voice.synthesize(script, lang="ta"))


def _cut_to_fit(script: str, seconds_now: float, max_sec: float) -> str:
    """Drop whole sentences from the end until the estimated length fits."""
    sentences = ttext.split_sentences(script)
    if len(sentences) <= 1:
        return script
    chars_per_sec = len(script) / max(seconds_now, 0.1)
    budget = max_sec * chars_per_sec * 0.95
    kept, used = [], 0
    for s in sentences:
        if kept and used + len(s) > budget:
            break
        kept.append(s)
        used += len(s) + 1
    return " ".join(kept)


def render(plan: Plan, slot_type: str, voice: Voice, out_path: Path) -> RenderResult:
    max_sec = slots.max_seconds(slot_type)
    script = writer.write_segment(plan.segment, plan.facts)
    if not script.text:
        raise RenderError(f"{plan.segment}: no script (model down and no template for this content)")
    seg = _speak(voice, script.text)
    attempts = 1
    text_now = script.text

    if audio.seconds(seg) > max_sec and script.source == "llm":
        log.info("%s: %.1fs > %.0fs, asking for a shorter version", plan.segment, audio.seconds(seg), max_sec)
        shorter = writer.write_segment(plan.segment, plan.facts, shorter=True)
        if shorter.text:
            seg2 = _speak(voice, shorter.text)
            attempts += 1
            if audio.seconds(seg2) < audio.seconds(seg):
                seg, text_now, script = seg2, shorter.text, shorter

    if audio.seconds(seg) > max_sec:
        cut = _cut_to_fit(text_now, audio.seconds(seg), max_sec)
        if cut != text_now:
            log.info("%s: still %.1fs, cutting at a sentence boundary", plan.segment, audio.seconds(seg))
            seg = _speak(voice, cut)
            text_now = cut
            attempts += 1
        if audio.seconds(seg) > max_sec:
            log.warning("%s: ends up %.1fs (limit %.0fs) — delivering anyway", plan.segment, audio.seconds(seg), max_sec)

    if audio.seconds(seg) < 1.0:
        raise RenderError(f"{plan.segment}: audio too short ({audio.seconds(seg):.2f}s)")

    audio.write_wav(seg, out_path)
    out_path.with_suffix(".txt").write_text(text_now, encoding="utf-8")
    return RenderResult(out_path, text_now, audio.seconds(seg), script.source, attempts)


def archive_path(cart: str, kind: str, when: datetime) -> Path:
    """output/archive/2026-10-20/AI2203_fresh_225812.wav — every render is kept for review."""
    return config.ARCHIVE_DIR / f"{when:%Y-%m-%d}" / f"{cart}_{kind}_{when:%H%M%S}.wav"
