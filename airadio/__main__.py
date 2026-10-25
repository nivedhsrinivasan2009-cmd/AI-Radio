"""Command line for AI Radio.

    python -m airadio check                       is everything ready? (model, internet, keys, files)
    python -m airadio sample-log --start 22:00    write tonight's pretend-station log (10 PM, 2 hours)
    python -m airadio sample-log --test-minutes 3 a short log starting in 3 minutes, for a quick full run
    python -m airadio run                         run tonight's show (+ WhatsApp webhook on :5000)
    python -m airadio render AI2202               render one slot from the log to output/test/ to listen to
    python -m airadio reset-carts                 put the safe station-ID clip into every AI cart
    python -m airadio webhook                     only the WhatsApp webhook
    python -m airadio import-songs export.csv     build the song table from a library export (AI cleanup)
    python -m airadio measure-loudness FOLDER     measure the station's files to set TARGET_LUFS
    python -m airadio run --monitor sim           run the show AND listen to it off-air (simulated radio)
    python -m airadio monitor --source rtl        just listen: signal, RDS name/text, dead air (real SDR stick)
    python -m airadio monitor --source sim --record out.wav --seconds 60   hear what the receiver decodes
"""
import argparse
import logging
import shutil
import signal
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config
from .logsetup import setup_logging

log = logging.getLogger("airadio")


# ------------------------------------------------------------------ wiring
def _default_log_path(d: date | None = None) -> Path:
    return config.STATION_LOG_DIR / f"log_{(d or date.today()):%Y-%m-%d}.csv"


def _load_timeline(path_arg: str | None):
    from .connectors.timeline import read_log_csv
    path = Path(path_arg) if path_arg else _default_log_path()
    if not path.exists():
        sys.exit(f"No station log at {path}.\nCreate one with:  python -m airadio sample-log --start 22:00")
    return read_log_csv(path)


def _songs():
    from .connectors.songs import SongTable
    from .sample_station import write_sample_song_table
    if not config.SONG_TABLE_PATH.exists():
        write_sample_song_table()
        log.info("wrote the sample song table to %s", config.SONG_TABLE_PATH)
    return SongTable.load(config.SONG_TABLE_PATH)


def _start_webhook(songs, pool, timeline_provider, now_playing) -> threading.Thread:
    from .listeners.store import UserStore
    from .listeners.whatsapp import WhatsAppBot, create_app
    bot = WhatsAppBot(UserStore(config.USERS_FILE), pool, songs,
                      timeline_provider=timeline_provider, now_playing=now_playing)
    app = create_app(bot)
    t = threading.Thread(target=lambda: app.run(host=config.WEBHOOK_HOST, port=config.WEBHOOK_PORT,
                                                debug=False, use_reloader=False, threaded=True),
                         daemon=True, name="webhook")
    t.start()
    log.info("WhatsApp webhook on :%d/whatsapp (point the Twilio sandbox at <ngrok-url>/whatsapp)", config.WEBHOOK_PORT)
    return t


def _monitor_source(kind: str, timeline, *, freq_mhz: float = 89.4, nowplaying_path: Path | None = None):
    """Build the radio-signal source for the off-air monitor."""
    from .monitor.sources import FileSource, RtlSdrSource, SimulatedSource
    if kind == "rtl":
        return RtlSdrSource(freq_mhz=freq_mhz)
    if kind == "sim":
        from .monitor.program import StationPlayout
        playout = StationPlayout(timeline, dropbox=config.DROPBOX_DIR, songs_dir=config.PROJECT_ROOT / "songs",
                                 ads_dir=config.PROJECT_ROOT / "ads", ids_dir=config.PROJECT_ROOT / "theme songs",
                                 nowplaying_path=nowplaying_path)
        return SimulatedSource(playout)
    if kind.startswith("file:"):
        return FileSource(Path(kind[5:]))
    sys.exit(f"unknown monitor source {kind!r} (use sim, rtl or file:PATH)")


# ------------------------------------------------------------------ commands
def cmd_run(args):
    from .connectors.delivery import get_delivery
    from .connectors.timeline import get_now_playing
    from .listeners.notify import Alerter
    from .listeners.shoutouts import ShoutoutPool
    from .show.runner import ShowRunner, ensure_safe_default
    from .voice.tts import get_voice

    timeline = _load_timeline(args.log)
    songs = _songs()
    pool = ShoutoutPool(config.SHOUTOUT_FILE)
    sim_np = None
    if args.monitor == "sim" and not config.NOWPLAYING_FILE:
        # The simulated station also behaves like AudioVAULT's now-playing output.
        sim_np = config.PROJECT_ROOT / "output" / "sim_nowplaying.json"
        now_playing = get_now_playing(str(sim_np))
    else:
        now_playing = get_now_playing(config.NOWPLAYING_FILE)
    voice = get_voice()
    if not args.no_webhook:
        _start_webhook(songs, pool, lambda: timeline, now_playing)
    safe = ensure_safe_default(voice)
    runner = ShowRunner(timeline, songs, pool, get_delivery(config.DROPBOX_DIR), voice,
                        now_playing=now_playing, alerter=Alerter(), safe_default=safe)
    signal.signal(signal.SIGINT, lambda *_: (log.info("stopping (Ctrl+C) — carts keep their current audio"),
                                             runner.stop()))
    log.info("carts go to %s; timing: %s", config.DROPBOX_DIR,
             "now-playing file + log" if (config.NOWPLAYING_FILE or sim_np) else "log only")
    monitor = None
    if args.monitor != "off":
        from .monitor.monitor import OffAirMonitor
        monitor = OffAirMonitor(_monitor_source(args.monitor, timeline, freq_mhz=args.freq, nowplaying_path=sim_np),
                                expected=runner.expected_on_air, on_aired=runner.on_aired,
                                on_missing=runner.on_missing, alerter=runner.alerter,
                                record_path=Path(args.record) if args.record else None).start()
        log.info("off-air monitor listening (%s)", args.monitor)
    try:
        runner.run()
    finally:
        if monitor:
            monitor.stop()
            log.info("off-air summary: %s", monitor.status())
    if not args.no_webhook and not runner.stop_event.is_set() and args.stay:
        log.info("show finished; webhook still up — Ctrl+C to exit")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


def cmd_monitor(args):
    from .monitor.monitor import OffAirMonitor
    tl = None
    if args.source == "sim":
        path = Path(args.log) if args.log else _default_log_path()
        if path.exists():
            from .connectors.timeline import read_log_csv
            tl = read_log_csv(path)
    mon = OffAirMonitor(_monitor_source(args.source, tl, freq_mhz=args.freq),
                        record_path=Path(args.record) if args.record else None, status_every_s=5)
    print(f"Listening ({args.source}, {args.freq} MHz). Ctrl+C to stop.")
    try:
        mon.run(max_seconds=args.seconds)
    except KeyboardInterrupt:
        pass
    print()
    print("summary:", mon.status())
    if args.record:
        print("off-air audio saved to", args.record)


def cmd_webhook(args):
    from .connectors.timeline import get_now_playing
    from .listeners.shoutouts import ShoutoutPool
    tl = None
    if _default_log_path().exists():
        tl = _load_timeline(None)
    t = _start_webhook(_songs(), ShoutoutPool(config.SHOUTOUT_FILE), lambda: tl, get_now_playing(config.NOWPLAYING_FILE))
    try:
        t.join()
    except KeyboardInterrupt:
        pass


def cmd_sample_log(args):
    from .sample_station import generate_log, write_log, write_sample_song_table
    write_sample_song_table()
    if args.test_minutes is not None:
        start = (datetime.now() + timedelta(minutes=args.test_minutes)).replace(microsecond=0)
        rows = generate_log(start, hours=args.hours or 1, test_scale=args.scale)
    else:
        d = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
        hh, mm = (int(x) for x in args.start.split(":"))
        start = datetime.combine(d, datetime.min.time()).replace(hour=hh, minute=mm)
        rows = generate_log(start, hours=args.hours or 2)
    path = write_log(rows, start.date(), name=args.name)
    n_ai = sum(r["type"] == "AI" for r in rows)
    print(f"wrote {path}  ({len(rows)} events, {n_ai} Kuyil slots, starts {start:%H:%M:%S})")


def cmd_render(args):
    from .listeners.shoutouts import ShoutoutPool
    from .show import composer, planner
    from .voice.tts import get_voice
    tl = _load_timeline(args.log)
    slot = tl.find_cart(args.cart)
    if not slot or not slot.is_ai:
        sys.exit(f"{args.cart} is not an AI slot in {tl.source}")
    pool = ShoutoutPool(config.SHOUTOUT_FILE) if args.shoutouts else None
    plan = planner.plan_slot(slot, tl, _songs(), pool=pool, with_shoutouts=bool(pool))
    out = config.PROJECT_ROOT / "output" / "test" / f"{slot.cart}_{datetime.now():%H%M%S}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    res = composer.render(plan, slot.slot_type, get_voice(), out)
    if pool:
        pool.release(plan.shoutout_ids)
    print(f"{slot.cart} ({slot.slot_type} → {plan.segment}) {res.seconds:.1f}s [{res.source}, {res.attempts} renders]")
    print(f"script: {res.script}")
    print(f"audio:  {out}")


def cmd_reset_carts(args):
    from .connectors.delivery import get_delivery
    from .listeners.shoutouts import ShoutoutPool
    from .show.runner import ShowRunner, ensure_safe_default
    from .voice.tts import get_voice
    tl = _load_timeline(args.log)
    voice = get_voice()
    runner = ShowRunner(tl, _songs(), ShoutoutPool(None), get_delivery(config.DROPBOX_DIR), voice,
                        safe_default=ensure_safe_default(voice))
    runner.reset_carts()


def cmd_measure_loudness(args):
    from .voice.audio import measure_lufs_file
    files = [p for p in Path(args.folder).rglob("*") if p.suffix.lower() in (".mp3", ".wav", ".flac", ".m4a")]
    if not files:
        sys.exit("no audio files found")
    values = []
    for p in files:
        v = measure_lufs_file(p)
        print(f"{v if v is not None else '  ?':>7} LUFS  {p.name}")
        if v is not None:
            values.append(v)
    if values:
        avg = sum(values) / len(values)
        print(f"\naverage {avg:.1f} LUFS over {len(values)} files → set TARGET_LUFS={avg:.1f} in .env to match")


def cmd_import_songs(args):
    import csv
    from .brain import llm
    from .connectors.songs import SongTable
    rows_in = list(csv.DictReader(open(args.export, newline="", encoding="utf-8-sig")))
    out_rows = []
    for r in rows_in:
        low = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        cart = low.get("cart") or low.get("id") or low.get("cut")
        raw_title, raw_artist = low.get("title", ""), low.get("artist", "")
        if not cart or not raw_title:
            continue
        prompt = (
            "This is a song from a Tamil radio station's music library. Clean up the messy library fields.\n"
            f"Raw title: {raw_title}\nRaw artist: {raw_artist}\n"
            "Return JSON with: title, film, music_director, singers (comma separated), and Tamil-script spoken forms "
            "title_ta, film_ta, music_director_ta, singers_ta. Use \"\" for anything you are not sure about — "
            "a blank is much better than a guess."
        )
        try:
            data = llm.chat_json(prompt)
        except Exception as e:
            print(f"{cart}: AI cleanup failed ({e}); keeping raw title")
            data = {"title": raw_title.title()}
        row = {k: str(data.get(k, "") or "") for k in
               ("title", "film", "music_director", "singers", "title_ta", "film_ta", "music_director_ta", "singers_ta")}
        row.update(cart=cart, reviewed="no")
        out_rows.append(row)
        print(f"{cart}: {row['title']} | {row['film']} | {row['music_director']}")
    out = Path(args.out) if args.out else config.SONG_TABLE_PATH
    if out.exists():
        shutil.copyfile(out, out.with_suffix(".csv.bak"))
    SongTable(out_rows, out).save()
    print(f"\nwrote {len(out_rows)} songs to {out}. Open it in Excel, fix anything wrong, set reviewed=yes.")


def cmd_check(args):
    import requests
    from .brain import llm
    ok = True

    def line(good, what, hint=""):
        nonlocal ok
        ok &= bool(good) or what.startswith("(optional)")
        print(f"[{'OK ' if good else '!! '}] {what}" + (f" — {hint}" if hint and not good else ""))

    line(shutil.which("ffmpeg"), "ffmpeg on PATH", "install ffmpeg (needed for audio)")
    line(llm.is_available(), f"Ollama running (model {config.LLM_MODEL})", "start Ollama")
    try:
        names = [m.get("name") or m.get("model") for m in llm._client().list()["models"]]
        line(config.LLM_MODEL in names, f"model {config.LLM_MODEL} pulled", f"ollama pull {config.LLM_MODEL}")
    except Exception:
        pass
    try:
        requests.get("https://translate.google.com", timeout=5)
        line(True, "internet (gTTS voice)")
    except Exception:
        line(False, "internet (gTTS voice)", "no internet: new audio can't be made; drafts/safe clip will air")
    line(config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN, "Twilio keys", "WhatsApp replies will only be logged")
    line(config.ALERT_WHATSAPP_TO, "(optional) ALERT_WHATSAPP_TO set", "alerts go to the log file only")
    line(config.NEWS_API_KEY, "(optional) NEWS_API_KEY", "news features will be skipped")
    line(config.TOMTOM_API_KEY, "(optional) TOMTOM_API_KEY", "traffic features will be skipped")
    line(config.METALPRICE_API_KEY, "(optional) METALPRICE_API_KEY", "gold features will be skipped")
    line(config.SONG_TABLE_PATH.exists(), f"song table {config.SONG_TABLE_PATH.name}", "python -m airadio sample-log")
    line(_default_log_path().exists(), f"tonight's log {_default_log_path().name}", "python -m airadio sample-log --start 22:00")
    print("\nReady." if ok else "\nFix the !! lines above.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="airadio", description="Kuyil — AI RJ for the station's automation log")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run tonight's show")
    r.add_argument("--log", help="station log CSV (default: today's)")
    r.add_argument("--no-webhook", action="store_true", help="don't start the WhatsApp webhook")
    r.add_argument("--stay", action="store_true", help="keep the webhook running after the show")
    r.add_argument("--monitor", default="off", help="off-air monitor: off (default), sim, rtl, or file:PATH")
    r.add_argument("--freq", type=float, default=89.4, help="station frequency in MHz (for --monitor rtl)")
    r.add_argument("--record", help="save the off-air audio to this WAV file")
    r.set_defaults(func=cmd_run)

    mo = sub.add_parser("monitor", help="listen to the station off-air (no show)")
    mo.add_argument("--source", default="sim", help="sim (default), rtl, or file:PATH")
    mo.add_argument("--freq", type=float, default=89.4)
    mo.add_argument("--log", help="log for the simulated station (default: today's)")
    mo.add_argument("--seconds", type=float, help="stop after this many seconds")
    mo.add_argument("--record", help="save the decoded off-air audio to a WAV file")
    mo.set_defaults(func=cmd_monitor)

    sub.add_parser("webhook", help="only the WhatsApp webhook").set_defaults(func=cmd_webhook)

    s = sub.add_parser("sample-log", help="write a pretend-station log")
    s.add_argument("--start", default="22:00", help="show start HH:MM (default 22:00)")
    s.add_argument("--hours", type=int, help="show length in hours (default 2; 1 in test mode)")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s.add_argument("--test-minutes", type=int, help="quick test: start N minutes from now with shortened songs")
    s.add_argument("--scale", type=float, default=0.2, help="test mode song-length scale (default 0.2)")
    s.add_argument("--name", help="file name (default log_YYYY-MM-DD.csv)")
    s.set_defaults(func=cmd_sample_log)

    rd = sub.add_parser("render", help="render one slot to output/test/ to listen to")
    rd.add_argument("cart")
    rd.add_argument("--log")
    rd.add_argument("--shoutouts", action="store_true", help="include pending WhatsApp shout-outs (released afterwards)")
    rd.set_defaults(func=cmd_render)

    rc = sub.add_parser("reset-carts", help="put the safe clip into every AI cart")
    rc.add_argument("--log")
    rc.set_defaults(func=cmd_reset_carts)

    ml = sub.add_parser("measure-loudness", help="measure a folder of station audio")
    ml.add_argument("folder")
    ml.set_defaults(func=cmd_measure_loudness)

    im = sub.add_parser("import-songs", help="build the song table from a library export CSV")
    im.add_argument("export")
    im.add_argument("--out")
    im.set_defaults(func=cmd_import_songs)

    sub.add_parser("check", help="check the setup").set_defaults(func=cmd_check)

    args = p.parse_args(argv)
    setup_logging()
    args.func(args)


if __name__ == "__main__":
    main()
