"""Fixed fallback lines used when the AI model is down (Part 6).

Every template only uses facts it is given, so the fact rule holds even without the model.
"""
import random

from .. import station

BREAK_LINES = [
    "ஒரு சின்ன break — உடனே திரும்ப வர்றேன், stay tuned!",
    "Quick break எடுத்துக்கலாம் — don't go anywhere, more music வருது.",
    "சின்ன pause — அப்புறம் இன்னும் நிறைய songs!",
]

ENTERTAINMENT_LINES = [
    ("சில பாடல்கள் first time கேக்கும்போது ஒன்னும் feel ஆகாது, ஆனா fifth time கேக்கும்போது அதுதான் "
     "நம்ம emotion-ஐ pull பண்ணுது. ஒரே tune — traffic-ல, birthday party-ல, long drive-ல — same notes, "
     "different stories. அதான் music-ஓட magic. "),
    ("Music ஒரு time machine மாதிரி. ஒரே note கேக்கும்போது school days, college days, first job — எல்லாம் "
     "flash ஆகுது. So அடுத்த முறை ஒரு old song வரும்போது, கண்ணை மூடி அந்த memory-க்கு ஒரு thanks சொல்லுங்க. "),
]


def _song_phrase(song: dict | None) -> str:
    if not song:
        return ""
    title = song.get("title_ta") or song.get("title")
    film = song.get("film_ta") or song.get("film")
    return f"{film} படத்திலிருந்து {title}" if film else title


def _roundup(facts: dict) -> str:
    shouts = facts.get("listener_shoutouts") or []
    if not shouts:
        return ""
    names = []
    for s in shouts:
        n = s.get("name", "")
        if s.get("area"):
            n = f"{s['area']}-ல இருந்து {n}"
        names.append(n)
    return " WhatsApp-ல message அனுப்பின " + ", ".join(names) + " — உங்க எல்லாருக்கும் Kuyil-ஓட big thanks!"


def render(segment: str, facts: dict) -> str:
    if segment == "top_of_hour":
        when = facts.get("time_spoken", "")
        return f"{when} — நீங்க கேக்குறது {station.STATION_NAME}, உங்களோட Kuyil. {station.SLOGAN_TA}"

    if segment == "song_link":
        parts = []
        prev = _song_phrase(facts.get("previous_song"))
        if prev:
            parts.append(f"அது {prev}.")
        shouts = facts.get("listener_shoutouts") or []
        if shouts:
            s = shouts[0]
            who = f"{s['area']}-ல இருந்து {s['name']}" if s.get("area") else s["name"]
            parts.append(f"{who}, உங்க message-க்கு thanks!")
        nxt = _song_phrase(facts.get("next_song"))
        parts.append(f"அடுத்தது {nxt}." if nxt else "இன்னொரு super பாடல் coming up — stay tuned!")
        return " ".join(parts)

    if segment == "break_intro":
        return random.choice(BREAK_LINES)

    if segment == "feature_weather":
        w = facts.get("weather") or {}
        if w.get("temp_c") is None:
            return ""
        line = f"Dubai weather update — இப்போ வெப்பநிலை {round(w['temp_c'])} degree. "
        line += "மழை வாய்ப்பு இருக்கு — கவனமா இருங்க." if w.get("raining") else "வானம் தெளிவா இருக்கு."
        return line + _roundup(facts)

    if segment == "feature_metals":
        m = facts.get("metals") or {}
        if not m.get("gold_aed_per_gram_24k"):
            return ""
        line = (f"Gold update — 24 carat gold ஒரு gram சுமார் {round(m['gold_aed_per_gram_24k'])} dirham"
                + (f", silver ஒரு gram சுமார் {m['silver_aed_per_gram']:.1f} dirham." if m.get("silver_aed_per_gram") else "."))
        return line + _roundup(facts)

    if segment == "feature_traffic":
        t = facts.get("traffic") or {}
        incs = t.get("incidents") or []
        if not incs:
            return "Road update — Dubai roads இப்போ calm-ஆ தான் இருக்கு. Safe-ஆ drive பண்ணுங்க!" + _roundup(facts)
        i = incs[0]
        return (f"Road update — {i['where']} பக்கம் {i['kind']}"
                + (f", சுமார் {i['delay_min']} நிமிட delay." if i.get("delay_min") else ".")
                + " Safe-ஆ drive பண்ணுங்க!" + _roundup(facts))

    if segment == "feature_entertainment":
        return random.choice(ENTERTAINMENT_LINES) + _roundup(facts) + " " + station.SLOGAN_TA

    if segment == "feature_news":
        # Headlines are English and unfiltered; without the model we can't make them safe.
        return ""

    return ""
