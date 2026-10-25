# AI Radio — Station Mode (the `airadio/` package)

**Built:** 2026-10-20. This is the version that plugs into a real station's workflow
(NaturalMusic/NaturalLog → AudioVAULT → console → processor → transmitter).

---

## What it does, in one paragraph

The station's schedule (the "log") has placeholder slots for Kuyil, called **AI carts**
(`AI2201`, `AI2202`, …). Our program reads the log, writes what Kuyil should say in each
slot, turns it into audio, and drops the audio file into the folder AudioVAULT imports from.
AudioVAULT plays it at the right moment, like any other voice track. We are **never in the
station's audio path**: if our PC crashes, the station keeps playing music and ads, and the
audio we already prepared still airs.

## How a night works (10 PM – 12 AM example)

| When | What happens |
|---|---|
| Any time before | Every AI cart that's empty gets the **safe clip** ("நீங்க கேட்டுக்கொண்டிருப்பது 89.4 தமிழ் FM…") |
| 9:40 PM (20 min before first slot) | **Drafts**: every slot of the night is written and recorded using the log + weather/news. Each cart now holds something sensible |
| ~3–5 min before each slot | **Fresh**: that slot is rewritten with the latest WhatsApp shout-outs and replaces the draft |
| 60 s before each slot | **Locked**: no more changes. If the fresh version wasn't ready, the draft airs |
| After the show | Every AI cart is **reset to the safe clip**, so stale audio ("Happy Monday") can never air tomorrow |

## The decisions it follows (agreed 2026-10-19/20)

- **Slots** (Part 1): top of hour 15–20 s · song link 10–20 s · break intro 5–10 s · feature 45–60 s.
  4–6 per hour. Carts named `AI` + hour + position. Dry segments (no talking over song intros).
- **Timeline** (Part 2): log + optional now-playing file. Talk about the previous song with confidence;
  don't name the next song in the last 10 minutes before the hour (it may get dropped).
- **Only the brain writes words** (Part 0): weather/news/traffic/gold connectors return plain numbers and
  headlines. Swapping in the station's own feed later changes nothing else.
- **Fact rule** (5c): Kuyil only says song/film/artist facts from the song table. A song not in the table is
  never named. A second AI pass **fact-checks every script** before it's recorded; if it invented anything
  (a listener, a place, a number) it's rewritten once, then replaced by a fixed template.
- **WhatsApp** (5b): instant text replies as before. Greetings/dedications/comments/requests are safety-checked
  (blocklist → AI check → reject when in doubt) and become **shout-outs**: first name + area only, retold in
  Kuyil's words, never read out word for word. Max 1 per song link, 3 per feature, 1 per listener per hour,
  expire after 30 min.
- **Requests** (Part 7): acknowledged on air only. The music log is never changed. Request *slots* are a
  future feature.
- **News**: headlines about death, crime, politics, war, religion etc. are removed in code before the AI sees them.
- **Failures** (Part 6): see the table below. Serious problems are WhatsApp'd to `ALERT_WHATSAPP_TO`.
- **Audio** (Part 8): 48 kHz / 16-bit WAV, silence trimmed, loudness matched (default −16 LUFS), no extra EQ.
  Voice = free gTTS for now (robotic); a paid voice can be swapped in later (`voice/tts.py`).

## What happens when something breaks

| Problem | What airs |
|---|---|
| Fresh version fails or is late | The draft |
| AI model down | Fixed template lines (only use the given facts) |
| A feed is down (e.g. traffic key expired) | The feature uses the next content type |
| Internet down | No new audio can be made → drafts / safe clip |
| Our PC dies mid-show | The drafts already sitting in the carts |
| Drafts failed before the show | Safe clip in those carts + WhatsApp alert |

---

## Running it

### One-time setup
1. `pip install -r requirements.txt` (no new packages were needed)
2. Ollama running with the models: `ollama pull gemma3:12b` (main) and `qwen2.5:7b-instruct` (backup).
   Gemma 3 was chosen after testing: Qwen 7B/14B produced broken Tamil and mixed in Chinese/Arabic.
3. Optional: put your phone in `.env` as `ALERT_WHATSAPP_TO=whatsapp:+9715XXXXXXXX` for alerts.
4. Check everything: `python -m airadio check`

### A normal night
```
python -m airadio sample-log --start 22:00     # pretend-station log for tonight (until we have the real one)
python -m airadio run                          # runs the show + the WhatsApp webhook on :5000
```
For WhatsApp, ngrok + the Twilio sandbox URL work exactly as before (`<ngrok-url>/whatsapp`).

### Useful commands
| Command | Use |
|---|---|
| `python -m airadio render AI2202` | Make one slot now and save it to `output/test/` to listen to |
| `python -m airadio sample-log --test-minutes 3 --name test.csv` | A 12-minute compressed show starting in 3 min, for a full test run |
| `python -m airadio run --log station_data/logs/test.csv` | Run that test show |
| `python -m airadio reset-carts` | Put the safe clip into every AI cart now |
| `python -m airadio measure-loudness <folder>` | Measure the station's music to set `TARGET_LUFS` |
| `python -m airadio import-songs export.csv` | Build the song table from the station's library export (AI cleanup, then check it in Excel) |
| `python -m unittest discover tests` | Run the 54 automatic tests (no internet, AI or SDR needed) |

### Where things are
| Path | What |
|---|---|
| `station_data/logs/` | The station log(s), `log_YYYY-MM-DD.csv` |
| `station_data/songs.csv` | Song info table (sample: 21 well-known songs, `reviewed=no` — check before airing) |
| `station_data/audiovault_dropbox/` | **The pretend AudioVAULT import folder** — finished carts appear here |
| `station_data/safe_default.wav` | The timeless safe clip |
| `output/archive/<date>/` | Every render ever made (WAV + script text), for review |
| `output/logs/` | Daily log file of everything the system did |
| `output/shoutouts.json` | The shout-out pool |

---

## Code map (`airadio/`)

```
config.py            settings from .env (no hard-coded keys)
station.py           station identity (name, slogan, Kuyil)
brain/
  persona.py         Kuyil's personality + per-slot prompt templates
  writer.py          facts → script, foreign-script check, fact check, template fallback
  templates.py       fixed lines for when the AI model is down
  llm.py             Ollama calls (main + backup model)
  text.py            Tamil text helpers, spoken time ("இரவு 10 மணி")
voice/
  tts.py             gTTS behind a swappable interface
  audio.py           WAV conversion, silence trim, loudness matching (ffmpeg's EBU R128 meter)
connectors/
  feeds.py           weather / news / traffic / gold — plain facts only
  timeline.py        reads the station log; now-playing file reader
  songs.py           song info table (CSV)
  delivery.py        drop finished audio into the import folder (write, then rename)
listeners/
  whatsapp.py        Twilio webhook: replies, song list, requests, commands
  safety.py          blocklist + AI safety check + first-name rules
  shoutouts.py       the shout-out pool and its limits
  store.py           users.json
  notify.py          outgoing WhatsApp + operator alerts
show/
  slots.py           slot types, lengths, cart naming
  planner.py         which facts each slot gets (incl. the caution + no-unknown-song rules, news filter)
  composer.py        plan → script → voice → length check → WAV
  runner.py          the orchestrator: prime → drafts → fresh → lock → reset
monitor/
  dsp.py             filters and sample rates shared by transmitter and receiver
  transmitter.py     simulated FM stereo + RDS transmitter (for testing without hardware)
  program.py         simulated station playout (plays the log like AudioVAULT)
  sources.py         RTL-SDR stick / recording / simulator
  receiver.py        software FM receiver: audio, pilot, RDS
  rds.py             RDS encoder + decoder (station name / text)
  detect.py          "did Kuyil air?" fingerprints; dead-air / signal-lost
  monitor.py         background listener that reports to the show runner
sample_station.py    the pretend station: sample song table + log generator
__main__.py          the command line
```

## The off-air monitor (SDR) — added 2026-10-21

A listener we control. It receives 89.4 FM exactly like a car radio — but in our own code — and checks
what **actually went out over the air**, not just what we handed to AudioVAULT.

**What it does**
- **Confirms each Kuyil segment aired** ("ON AIR CONFIRMED AI2202 at 22:09:31"), by matching a fingerprint
  of the audio we delivered against what it hears. Different Kuyil segments don't get confused (tested).
- **Tells listeners their shout-out aired**: "🎙️ இப்போதான் Kuyil உங்களை 89.4 Tamil FM-ல mention பண்ணாங்க!"
  — only after the monitor actually heard it.
- **Alerts** you if the station is silent for 30 s, if the 89.4 signal disappears for 30 s, or if a Kuyil
  segment that was due is never heard.
- **Reads RDS** — the station name/text car radios display — straight off the air.

**How it works (the radio part, for the EE side)**
```
IQ samples 1.14 MHz → channel filter, ÷5 → FM demodulator (phase difference) → MPX 228 kHz
   MPX → 14 kHz low-pass, ÷5, 50 µs de-emphasis → audio 45.6 kHz → fingerprints / silence check
   MPX → FFT → 19 kHz pilot present? → "station is on air"
   MPX → mix down 57 kHz, low-pass, ÷24 → BPSK carrier tracking → biphase bit timing →
         differential decoding → CRC/offset-word block sync → groups 0A (name) / 2A (text)
```
The RDS check-word maths matches gr-rds (an open-source decoder used on real stations) — tested.

**Without the stick: the simulator.** `monitor/transmitter.py` builds a real FM stereo broadcast signal
(pilot, 38 kHz stereo, 57 kHz RDS, pre-emphasis, ±75 kHz FM, noise) from the pretend station's playout
(`monitor/program.py` plays the log like AudioVAULT would, reading Kuyil's carts from the drop folder at
the moment they start). The receiver can't tell it from a real stick.

**Commands**
| Command | Use |
|---|---|
| `python -m airadio run --monitor sim` | Run the show and listen to it through the simulated radio |
| `python -m airadio run --monitor rtl` | Same, listening to the real 89.4 with the SDR stick |
| `python -m airadio monitor --source rtl --seconds 60 --record offair.wav` | Just listen: signal, RDS, and save what our own receiver decodes |
| `python -m airadio monitor --source sim` | Same, on the simulated station |

**When the RTL-SDR stick arrives** (e.g. "RTL-SDR Blog V4", ~$30)
1. Plug it in, attach the antenna (stretch it out, near a window).
2. Install the driver: run **Zadig** (zadig.akeo.ie) → Options → List all devices → pick
   "Bulk-In, Interface (Interface 0)" → WinUSB → Replace Driver.
3. `pip install pyrtlsdr pyrtlsdrlib`
4. `python -m airadio monitor --source rtl --seconds 30 --record test.wav` — you should see
   "signal OK", the real station's RDS name, and hear 89.4 in `test.wav`.

## What still needs the station engineer (parked)

These are the only places that change when we get the real details — everything else stays:

1. **Log format** → `connectors/timeline.py` (`read_log_csv` reads our pretend CSV; add a reader for the
   real NaturalLog/AudioVAULT export).
2. **How AudioVAULT takes new audio** → `connectors/delivery.py` (watch folder vs API). Today: a folder.
3. **Now-playing output** → `connectors/timeline.py` (`FileNowPlaying`). Today: off (log-only timing).
4. **Required file format / loudness** → `.env` (`AUDIO_SAMPLE_RATE`, `TARGET_LUFS`).
5. **Song library export** → `python -m airadio import-songs` to build the real song table.

## Known limits / later

- gTTS voice is robotic; a neural voice is the biggest quality upgrade (needs a paid/free-tier key).
- TomTom key is rejected (expired?) → traffic features are skipped until a new key is in `.env`.
- NewsAPI's free headlines are US-centric; most get filtered out. A local/station news source would be better.
- The files in `songs/` are only 20–30 s previews, not full songs (fine for the simulator).
- Parked features: kill switch, request slots, talk-overs, log reload, demo mode on top of this package.
