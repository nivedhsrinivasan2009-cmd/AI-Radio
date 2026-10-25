"""Broadcast finishing for Kuyil's audio (Part 8).

We deliberately do very little: convert to broadcast WAV, trim silence, match loudness.
EQ and compression are left to the station's own processor (processing twice sounds worse).
"""
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from pydub import AudioSegment
from pydub.silence import detect_leading_silence

from .. import config

log = logging.getLogger("airadio.audio")

SILENCE_THRESH_DBFS = -50.0
EDGE_PAD_MS = 50     # keep a little air so soft consonants aren't clipped
FADE_MS = 5          # tiny fade so the cut doesn't click


def to_broadcast_format(seg: AudioSegment) -> AudioSegment:
    return (seg.set_frame_rate(config.AUDIO_SAMPLE_RATE)
               .set_channels(config.AUDIO_CHANNELS)
               .set_sample_width(config.AUDIO_SAMPLE_WIDTH))


def trim_silence(seg: AudioSegment) -> AudioSegment:
    start = detect_leading_silence(seg, silence_threshold=SILENCE_THRESH_DBFS)
    end = detect_leading_silence(seg.reverse(), silence_threshold=SILENCE_THRESH_DBFS)
    if start + end >= len(seg):
        return seg  # all silence; leave it for the caller to reject
    start = max(0, start - EDGE_PAD_MS)
    stop = min(len(seg), len(seg) - end + EDGE_PAD_MS)
    return seg[start:stop].fade_in(FADE_MS).fade_out(FADE_MS)


_LUFS_RE = re.compile(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS")


def measure_lufs_file(path: Path) -> float | None:
    """Integrated loudness (ITU-R BS.1770 / EBU R128) measured by ffmpeg's ebur128 filter."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        found = _LUFS_RE.findall(proc.stderr)
        return float(found[-1]) if found else None  # last match = the summary
    except Exception as e:
        log.warning("loudness measurement failed for %s: %s", path, e)
        return None


def measure_lufs(seg: AudioSegment) -> float | None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.wav"
        seg.export(p, format="wav").close()
        return measure_lufs_file(p)


def match_loudness(seg: AudioSegment, target_lufs: float | None = None) -> AudioSegment:
    target = config.TARGET_LUFS if target_lufs is None else target_lufs
    current = measure_lufs(seg)
    if current is None or current < -60:
        log.warning("could not measure loudness (%s); leaving level unchanged", current)
        return seg
    gain = target - current
    # Never push peaks above the ceiling (clipping = harsh distortion).
    headroom = config.PEAK_CEILING_DBFS - seg.max_dBFS
    if gain > headroom:
        log.info("loudness gain limited by peaks: wanted %+.1f dB, using %+.1f dB", gain, headroom)
        gain = headroom
    return seg.apply_gain(gain)


def finish(seg: AudioSegment) -> AudioSegment:
    """Raw TTS audio -> broadcast-ready clip."""
    seg = to_broadcast_format(seg)
    seg = trim_silence(seg)
    return match_loudness(seg)


def write_wav(seg: AudioSegment, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    seg.export(path, format="wav").close()


def seconds(seg: AudioSegment) -> float:
    return len(seg) / 1000.0
