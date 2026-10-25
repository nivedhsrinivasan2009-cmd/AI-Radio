"""Turn plain facts into Kuyil's on-air script (the only place words get written).

Every on-air script goes through:
  1. generation with the persona + segment prompt + FACTS,
  2. a script check (no Chinese/Arabic/etc. leaking in, not empty),
  3. a FACT CHECK: a second model call lists anything in the script that isn't in FACTS
     (invented listeners, songs, places, numbers). One retry with that feedback, then the
     fixed template is used instead — templates only ever use the given facts.
"""
import json
import logging
import re
from collections import deque
from dataclasses import dataclass

from . import llm, templates, text
from .persona import DJ_PERSONALITY, NO_SHOUTOUTS_NOTE, ROUNDUP_NOTE, SEGMENT_PROMPTS

log = logging.getLogger("airadio.writer")

# Last few on-air lines, so Kuyil doesn't repeat herself across the night.
_recent_lines: deque[str] = deque(maxlen=6)

SLOGAN_SEGMENTS = {"top_of_hour", "feature_entertainment"}
GENERATION_TEMPERATURE = 0.6

# Scripts gTTS-Tamil can't read and that signal a confused model: CJK, Hangul, Arabic, Devanagari, Cyrillic...
_FOREIGN_SCRIPT = re.compile(r"[Ѐ-ӿ؀-ۿऀ-ॿ぀-ヿ㐀-鿿가-힯＀-￯]")

ALWAYS_ALLOWED = "89.4 Tamil FM, Tamil, Dubai, UAE, Kuyil (குயில்), WhatsApp, the station slogan"


@dataclass
class Script:
    text: str
    source: str  # "llm" or "template"


def _facts_block(facts: dict) -> str:
    lines = []
    for k, v in facts.items():
        if v in (None, "", [], {}):
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines) if lines else "  (none)"


def build_prompt(segment: str, facts: dict, *, shorter: bool = False, avoid_terms: list[str] | None = None) -> str:
    cfg = SEGMENT_PROMPTS[segment]
    examples = "\n".join(f"  • {ex}" for ex in cfg["examples"])
    recent = "\n".join(f"  • {r}" for r in _recent_lines) or "  (none)"
    length = cfg["length"]
    if shorter:
        length += " IMPORTANT: your last attempt was too long — make this clearly SHORTER, around two thirds of that."
    notes = ROUNDUP_NOTE if facts.get("listener_shoutouts") else NO_SHOUTOUTS_NOTE
    if avoid_terms:
        notes += (" Your previous attempt mentioned things that are NOT in FACTS: "
                  + ", ".join(avoid_terms) + ". Do not mention them.")
    return (
        f"{DJ_PERSONALITY}\n"
        f"SEGMENT: {segment}\nTASK: {cfg['task']}\n{notes}\n"
        f"LENGTH TARGET: {length}\n"
        f"AVOID: {cfg['avoid']}\n\n"
        f"GOOD EXAMPLES (copy the voice only; their songs and numbers are just examples — never reuse them):\n{examples}\n\n"
        f"FACTS (the only facts you may use):\n{_facts_block(facts)}\n\n"
        f"RECENT ON-AIR LINES (don't repeat these):\n{recent}\n\n"
        f"Write the on-air line now. Tamil script for Tamil words, English words in English letters, nothing else."
    )


_VERIFY_PROMPT = """You check radio scripts before they go on air. The script may only state things found in FACTS.

FACTS:
{facts}

SCRIPT:
{script}

Check ONLY these kinds of items:
  - names of people (listeners, singers, music directors, actors)
  - place names
  - song titles and film titles
  - numbers (temperatures, prices, delays, years)
  - listener messages, requests or dedications
Do NOT list ordinary words, feelings, adjectives, filler or time-of-day words in any language
("magic", "super", "இப்போ", "melody", "stay tuned" are all fine).
Always allowed: {allowed}.
The same name in Tamil script and in English letters counts as the same. Rounded numbers (29 for 29.0) are fine.

Reply with JSON only: {{"unsupported": ["item", ...]}}  (empty list if everything is supported)"""

_SLOGAN_WORDS = set(re.findall(r"\w+", "89.4 Tamil FM தமிழ் எஃப்எம் நம்ம Radio Music ரேடியோ மியூசிக் Kuyil குயில் Dubai துபாய் UAE WhatsApp"))


# Places listeners commonly mention. Any of these in a script must also be in FACTS (the model likes to
# invent where a listener is "calling from"). Dubai is always allowed — it's the station's city.
_PLACES = {
    "Sharjah": ["sharjah", "ஷார்ஜா"], "Abu Dhabi": ["abu dhabi", "அபுதாபி", "அபு தாபி"],
    "Ajman": ["ajman", "அஜ்மான்"], "Al Ain": ["al ain", "அல் ஐன்"],
    "Ras Al Khaimah": ["ras al khaimah", "rak", "ராஸ் அல் கைமா"], "Fujairah": ["fujairah", "ஃபுஜைரா", "புஜைரா"],
    "Umm Al Quwain": ["umm al quwain", "உம் அல் குவைன்"], "Karama": ["karama", "கராமா"],
    "Deira": ["deira", "தேரா", "டேரா"], "Bur Dubai": ["bur dubai", "பர் துபாய்"], "Al Nahda": ["nahda", "நஹ்தா"],
    "Al Qusais": ["qusais", "குசைஸ்"], "Jumeirah": ["jumeirah", "ஜுமைரா"], "Mussafah": ["mussafah", "முசாஃபா"],
    "International City": ["international city"], "Silicon Oasis": ["silicon oasis"],
    "Chennai": ["chennai", "சென்னை"], "Madurai": ["madurai", "மதுரை"], "Coimbatore": ["coimbatore", "கோயம்புத்தூர்", "கோவை"],
    "Trichy": ["trichy", "திருச்சி"], "Kerala": ["kerala", "கேரளா"], "Jaffna": ["jaffna", "யாழ்ப்பாணம்"],
    "Sri Lanka": ["sri lanka", "இலங்கை"], "Singapore": ["singapore", "சிங்கப்பூர்"],
}

# Requests are acknowledge-only (Part 7): Kuyil must never promise to play one.
_PROMISE_TO_PLAY = re.compile(
    r"(போடணும்|போடுறேன்|போடறேன்|போடுவேன்|போடுவோம்|போட்டுடுறேன்|play\s*பண்(ணு|ண)?(றேன்|ரேன்|வேன்|வோம்|றோம்)|"
    r"will play|i'?ll play|we'?ll play|playing it next|coming up for you)",
    re.IGNORECASE,
)


def rule_violations(script: str, facts: dict) -> list[str]:
    """Hard checks done in code, because the model checker can miss them."""
    low_script = script.lower()
    facts_text = _facts_block(facts).lower()
    problems = []
    for place, spellings in _PLACES.items():
        # English spellings as whole words; Tamil as substrings (suffixes attach directly: சென்னையில).
        in_script = any((s in low_script) if re.search(r"[஀-௿]", s)
                        else re.search(rf"(?<![a-z]){re.escape(s)}(?![a-z])", low_script) for s in spellings)
        if in_script and not any(s in facts_text for s in spellings + [place.lower()]):
            problems.append(place)
    has_request = any(s.get("message_summary", "").startswith("asked for")
                      for s in facts.get("listener_shoutouts") or [] if isinstance(s, dict))
    if has_request and _PROMISE_TO_PLAY.search(script):
        problems.append("a promise to play the requested song (requests are acknowledged only)")
    return problems


def fact_check(script: str, facts: dict) -> list[str]:
    """Return the names/places/titles/numbers in the script that FACTS don't support (empty = OK)."""
    hard = rule_violations(script, facts)
    if hard:
        return hard
    data = llm.chat_json(_VERIFY_PROMPT.format(facts=_facts_block(facts), script=script, allowed=ALWAYS_ALLOWED))
    facts_text = _facts_block(facts).lower()
    flagged = []
    for item in data.get("unsupported") or []:
        item = str(item).strip()
        if not item:
            continue
        words = re.findall(r"\w+", item.lower())
        # Ignore false alarms: the item literally appears in FACTS, or it's only station/slogan words.
        if item.lower() in facts_text or (words and all(w in facts_text or w in {x.lower() for x in _SLOGAN_WORDS}
                                                       for w in words)):
            continue
        flagged.append(item[:60])
    return flagged[:8]


def _finish(segment: str, script: str) -> str:
    script = text.clean_script(script)
    if segment in SLOGAN_SEGMENTS:
        script = text.ensure_slogan(script)
    return script


def _generate(segment: str, facts: dict, *, shorter: bool, avoid: list[str] | None) -> str | None:
    """One generation, rejected if another writing system leaks in or it's too short."""
    for _ in range(2):
        raw = llm.chat(build_prompt(segment, facts, shorter=shorter, avoid_terms=avoid),
                       temperature=GENERATION_TEMPERATURE)
        if _FOREIGN_SCRIPT.search(raw):
            log.info("%s: output had foreign script, regenerating", segment)
            continue
        out = _finish(segment, raw)
        if len(out) >= 8 and text.tamil_ratio(out) >= 0.15:
            return out
        log.info("%s: output too short or barely Tamil (%r), regenerating", segment, out[:60])
    return None


def write_segment(segment: str, facts: dict, *, shorter: bool = False) -> Script:
    """Write one segment. Falls back to fixed templates if the model is down or keeps inventing facts."""
    try:
        avoid: list[str] = []
        for attempt in range(2):
            out = _generate(segment, facts, shorter=shorter, avoid=avoid or None)
            if out is None:
                break
            unsupported = fact_check(out, facts)
            if not unsupported:
                _recent_lines.append(out[:200])
                return Script(out, "llm")
            log.warning("%s: fact check flagged %s (attempt %d)", segment, unsupported, attempt + 1)
            avoid = unsupported
    except llm.LLMError as e:
        log.warning("%s: model unavailable (%s)", segment, e)
    except (ValueError, KeyError) as e:
        log.warning("%s: fact check unreadable (%s)", segment, e)
    log.info("%s: using the fixed template", segment)
    fallback = templates.render(segment, facts)
    return Script(_finish(segment, fallback) if fallback else "", "template")


def chat_reply(listener_text: str, listener_name: str | None) -> str:
    """WhatsApp text reply (not on air). Listener text is passed as quoted data."""
    facts = {"listener_name": listener_name or "", "listener_message": listener_text[:500]}
    try:
        prompt = build_prompt("freeform", facts).replace(NO_SHOUTOUTS_NOTE, "")
        reply = llm.chat(prompt, temperature=GENERATION_TEMPERATURE)
        reply = _FOREIGN_SCRIPT.sub("", reply)
        return text.clean_script(reply)[:900] or "🙂"
    except llm.LLMError:
        return "Kuyil இப்போ கொஞ்சம் busy — கொஞ்ச நேரம் கழிச்சு மீண்டும் message பண்ணுங்க! 🙏"
