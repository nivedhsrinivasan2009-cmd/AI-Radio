"""Shared fakes: a controllable clock, an instant voice, and a scripted AI model."""
import json
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from pydub.generators import Sine

LOG_HEADER = "time,type,cart,title,artist,duration\n"


def write_log(rows: str, name="log_2026-10-20.csv") -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / name
    p.write_text(LOG_HEADER + rows.strip() + "\n", encoding="utf-8")
    return p


def write_songs(rows: list[dict]) -> Path:
    from airadio.connectors.songs import SongTable
    p = Path(tempfile.mkdtemp()) / "songs.csv"
    SongTable(rows, p).save()
    return p


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def sleep(self, sec: float) -> None:
        self.t += timedelta(seconds=sec)
        time.sleep(0.002)  # let worker threads run


class FakeVoice:
    """Speech length proportional to text length (~10 chars per second, like Tamil gTTS)."""
    name = "fake"

    def __init__(self, chars_per_sec: float = 10.0, fail: bool = False):
        self.cps = chars_per_sec
        self.fail = fail
        self.calls = 0

    def synthesize(self, text: str, lang: str = "ta"):
        self.calls += 1
        if self.fail:
            from airadio.voice.tts import TTSError
            raise TTSError("fake failure")
        ms = max(300, int(len(text) / self.cps * 1000))
        return Sine(440).to_audio_segment(duration=ms, volume=-20).set_frame_rate(24000).set_channels(1)


def fake_llm(lines=None, unsupported=None):
    """Patch the model: chat() returns lines in turn; chat_json() answers fact checks / safety checks."""
    lines = list(lines or ["அடுத்தது ஒரு super பாடல் வருது, stay tuned!"])
    state = {"i": 0}

    def chat(prompt, json_mode=False, temperature=0.8):
        out = lines[min(state["i"], len(lines) - 1)]
        state["i"] += 1
        return out

    def chat_json(prompt):
        if "unsupported" in prompt:
            return {"unsupported": list(unsupported or [])}
        return {"safe": True, "kind": "dedication", "summary": "says hi to her mother", "area": "Sharjah", "reason": ""}

    return mock.patch.multiple("airadio.brain.llm", chat=chat, chat_json=chat_json)


def no_loudness():
    """Skip the ffmpeg loudness measurement in fast tests."""
    return mock.patch("airadio.voice.audio.measure_lufs", return_value=-16.0)


def jdump(o) -> str:
    return json.dumps(o, ensure_ascii=False)
