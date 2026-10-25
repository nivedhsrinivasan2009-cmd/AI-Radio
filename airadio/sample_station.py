"""The PRETEND station: sample song table + generated logs in the pretend log format.

Used until the real station's NaturalLog export and song library are available, and later
by the demo mode. Song facts below are a starting sample — check them before anything airs
(that's what the `reviewed` column is for).
"""
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config
from .connectors.songs import FIELDS, SongTable

# cart, title, film, music_director, singers, title_ta, film_ta, music_director_ta, singers_ta, duration
SAMPLE_SONGS = [
    ("M00001", "Kannazhaga", "3", "Anirudh Ravichander", "Dhanush, Shruti Haasan",
     "கண்ணழகா", "3", "அனிருத்", "தனுஷ், ஸ்ருதி ஹாசன்", "4:15"),
    ("M00002", "Un Paarvayil", "Ethir Neechal", "Anirudh Ravichander", "",
     "உன் பார்வையில்", "எதிர் நீச்சல்", "அனிருத்", "", "4:05"),
    ("M00003", "Why This Kolaveri Di", "3", "Anirudh Ravichander", "Dhanush",
     "Why This Kolaveri Di", "3", "அனிருத்", "தனுஷ்", "4:10"),
    ("M00004", "Vaathi Coming", "Master", "Anirudh Ravichander", "Anirudh Ravichander, Gana Balachandar",
     "வாத்தி கம்மிங்", "மாஸ்டர்", "அனிருத்", "அனிருத், கானா பாலசந்தர்", "3:50"),
    ("M00005", "Arabic Kuthu", "Beast", "Anirudh Ravichander", "Anirudh Ravichander, Jonita Gandhi",
     "அரபிக் குத்து", "பீஸ்ட்", "அனிருத்", "அனிருத், ஜொனிதா காந்தி", "4:40"),
    ("M00006", "Rowdy Baby", "Maari 2", "Yuvan Shankar Raja", "Dhanush, Dhee",
     "ரவுடி பேபி", "மாரி 2", "யுவன் ஷங்கர் ராஜா", "தனுஷ், தீ", "4:43"),
    ("M00007", "Enjoy Enjaami", "", "Santhosh Narayanan", "Dhee, Arivu",
     "என்ஜாய் எஞ்சாமி", "", "சந்தோஷ் நாராயணன்", "தீ, அறிவு", "4:20"),
    ("M00008", "Munbe Vaa", "Sillunu Oru Kaadhal", "A. R. Rahman", "Shreya Ghoshal, Naresh Iyer",
     "முன்பே வா", "சில்லுனு ஒரு காதல்", "ஏ. ஆர். ரஹ்மான்", "ஷ்ரேயா கோஷல், நரேஷ் ஐயர்", "6:00"),
    ("M00009", "Kadhal Rojave", "Roja", "A. R. Rahman", "S. P. Balasubrahmanyam, Sujatha",
     "காதல் ரோஜாவே", "ரோஜா", "ஏ. ஆர். ரஹ்மான்", "எஸ். பி. பாலசுப்ரமணியம், சுஜாதா", "5:00"),
    ("M00010", "Ilamai Itho Itho", "Sakalakala Vallavan", "Ilaiyaraaja", "S. P. Balasubrahmanyam",
     "இளமை இதோ இதோ", "சகலகலா வல்லவன்", "இளையராஜா", "எஸ். பி. பாலசுப்ரமணியம்", "4:30"),
    ("M00011", "Nenjukkul Peidhidum", "Vaaranam Aayiram", "Harris Jayaraj", "Hariharan",
     "நெஞ்சுக்குள் பெய்திடும்", "வாரணம் ஆயிரம்", "ஹாரிஸ் ஜெயராஜ்", "ஹரிஹரன்", "5:20"),
    ("M00012", "Vaseegara", "Minnale", "Harris Jayaraj", "Bombay Jayashri",
     "வசீகரா", "மின்னலே", "ஹாரிஸ் ஜெயராஜ்", "பாம்பே ஜெயஸ்ரீ", "5:30"),
    ("M00013", "Hosanna", "Vinnaithaandi Varuvaayaa", "A. R. Rahman", "Vijay Prakash",
     "ஹோசானா", "விண்ணைத்தாண்டி வருவாயா", "ஏ. ஆர். ரஹ்மான்", "விஜய் பிரகாஷ்", "5:30"),
    ("M00014", "Kaathalae Kaathalae", "96", "Govind Vasantha", "Chinmayi, Govind Vasantha",
     "காதலே காதலே", "96", "கோவிந்த் வசந்தா", "சின்மயி, கோவிந்த் வசந்தா", "4:00"),
    ("M00015", "Kanave Kanave", "David", "Anirudh Ravichander", "Anirudh Ravichander",
     "கனவே கனவே", "டேவிட்", "அனிருத்", "அனிருத்", "4:30"),
    ("M00016", "Ennodu Nee Irundhaal", "I", "A. R. Rahman", "Sid Sriram, Sunitha Sarathy",
     "என்னோடு நீ இருந்தால்", "ஐ", "ஏ. ஆர். ரஹ்மான்", "சித் ஸ்ரீராம், சுனிதா சாரதி", "5:50"),
    ("M00017", "Maruvaarthai", "Enai Noki Paayum Thota", "Darbuka Siva", "Sid Sriram",
     "மறுவார்த்தை", "எனை நோக்கி பாயும் தோட்டா", "தர்புகா சிவா", "சித் ஸ்ரீராம்", "4:55"),
    ("M00018", "Raja Raja Chozhan", "Rettai Vaal Kuruvi", "Ilaiyaraaja", "K. J. Yesudas",
     "ராஜ ராஜ சோழன்", "ரெட்டை வால் குருவி", "இளையராஜா", "கே. ஜே. யேசுதாஸ்", "4:35"),
    ("M00019", "Thalli Pogathey", "Achcham Yenbadhu Madamaiyada", "A. R. Rahman", "Sid Sriram",
     "தள்ளிப் போகாதே", "அச்சம் என்பது மடமையடா", "ஏ. ஆர். ரஹ்மான்", "சித் ஸ்ரீராம்", "5:40"),
    ("M00020", "Aalaporan Thamizhan", "Mersal", "A. R. Rahman", "Kailash Kher",
     "ஆளப்போறான் தமிழன்", "மெர்சல்", "ஏ. ஆர். ரஹ்மான்", "கைலாஷ் கேர்", "5:55"),
    ("M00021", "Ponni Nadhi", "Ponniyin Selvan: I", "A. R. Rahman", "A. R. Rahman",
     "பொன்னி நதி", "பொன்னியின் செல்வன்", "ஏ. ஆர். ரஹ்மான்", "ஏ. ஆர். ரஹ்மான்", "3:50"),
]

SAMPLE_ADS = [("AD1001", "MALABAR GOLD :30", "0:30"), ("AD1002", "KAG TILES :30", "0:30"),
              ("AD1003", "STATION PROMO :20", "0:20")]

UNKNOWN_SONG = ("M09999", "UNLISTED TRACK", "4:00")  # not in the song table: exercises the "don't name it" rule

PLANNED_AI_LEN = {"TOP OF HOUR": "0:20", "LINK": "0:15", "BREAK": "0:08", "FEATURE": "0:55"}


def write_sample_song_table(path: Path = None, overwrite: bool = False) -> Path:
    path = Path(path or config.SONG_TABLE_PATH)
    if path.exists() and not overwrite:
        return path
    rows = [dict(zip(FIELDS, list(s[:9]) + ["no"])) for s in SAMPLE_SONGS]
    SongTable(rows, path).save()
    return path


def _dur(s: str) -> float:
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)


def _fmt_dur(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def generate_log(start: datetime, hours: int = 2, *, test_scale: float | None = None, seed: int | None = None):
    """Build a realistic log: per hour ID, top-of-hour, 2 songs, link, 2 songs, break + 3 ads,
    song, feature, 2 songs, link, then songs until the hour is full (5 AI slots per hour).

    test_scale shrinks songs/ads (e.g. 0.2 → 4-minute songs last ~50 s) for quick end-to-end tests.
    """
    rng = random.Random(seed if seed is not None else start.toordinal())
    songs = [(s[0], s[1].upper(), s[3].upper(), s[9]) for s in SAMPLE_SONGS]
    deck: list = []

    def next_song():
        if not deck:
            deck.extend(rng.sample(songs, len(songs)))
        last = next((r["cart"] for r in reversed(rows) if r["type"] == "SONG"), None)
        if deck[-1][0] == last and len(deck) > 1:
            deck[-1], deck[0] = deck[0], deck[-1]  # never the same song back to back
        return deck.pop()

    rows, t = [], start
    scale = test_scale or 1.0
    unknown_used = False

    def add(kind, cart, title, artist, dur):
        nonlocal t
        rows.append({"time": t.strftime("%H:%M:%S"), "type": kind, "cart": cart, "title": title,
                     "artist": artist, "duration": dur})
        t += timedelta(seconds=_dur(dur) if ":" in dur else float(dur))

    def add_song():
        nonlocal unknown_used
        if not unknown_used and len(rows) > 20:
            unknown_used = True
            add("SONG", UNKNOWN_SONG[0], UNKNOWN_SONG[1], "", _fmt_dur(_dur(UNKNOWN_SONG[2]) * scale))
            return
        cart, title, artist, dur = next_song()
        add("SONG", cart, title, artist, _fmt_dur(_dur(dur) * scale))

    def ai(hour, pos, label):
        add("AI", f"AI{hour:02d}{pos:02d}", f"KUYIL {label}", "", PLANNED_AI_LEN[label])

    for h in range(hours):
        hour_start = start + timedelta(hours=h) if not test_scale else t
        hour_end = hour_start + timedelta(hours=1 * scale) if test_scale else hour_start + timedelta(hours=1)
        hh = (start.hour + h) % 24  # cart numbers stay unique even when test hours are compressed
        if not test_scale:
            t = hour_start
        add("ID", "ID0001", "STATION ID LEGAL", "", "0:10")
        ai(hh, 1, "TOP OF HOUR")
        add_song(); add_song()
        ai(hh, 2, "LINK")
        add_song(); add_song()
        ai(hh, 3, "BREAK")
        for cart, title, dur in SAMPLE_ADS:
            add("AD", cart, title, "", _fmt_dur(_dur(dur) * (scale if test_scale else 1)))
        add_song()
        ai(hh, 4, "FEATURE")
        add_song(); add_song()
        ai(hh, 5, "LINK")
        # Fill the rest of the hour with songs that fit (real logs backtime the same way).
        while True:
            cart, title, artist, dur = next_song()
            d = _dur(dur) * scale
            if t + timedelta(seconds=d) > hour_end:
                deck.append((cart, title, artist, dur))
                break
            add("SONG", cart, title, artist, _fmt_dur(d))
    return rows


def write_log(rows: list[dict], log_date: date, folder: Path = None, name: str | None = None) -> Path:
    folder = Path(folder or config.STATION_LOG_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or f"log_{log_date:%Y-%m-%d}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["time", "type", "cart", "title", "artist", "duration"])
        w.writeheader()
        w.writerows(rows)
    return path
