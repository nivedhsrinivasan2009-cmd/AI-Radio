"""AI Radio — Kuyil, the AI RJ that fills voice slots in a station's automation log.

Package layout:
    config      settings from .env (no secrets hard-coded)
    station     station identity (name, slogan, persona name)
    brain/      AI model calls, persona + segment prompts, script writing, Tamil text helpers
    voice/      text-to-speech + broadcast audio finishing (WAV, loudness, silence trim)
    connectors/ outside data: weather, news, traffic, metals, station log, now-playing,
                song info table, delivery into the automation system
    listeners/  WhatsApp webhook, listener store, safety filter, shout-out pool
    show/       slot model, planner, composer and the show runner (orchestrator)
"""
