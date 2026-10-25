"""WhatsApp webhook (Twilio -> Flask).

Two outputs per message (Part 5b):
  1. an instant text reply (as before), and
  2. for greetings / dedications / comments / requests: a safety-checked entry in the shout-out
     pool that Kuyil may mention on air. Commands never go on air.

Song requests are acknowledge-only (Part 7, option A): they never change the music log.
"""
import logging
import re
import threading
from datetime import datetime, timedelta

from flask import Flask, Response, request
from twilio.twiml.messaging_response import MessagingResponse

from .. import config, station
from ..brain import text as ttext
from ..brain import writer
from ..connectors.feeds import FeedError, get_feeds
from . import notify, safety
from .shoutouts import ShoutoutPool
from .store import UserStore

log = logging.getLogger("airadio.whatsapp")

LIST_PAGE_SIZE = 15
SEP = "— — —"


class WhatsAppBot:
    def __init__(self, store: UserStore, pool: ShoutoutPool, songs, *, timeline_provider=None,
                 now_playing=None, now=datetime.now, async_send=True):
        self.store, self.pool, self.songs = store, pool, songs
        self.timeline_provider = timeline_provider or (lambda: None)
        self.now_playing = now_playing
        self.now = now
        self.async_send = async_send

    # ------------------------------------------------------------------ entry
    def handle(self, number: str, body: str) -> str | None:
        """Return the instant reply text (None = reply is sent later in the background)."""
        body = (body or "").strip()
        low = body.lower()
        user = self.store.get(number)

        if user is None:
            self.store.create(number)
            return (f"👋 {station.STATION_NAME}-க்கு வரவேற்கிறோம்! நான் {station.DJ_HANDLE}.\n"
                    f"உங்க பெயர் என்ன?\n\n"
                    f"🎙️ குறிப்பு: உங்க message on-air-ல வரலாம் (முதல் பெயர் மட்டும்).")

        if user.get("name") is None:
            if low in ("hi", "hello", "hey", "hai", "வணக்கம்"):
                return f"{ttext.greeting_en(self.now())}! I'm {station.DJ_HANDLE}. What's your name?"
            name = body[:40]
            ok = safety.name_ok(name)
            self.store.update(number, name=name, name_ok=ok)
            return (f"சந்தோஷம் {safety.first_name(name) or name}! {station.DJ_HANDLE} பேசுறேன். 🎶\n"
                    f"பாடல் கேட்க 'list' type பண்ணுங்க, அப்புறம் ஒரு number அனுப்புங்க.\n"
                    f"Commands பார்க்க 'help'.")

        if "name_ok" not in user:  # listeners registered by the old try.py
            user = self.store.update(number, name_ok=safety.name_ok(user["name"]))

        if low in ("hi", "hello", "hey", "hai", "வணக்கம்"):
            return f"{ttext.greeting_en(self.now())}, {safety.first_name(user['name'])}! 'list' for songs, 'help' for commands."

        m = re.fullmatch(r"list(?:\s+(\d+))?", low)
        if m:
            return self._song_list(int(m.group(1) or 1))

        m = re.fullmatch(r"(?:song\s*|#)?(\d{1,3})[).]?", low)
        if m:
            return self._request(number, user, int(m.group(1)))

        direct = self._command(low, number, user)
        if direct is not None:
            return direct

        # Free text: chat reply (background, so a busy model can't time out Twilio's webhook)
        # and an on-air candidate check.
        threading.Thread(target=self._chat_and_screen, args=(number, user, body), daemon=True).start()
        return None

    # ------------------------------------------------------------------ songs
    def _song_list(self, page: int) -> str:
        rows = self.songs.numbered()
        if not rows:
            return "🎵 பாடல் பட்டியல் இப்போ கிடைக்கல — கொஞ்ச நேரம் கழிச்சு try பண்ணுங்க."
        pages = max(1, -(-len(rows) // LIST_PAGE_SIZE))
        page = min(max(1, page), pages)
        start = (page - 1) * LIST_PAGE_SIZE
        lines = []
        for i, r in enumerate(rows[start:start + LIST_PAGE_SIZE], start + 1):
            film = f" ({r['film']})" if r.get("film") else ""
            lines.append(f"{i}. {r['title']}{film}")
        more = f"\n\nஅடுத்த page: 'list {page + 1}'" if page < pages else ""
        return f"🎵 பாடல் பட்டியல் ({page}/{pages})\n{SEP}\n" + "\n".join(lines) + more + "\n\nஒரு number அனுப்புங்க!"

    def _request(self, number: str, user: dict, n: int) -> str:
        rows = self.songs.numbered()
        if not (1 <= n <= len(rows)):
            return "❌ அந்த number list-ல இல்லை. 'list' type பண்ணி பாருங்க."
        last = user.get("last_request_at")
        if last:
            wait = datetime.fromisoformat(last) + timedelta(minutes=config.REQUEST_COOLDOWN_MIN) - self.now()
            if wait.total_seconds() > 0:
                mins = int(wait.total_seconds() // 60) + 1
                return f"⏳ ஒரு request ஏற்கனவே note பண்ணியிருக்கோம். ~{mins} நிமிஷம் கழிச்சு இன்னொன்னு அனுப்புங்க."
        song = rows[n - 1]
        self.store.update(number, last_request_at=self.now().isoformat(timespec="seconds"))
        self.store.add_history(number, song["title"])
        if user.get("name_ok"):
            film = f" from {song['film']}" if song.get("film") else ""
            self.pool.add(number=number, name=safety.first_name(user["name"]), kind="request",
                          summary=f"asked for the song {song['title']}{film}", area=user.get("area", ""))
        return (f"🎵 Request noted — {song['title']}!\n"
                f"Kuyil on-air-ல உங்களை mention பண்ணலாம் 🎙️ (பாடல் play ஆகும்னு உறுதி இல்லை — "
                f"station playlist தான் decide பண்ணும்).")

    # ------------------------------------------------------------------ commands
    def _command(self, low: str, number: str, user: dict) -> str | None:
        if low in ("now", "np", "playing", "what's playing", "whats playing", "என்ன ஓடுது"):
            return self._now()
        if low in ("queue", "position", "eta"):
            return ("🎙️ Requests இப்போ queue-ல போகாது — Kuyil அவற்றை note பண்ணி on-air-ல mention பண்ணுவாங்க. "
                    "பாடல் station playlist-படி தான் play ஆகும்.")
        if low in ("weather", "w", "வானிலை"):
            return self._weather()
        if low in ("news", "n", "headlines", "செய்தி"):
            return self._news()
        if low in ("traffic", "roads", "ட்ராஃபிக்"):
            return self._traffic()
        if low in ("gold", "metals", "தங்கம்"):
            return self._gold()
        if low == "history":
            hist = user.get("history") or []
            return ("🎶 நீங்க கேட்ட பாடல்கள்: " + ", ".join(hist[-10:])) if hist else "🎶 இன்னும் request எதுவும் இல்லை."
        if low in ("purpose", "about", "ஏன்", "பற்றி"):
            return (f"{station.DJ_HANDLE}: மனிதரைப் போல பேசும் AI RJ — songs, quick updates, shout-outs.\n"
                    f"நம்ம ஸ்லோகன்: {station.SLOGAN_TA}\nஎப்படி கேட்கலாம்: {station.LISTEN_BLURB_TA}")
        if low in ("how it works", "how", "tech"):
            return ("எப்படி வேலை செய்கிறது\n" + SEP + "\nChat: WhatsApp\nVoice: AI-generated segments between songs\n"
                    "Feeds: weather, news, traffic, gold\nஉங்க messages safety check பண்ணின பிறகு தான் on-air போகும்.")
        if low in ("help", "menu", "commands", "features"):
            return ("கமாண்ட்ஸ்\n" + SEP + "\nlist — பாடல் பட்டியல் (number அனுப்பி request)\nnow — இப்போ என்ன ஓடுது\n"
                    "weather · news · traffic · gold\nhistory — உங்க requests\nabout · how\n\n"
                    "வேற எதுவும் type பண்ணா Kuyil reply பண்ணுவாங்க — shout-out on-air-லயும் வரலாம்! 🎙️")
        return None

    def _now(self) -> str:
        tl = self.timeline_provider()
        if tl is None:
            return f"இப்போது ஓடுவது\n{SEP}\nSchedule இப்போ load ஆகல.\n\n{station.ONAIR_TAG_TA}"
        ev = None
        np = self.now_playing.current() if self.now_playing else None
        if np:
            ev = tl.find_cart(np.cart, near=np.started_at)
        if ev is None:
            ev = tl.at(self.now())
        if ev is None:
            return f"இப்போது ஓடுவது\n{SEP}\nShow இப்போ on-air-ல இல்லை.\n\n{station.ONAIR_TAG_TA}"
        if ev.is_song:
            facts = self.songs.facts(ev.cart)
            what = f"🎵 {facts['title']}" + (f" — {facts['film']}" if facts.get("film") else "") if facts else "🎵 ஒரு super பாடல்"
        elif ev.is_ai:
            what = "🎙️ Kuyil பேசுறாங்க!"
        elif ev.kind == "AD":
            what = "📢 Sponsor break"
        else:
            what = "📻 Station ID"
        return f"இப்போது ஓடுவது\n{SEP}\n{what}\n\n{station.ONAIR_TAG_TA}"

    def _weather(self) -> str:
        try:
            w = get_feeds()["weather"].get()
        except FeedError:
            return "வானிலை தகவல் இப்போ கிடைக்கல — கொஞ்ச நேரம் கழிச்சு try பண்ணுங்க."
        rain = "மழை வாய்ப்பு இருக்கு ☔" if w["raining"] else "வானம் தெளிவு ☀️"
        tomorrow = (f"\nநாளை: {w['tomorrow_min_c']}° – {w['tomorrow_max_c']}°C"
                    if w.get("tomorrow_max_c") is not None else "")
        return f"வானிலை — Dubai\n{SEP}\nஇப்போ: {w['temp_c']}°C, {rain}{tomorrow}"

    def _news(self) -> str:
        try:
            heads = safety.family_safe_headlines(get_feeds()["news"].get()["headlines"])[:3]
        except FeedError:
            heads = []
        if not heads:
            return "News இப்போ கிடைக்கல — கொஞ்ச நேரம் கழிச்சு try பண்ணுங்க."
        return "செய்திகள்\n" + SEP + "\n" + "\n\n".join(f"{i}) {h}" for i, h in enumerate(heads, 1))

    def _traffic(self) -> str:
        try:
            incs = get_feeds()["traffic"].get()["incidents"]
        except FeedError:
            return "Traffic தகவல் இப்போ கிடைக்கல."
        if not incs:
            return "ட்ராஃபிக் — Dubai\n" + SEP + "\nRoads இப்போ calm-ஆ இருக்கு. Safe drive! 🚗"
        lines = [f"{i}) {x['where']} — {x['kind']}" + (f", ~{x['delay_min']} min delay" if x.get("delay_min") else "")
                 for i, x in enumerate(incs, 1)]
        return "ட்ராஃபிக் — Dubai\n" + SEP + "\n" + "\n".join(lines)

    def _gold(self) -> str:
        try:
            m = get_feeds()["metals"].get()
        except FeedError:
            return "Gold விலை இப்போ கிடைக்கல."
        silver = f"\nSilver: ~{m['silver_aed_per_gram']} AED/gram" if m.get("silver_aed_per_gram") else ""
        return f"Gold & Silver\n{SEP}\n24K Gold: ~{m['gold_aed_per_gram_24k']} AED/gram{silver}"

    # ------------------------------------------------------------------ free text
    def _chat_and_screen(self, number: str, user: dict, body: str) -> None:
        name = safety.first_name(user.get("name") or "")
        try:
            reply = writer.chat_reply(body, name)
            notify.send_whatsapp(number, f"🎙️ {station.DJ_HANDLE}: {reply}")
        except Exception as e:
            log.warning("chat reply failed: %s", e)
        if not user.get("name_ok"):
            log.info("not screening for air: listener %s has no usable on-air name", number[-4:])
            return
        verdict = safety.check_message(body, name)
        if verdict.safe and verdict.kind in safety.ON_AIR_KINDS:
            if verdict.area and not user.get("area"):
                self.store.update(number, area=verdict.area)
            self.pool.add(number=number, name=name, summary=verdict.summary, kind=verdict.kind,
                          area=verdict.area or user.get("area", ""))
        else:
            log.info("not for air (%s, %s): %s", verdict.kind, verdict.reason, body[:80])


def create_app(bot: WhatsAppBot) -> Flask:
    app = Flask("airadio")

    @app.route("/whatsapp", methods=["POST"])
    def whatsapp_reply():
        number = request.values.get("From", "") or ""
        body = request.values.get("Body", "") or ""
        log.info("message from …%s: %s", number[-4:], body[:80])
        try:
            reply = bot.handle(number, body)
        except Exception:
            log.exception("webhook error")
            reply = "Sorry, ஒரு சின்ன problem — மீண்டும் try பண்ணுங்க."
        resp = MessagingResponse()
        if reply:
            resp.message(reply)
        return Response(str(resp), status=200, mimetype="text/xml; charset=utf-8")

    @app.route("/health")
    def health():
        return {"ok": True, "pending_shoutouts": bot.pool.pending_count()}

    return app
