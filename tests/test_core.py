"""Fast tests (no internet, no real AI model). Run:  python -m unittest discover tests"""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from airadio import config
from airadio.brain import text as ttext
from airadio.brain import writer
from airadio.connectors.delivery import FolderDelivery
from airadio.connectors.songs import SongTable
from airadio.connectors.timeline import FileNowPlaying, parse_duration, read_log_csv, slot_type_from_title
from airadio.listeners import safety
from airadio.listeners.shoutouts import ShoutoutPool, for_brain
from airadio.show import composer, planner
from tests.helpers import FakeClock, FakeVoice, fake_llm, no_loudness, write_log

SONGS = [
    {"cart": "M1", "title": "Kannazhaga", "film": "3", "music_director": "Anirudh Ravichander",
     "title_ta": "கண்ணழகா", "film_ta": "3", "music_director_ta": "அனிருத்"},
    {"cart": "M2", "title": "Vaseegara", "film": "Minnale", "music_director": "Harris Jayaraj",
     "title_ta": "வசீகரா", "film_ta": "மின்னலே", "music_director_ta": "ஹாரிஸ் ஜெயராஜ்"},
]

LOG = """
22:00:00,ID,ID0001,STATION ID LEGAL,,0:10
22:00:10,AI,AI2201,KUYIL TOP OF HOUR,,0:20
22:00:30,SONG,M1,KANNAZHAGA,ANIRUDH,4:00
22:04:30,AI,AI2202,KUYIL LINK,,0:15
22:04:45,SONG,M2,VASEEGARA,HARRIS,5:00
22:09:45,AI,AI2203,KUYIL BREAK,,0:08
22:09:53,AD,AD1,MALABAR GOLD :30,,0:30
22:10:23,SONG,M9,UNLISTED,,4:00
22:14:23,AI,AI2204,KUYIL FEATURE,,0:55
22:15:18,SONG,M1,KANNAZHAGA,ANIRUDH,4:00
22:52:00,AI,AI2205,KUYIL LINK,,0:15
22:52:15,SONG,M2,VASEEGARA,HARRIS,7:00
23:59:30,SONG,M1,KANNAZHAGA,ANIRUDH,4:00
00:03:30,AI,AI0001,KUYIL LINK,,0:15
"""


def songs():
    return SongTable(SONGS)


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.tl = read_log_csv(write_log(LOG))

    def test_parse(self):
        self.assertEqual(len(self.tl.events), 14)
        self.assertEqual([s.slot_type for s in self.tl.ai_slots()],
                         ["top_of_hour", "song_link", "break_intro", "feature", "song_link", "song_link"])
        self.assertEqual(self.tl.events[0].start, datetime(2026, 10, 20, 22, 0, 0))

    def test_midnight_rollover(self):
        last = self.tl.events[-1]
        self.assertEqual(last.start, datetime(2026, 10, 21, 0, 3, 30))

    def test_neighbours(self):
        link = self.tl.find_cart("AI2202")
        self.assertEqual(self.tl.previous_song(link).cart, "M1")
        self.assertEqual(self.tl.next_song(link).cart, "M2")
        brk = self.tl.find_cart("AI2203")
        self.assertIsNone(self.tl.next_song(brk))  # an ad follows, not a song

    def test_durations_and_titles(self):
        self.assertEqual(parse_duration("4:12"), 252)
        self.assertEqual(parse_duration("1:02:03"), 3723)
        self.assertEqual(slot_type_from_title("KUYIL BREAK"), "break_intro")
        self.assertEqual(slot_type_from_title("KUYIL TOP OF HOUR"), "top_of_hour")

    def test_now_playing_file(self):
        p = Path(tempfile.mkdtemp()) / "np.txt"
        p.write_text('{"cart": "M2", "started": "2026-10-20T22:05:00"}', encoding="utf-8")
        np = FileNowPlaying(str(p)).current()
        self.assertEqual((np.cart, np.started_at), ("M2", datetime(2026, 10, 20, 22, 5)))
        p.write_text("AI2203|KUYIL BREAK", encoding="utf-8")
        self.assertEqual(FileNowPlaying(str(p)).current().cart, "AI2203")


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.tl = read_log_csv(write_log(LOG))

    def test_link_names_known_songs(self):
        plan = planner.plan_slot(self.tl.find_cart("AI2202"), self.tl, songs())
        self.assertEqual(plan.facts["previous_song"]["title"], "Kannazhaga")
        self.assertEqual(plan.facts["next_song"]["title"], "Vaseegara")

    def test_no_next_song_in_last_10_minutes(self):
        plan = planner.plan_slot(self.tl.find_cart("AI2205"), self.tl, songs())  # 22:52 -> 8 min to the hour
        self.assertIsInstance(plan.facts["next_song"], str)
        self.assertIn("do not name", plan.facts["next_song"])

    def test_unknown_song_never_named(self):
        tl = read_log_csv(write_log("""
22:00:00,SONG,M9,UNLISTED,,4:00
22:04:00,AI,AI2202,KUYIL LINK,,0:15
22:04:15,SONG,M9,UNLISTED,,4:00
"""))
        plan = planner.plan_slot(tl.find_cart("AI2202"), tl, songs())
        self.assertIn("do not name", plan.facts["previous_song"])
        self.assertIn("do not name", plan.facts["next_song"])

    def test_break_never_names_advertiser(self):
        plan = planner.plan_slot(self.tl.find_cart("AI2203"), self.tl, songs())
        self.assertNotIn("MALABAR", str(plan.facts).upper())

    def test_top_of_hour_show_start(self):
        plan = planner.plan_slot(self.tl.find_cart("AI2201"), self.tl, songs())
        self.assertEqual(plan.facts["time_spoken"], "இரவு 10 மணி")
        self.assertTrue(plan.facts["show_start"])

    def test_feature_skips_dead_feed(self):
        from airadio.connectors.feeds import FeedError

        class Dead:
            def get(self):
                raise FeedError("down")

        class Weather:
            def get(self):
                return {"temp_c": 30}

        with mock.patch("airadio.show.planner.get_feeds",
                        return_value={"weather": Dead(), "news": Dead(), "traffic": Dead(), "metals": Dead()}):
            plan = planner.plan_slot(self.tl.find_cart("AI2204"), self.tl, songs())
        self.assertEqual(plan.segment, "feature_entertainment")
        with mock.patch("airadio.show.planner.get_feeds",
                        return_value={"weather": Weather(), "news": Dead(), "traffic": Dead(), "metals": Dead()}):
            plan = planner.plan_slot(self.tl.find_cart("AI2204"), self.tl, songs())
        self.assertEqual(plan.segment, "feature_weather")

    def test_news_filter(self):
        heads = ["Star dies at 27", "Apple to release new products", "Trump announces plan", "India win cricket series"]
        self.assertEqual(safety.family_safe_headlines(heads),
                         ["Apple to release new products", "India win cricket series"])


class ShoutoutTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(datetime(2026, 10, 20, 22, 0))
        self.pool = ShoutoutPool(Path(tempfile.mkdtemp()) / "s.json", now=self.clock.now)

    def add(self, number, name="A"):
        return self.pool.add(number=number, name=name, summary="says hi", kind="greeting")

    def test_fresh_first_and_limit(self):
        self.add("1", "Old"); self.clock.sleep(60); self.add("2", "New")
        got = self.pool.take(1)
        self.assertEqual(got[0]["name"], "New")

    def test_one_per_listener_per_hour(self):
        self.add("1"); self.add("1")
        first = self.pool.take(3)
        self.assertEqual(len(first), 1)
        self.pool.commit([first[0]["id"]])
        self.assertEqual(self.pool.take(3), [])           # same listener, same hour
        self.clock.sleep(3700)
        self.add("1")
        self.assertEqual(len(self.pool.take(3)), 1)        # an hour later it's fine

    def test_expiry(self):
        self.add("1")
        self.clock.sleep((config.SHOUTOUT_EXPIRY_MIN + 1) * 60)
        self.assertEqual(self.pool.take(3), [])

    def test_release_returns_to_pool(self):
        self.add("1")
        got = self.pool.take(1)
        self.assertEqual(self.pool.take(1), [])
        self.pool.release([got[0]["id"]])
        self.assertEqual(len(self.pool.take(1)), 1)

    def test_brain_never_sees_number(self):
        self.add("whatsapp:+971500000000", "Priya")
        brain = for_brain(self.pool.take(1))
        self.assertNotIn("971", str(brain))


class SafetyTests(unittest.TestCase):
    def test_blocklist(self):
        self.assertIsNotNone(safety.blocklist_hit("vote for X"))
        self.assertIsNotNone(safety.blocklist_hit("visit www.example.com"))
        self.assertIsNotNone(safety.blocklist_hit("call me 050 123 4567"))
        self.assertIsNone(safety.blocklist_hit("Hi Kuyil, love the show from Sharjah!"))

    def test_names(self):
        self.assertEqual(safety.first_name("Priya Ramesh"), "Priya")
        self.assertTrue(safety.name_ok("Priya"))
        self.assertTrue(safety.name_ok("கார்த்திக்"))
        self.assertFalse(safety.name_ok("http://spam"))
        self.assertFalse(safety.name_ok("x"))

    def test_model_reject_and_failure_reject(self):
        with mock.patch("airadio.brain.llm.chat_json", side_effect=RuntimeError("down")):
            self.assertFalse(safety.check_message("hello", "Priya").safe)   # when in doubt, reject
        with mock.patch("airadio.brain.llm.chat_json",
                        return_value={"safe": True, "kind": "greeting", "summary": "", "area": ""}):
            self.assertFalse(safety.check_message("hello", "Priya").safe)   # no paraphrase -> reject
        with mock.patch("airadio.brain.llm.chat_json",
                        return_value={"safe": True, "kind": "greeting", "summary": "says hi", "area": "Sharjah"}):
            v = safety.check_message("hello", "Priya")
            self.assertTrue(v.safe)
            self.assertEqual(v.area, "Sharjah")


class WriterTests(unittest.TestCase):
    def test_fact_check_failure_falls_back_to_template(self):
        facts = {"previous_song": SONGS[0], "next_song": SONGS[1]}
        with fake_llm(["Nithin from Sharjah says hi! அடுத்தது வசீகரா"], unsupported=["Nithin", "Sharjah"]):
            s = writer.write_segment("song_link", facts)
        self.assertEqual(s.source, "template")
        self.assertIn("வசீகரா", s.text)
        self.assertNotIn("Nithin", s.text)

    def test_hard_rules_catch_invented_place_and_play_promise(self):
        req = {"listener_shoutouts": [{"name": "Karthik", "message_summary": "asked for the song Vaseegara"}]}
        self.assertEqual(writer.rule_violations("Karthik, Abu Dhabi-ல இருந்து Vaseegara கேட்டீங்க", req), ["Abu Dhabi"])
        self.assertTrue(writer.rule_violations("Karthik, Vaseegara கண்டிப்பா போடணும்!", req))
        self.assertEqual(writer.rule_violations("Karthik Vaseegara கேட்டிருக்காரு, thanks!", req), [])
        hi = {"listener_shoutouts": [{"name": "Priya", "area": "Sharjah", "message_summary": "says hi from Chennai"}]}
        self.assertEqual(writer.rule_violations("Priya, Sharjah-ல இருந்து சென்னையில உள்ள அம்மாவுக்கு hi", hi), [])
        self.assertEqual(writer.rule_violations("துபாயில் weather super, Dubai!", {}), [])
        with fake_llm(["Karthik, Ajman-ல இருந்து hello!", "Karthik, Ajman-ல இருந்து hello!"]):
            s = writer.write_segment("song_link", req)          # model's own checker says OK, code catches it
        self.assertEqual(s.source, "template")

    def test_foreign_script_rejected(self):
        with fake_llm(["这里是 line", "这里是 again"]):
            s = writer.write_segment("break_intro", {})
        self.assertEqual(s.source, "template")

    def test_good_line_kept(self):
        with fake_llm(["அது கண்ணழகா! அடுத்தது வசீகரா, stay tuned!"]):
            s = writer.write_segment("song_link", {"previous_song": SONGS[0], "next_song": SONGS[1]})
        self.assertEqual(s.source, "llm")

    def test_slogan_added(self):
        with fake_llm(["இரவு 10 மணி, Kuyil உங்க கூட!"]):
            s = writer.write_segment("top_of_hour", {"time_spoken": "இரவு 10 மணி"})
        self.assertTrue(s.text.endswith("நம்ம Radio நம்ம Music"))

    def test_shoutout_prompt_only_when_present(self):
        p1 = writer.build_prompt("song_link", {})
        p2 = writer.build_prompt("song_link", {"listener_shoutouts": [{"name": "Priya", "message_summary": "hi"}]})
        self.assertIn("NO listener messages", p1)
        self.assertIn("Mention ONLY these listeners", p2)


class ComposerTests(unittest.TestCase):
    def test_too_long_gets_shorter_then_cut(self):
        long_line = "அடுத்தது ஒரு super பாடல் வருது. " * 12        # ~38 s at 10 chars/s
        with fake_llm([long_line, long_line]), no_loudness():
            out = Path(tempfile.mkdtemp()) / "x.wav"
            res = composer.render(planner.Plan("song_link", {}), "song_link", FakeVoice(), out)
        self.assertLessEqual(res.seconds, 22.5)
        self.assertTrue(out.exists() and out.with_suffix(".txt").exists())

    def test_broadcast_format(self):
        with fake_llm(["ஒரு சின்ன break — stay tuned!"]):
            out = Path(tempfile.mkdtemp()) / "x.wav"
            composer.render(planner.Plan("break_intro", {}), "break_intro", FakeVoice(), out)
        from pydub import AudioSegment
        with open(out, "rb") as f:
            seg = AudioSegment.from_wav(f)
        self.assertEqual((seg.frame_rate, seg.channels, seg.sample_width), (48000, 2, 2))


class DeliveryTests(unittest.TestCase):
    def test_atomic_replace(self):
        d = Path(tempfile.mkdtemp())
        src = d / "src.wav"
        src.write_bytes(b"one")
        fd = FolderDelivery(d / "drop")
        fd.deliver("AI2201", src)
        src.write_bytes(b"two")
        fd.deliver("AI2201", src)
        self.assertEqual((d / "drop" / "AI2201.wav").read_bytes(), b"two")
        self.assertEqual([p.name for p in (d / "drop").iterdir()], ["AI2201.wav"])  # no leftovers


class TextTests(unittest.TestCase):
    def test_spoken_time(self):
        self.assertEqual(ttext.spoken_hour_ta(datetime(2026, 1, 1, 22)), "இரவு 10 மணி")
        self.assertEqual(ttext.spoken_hour_ta(datetime(2026, 1, 1, 0)), "நள்ளிரவு 12 மணி")
        self.assertEqual(ttext.spoken_time_ta(datetime(2026, 1, 1, 23, 30)), "இரவு 11 மணி 30 நிமிடம்")

    def test_clean_script(self):
        self.assertEqual(ttext.clean_script('On-air line: "வணக்கம் Dubai! [laughs] 😀"'), "வணக்கம் Dubai!")


if __name__ == "__main__":
    unittest.main()
