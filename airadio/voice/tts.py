"""Text-to-speech connector. gTTS is the free default; a paid neural voice can be added later
by writing another class with the same `synthesize` method and selecting it in get_voice()."""
import io
import logging
import os
import time
from typing import Protocol

from pydub import AudioSegment

from ..brain.text import strip_non_ta_en

log = logging.getLogger("airadio.tts")


class TTSError(RuntimeError):
    pass


class Voice(Protocol):
    name: str

    def synthesize(self, text: str, lang: str = "ta") -> AudioSegment: ...


class GTTSVoice:
    """Google Translate's free TTS. Needs internet; robotic but free."""
    name = "gtts"

    def __init__(self, retries: int = 3):
        self.retries = retries

    def synthesize(self, text: str, lang: str = "ta") -> AudioSegment:
        from gtts import gTTS
        text = strip_non_ta_en(text) if lang != "en" else text
        if not text.strip():
            raise TTSError("empty text")
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                buf = io.BytesIO()
                gTTS(text=text, lang=lang).write_to_fp(buf)  # gTTS splits long text itself
                buf.seek(0)
                return AudioSegment.from_file(buf, format="mp3")
            except Exception as e:
                last = e
                log.warning("gTTS attempt %d failed: %s", attempt, e)
                time.sleep(1.5 * attempt)
        raise TTSError(f"gTTS failed: {last}")


def get_voice() -> Voice:
    name = os.getenv("TTS_ENGINE", "gtts").lower()
    if name == "gtts":
        return GTTSVoice()
    raise ValueError(f"unknown TTS_ENGINE {name!r}")
