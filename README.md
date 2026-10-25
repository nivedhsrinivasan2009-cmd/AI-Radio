# AI Radio — Kuyil, an AI radio jockey

A prototype AI RJ for a Tamil-language FM station in Dubai (89.4 Tamil FM), designed to run
late-night shows when no human DJ is on air. It plugs into the station's existing broadcast
workflow instead of replacing it.

> **Status:** working prototype against a *pretend station* (sample schedule, sample song list, a local
> folder standing in for the station's automation system). Connecting to the real station's log format
> and playout system is the next step. The off-air monitor has been tested with a simulated FM broadcast;
> real-radio testing needs an RTL-SDR stick.

## How it works

The station's schedule (the "log") contains placeholder slots for Kuyil. For each slot the system:

1. **Plans** what to say from plain facts — the songs before and after, weather, news, gold prices, and
   safety-checked WhatsApp shout-outs.
2. **Writes** a natural Tamil-English line with a local LLM (Ollama, `gemma3:12b`), then **fact-checks** it
   so nothing is invented (listeners, places, song details, numbers).
3. **Voices** it with text-to-speech and finishes it to broadcast standard (48 kHz WAV, trimmed, loudness-matched).
4. **Delivers** it into the station's automation system ahead of air time: drafts 20 min before the show,
   fresh versions ~3–5 min before each slot, locked 60 s before air, reset to a safe station-ID clip afterwards.

The system is never in the station's audio path — if it crashes, the station keeps playing and the
pre-made segments still air.

**WhatsApp:** listeners can chat, request songs and send dedications. Messages that could go on air pass a
safety filter (blocklist + LLM moderation, reject when in doubt) and are retold in Kuyil's own words —
first name only, never read verbatim.

**Off-air monitor (SDR):** a software FM receiver that listens to the station like a listener would. It
demodulates FM, detects the 19 kHz stereo pilot, decodes RDS (BPSK on the 57 kHz subcarrier), confirms each
Kuyil segment actually aired using audio fingerprints, notifies listeners whose shout-out aired, and alerts on
dead air or signal loss. A simulated FM stereo + RDS transmitter lets it be tested without hardware.

## Quick start

```bash
pip install -r requirements.txt
ollama pull gemma3:12b            # main model (qwen2.5:7b-instruct is the fallback)
cp .env.example .env              # add your own keys (all optional except Twilio for WhatsApp)
python -m airadio check           # checks model, internet, keys, files
python -m airadio sample-log --start 22:00
python -m airadio run             # tonight's show + WhatsApp webhook on :5000
python -m airadio render AI2202   # make one segment now to listen to
```

The simulated station (`--monitor sim`, `monitor --source sim`) needs some audio files of your own in a
`songs/` folder (music isn't included in this repository); the same applies to 3 of the monitor tests.

## Tests

```bash
python -m unittest discover tests
```

No internet, AI model or SDR hardware is needed for the tests.

## More detail

See [STATION_MODE.md](STATION_MODE.md) for the design decisions, failure handling, the off-air monitor's
signal chain, all commands, and the code map.
