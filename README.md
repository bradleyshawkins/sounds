# Sounds

A desktop music practice tool for musicians who learn by ear. Slow down and pitch-shift any audio in real time — no pre-processing delay — with clean file management and AI-powered features on the roadmap.

Inspired by Amazing Slow Downer, but with better organization, stem separation, and song structure detection planned.

## Features

- **Instant playback** — real-time time-stretching and pitch-shifting via [Rubber Band](https://breakfastquay.com/rubberband/), starts the moment you hit play
- **Speed control** — 0.1× to 1.9×, pitch-preserved
- **Pitch shifting** — ±24 semitones, ±100 cents fine tuning
- **Volume control**
- **A/B looping** — set loop points by button or by typing a timestamp
- **Seek bar** — click or type a position to jump anywhere in the track
- **URL support** — paste a YouTube URL to play directly via yt-dlp

## Requirements

- [Rubber Band](https://breakfastquay.com/rubberband/) CLI (`brew install rubberband` on macOS)
- [ffmpeg](https://ffmpeg.org/) (for URL audio extraction)
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
git clone <repo>
cd sounds
uv sync
```

## Running

```bash
uv run sounds
```

## Tech Stack

| Concern | Library |
|---|---|
| UI | PyQt6 |
| Time-stretch / pitch | pylibrb (Rubber Band) |
| Audio I/O | sounddevice |
| File decoding | soundfile |
| URL audio | yt-dlp |
| Metadata | mutagen (planned) |
| Library storage | SQLite (planned) |
