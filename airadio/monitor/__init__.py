"""Off-air monitor: listens to the station the way a listener does (SDR or simulation).

    sources.py      where the radio signal comes from: RTL-SDR stick, a recording, or the simulator
    transmitter.py  simulated FM transmitter (program audio -> FM radio signal), for testing without hardware
    program.py      simulated station playout (the log played out, like AudioVAULT would)
    receiver.py     FM receiver in software: radio signal -> audio, stereo pilot, RDS text
    rds.py          RDS: the station-name text car radios show (encoder + decoder)
    detect.py       "did Kuyil air?" (audio fingerprint matching) and dead-air / signal-lost detection
    monitor.py      ties it together, runs in a background thread, reports to the show runner
    dsp.py          shared signal-processing building blocks
"""
