# Music Practice Tool — Project Context

## What We're Building

A **desktop music practice application** inspired by Amazing Slow Downer (ASD), but with significantly better file management, AI-powered stem separation, song structure detection, and a modern UI. The app targets musicians who want to slow down and learn music by ear — guitarists, bassists, transcribers, etc.

The core gap in the market: no existing desktop app combines good file management, stem separation, and song structure detection in one place. ASD is simple but weak on organization. Transcribe! is powerful but dated. Moises has the best AI features but is web/mobile-first. This app fills that gap.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python | Best audio DSP ecosystem |
| UI Framework | PyQt6 | Native-feeling desktop UI, capable of waveform rendering, loop markers, playlist panels |
| Time-stretch / Pitch | pyrubberband | Gold standard (used in pro DAWs); wraps the Rubberband C++ library |
| Audio I/O | sounddevice | Real-time chunked audio output |
| File decoding | soundfile | FLAC, WAV, AIFF support |
| Audio analysis | librosa | Waveform display data, HPSS, structure analysis fallback |
| Stem separation | Demucs | Best open-source quality for 4-stem separation (vocals, drums, bass, other) |
| Song structure | allin1 | Deep learning model; outputs semantic labels (intro, verse, chorus, bridge, outro) |
| Metadata | MusicBrainz API | Free, open; enriches local library with artist/album/BPM/key/art |
| Audio search | yt-dlp | Search by song name and fetch audio stream from YouTube; avoids need for local MP3 |
| Signal processing math | numpy | Glue between all audio libraries |
| EQ / effects | pedalboard (Spotify) | Ready-made parametric EQ, low overhead |

---

## Core Features

### Playback Engine
- Speed control: 10%–200% of original speed, pitch-preserved
- Pitch shifting: ±24 semitones, fine-tune in cents
- Real-time processing via chunked pipeline (no pre-processing step)
- EQ with frequency band control

### Looping
- A/B loop point setting (set during playback or by dragging on waveform)
- Named, saveable loops — persisted per track
- Precise loop boundary editing
- One-click loop from detected song sections (verse, chorus, etc.)
- +/-1% speed increment buttons (pain point in ASD)

### File Management (Key Differentiator)
- Folder browser + library view with metadata (artist, album, tags)
- Drag-and-drop import
- Playlist / queue support
- MusicBrainz metadata enrichment
- Search by song name via yt-dlp (no local file needed)

### AI Processing (Per-Track, One-Time Background Job)
- **Stem separation** via Demucs: vocals, drums, bass, other
  - Cached after first run
  - Toggle stems on/off during playback
- **Song structure detection** via allin1: intro, verse, chorus, bridge, outro
  - Displayed as colored regions on waveform timeline
  - Click region to jump or instantly set as loop
  - Manual rename/adjust supported (auto-detection isn't perfect)
- Run both as a background worker thread on track load
- librosa HPSS available as lightweight fallback (harmonic vs. percussive split)

### Export
- Render processed audio (slowed, pitch-shifted) to new file

### Keyboard Shortcuts
- Fully customizable
- Modifier key combos supported (Ctrl, Shift, Alt + any key)
- Important for hands-free musician workflow

---

## DSP Concepts (Key Knowledge for This Project)

### Sample Rate
- Standard: 44,100 Hz (CD) or 48,000 Hz (pro)
- Always read from the file and pass through to pyrubberband — never hardcode

### Chunked / Buffered Playback
The core playback loop:
```
while playing:
    chunk = audio_data[read_pos : read_pos + CHUNK_SIZE]
    stretched = pyrubberband.stretch(chunk, rate=speed, pitch=pitch_scale)
    sounddevice.output(stretched)
    read_pos += CHUNK_SIZE
    if read_pos >= loop_end:
        read_pos = loop_start
```
Typical chunk size: 4096 samples.

### Pitch Scale Formula
pyrubberband takes a ratio, not semitones directly:
```python
pitch_scale = 2 ** ((semitones + cents / 100) / 12)
```

### Array Shape Convention
- soundfile, pyrubberband, and sounddevice all use `(samples, channels)` — no transposing needed between them
- librosa uses `(channels, samples)` — transpose when passing data to/from librosa
- Shape mismatches between libraries are the #1 source of bugs — always verify

### Loop Points
- Stored in **input-sample space**, not output-sample space
- Time-stretching causes input and output positions to diverge — track loop points before stretching

### HPSS (Harmonic-Percussive Source Separation)
- librosa built-in: `harmonic, percussive = librosa.effects.hpss(audio)`
- Fast, near-real-time; separates pitched instruments from drums
- Not instrument-level, but useful as a lightweight toggle

---

## Architecture (Three Layers)

```
┌─────────────────────────────────────────┐
│               PyQt6 UI                  │
│  Waveform, loop markers, stem toggles,  │
│  section regions, library panel,        │
│  speed/pitch sliders, playlist          │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│            State Manager                │
│  Current file, loop points, speed,      │
│  pitch, active stems, section data,     │
│  library index, per-track cache         │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│            Audio Engine                 │
│  pyrubberband (stretch/pitch)           │
│  sounddevice (real-time output)         │
│  soundfile (file decode)                │
│  Demucs (stem separation, background)   │
│  allin1 (structure detection, bg)       │
│  yt-dlp (stream fetch by song name)     │
└─────────────────────────────────────────┘
```

---

## Competitive Landscape

| App | Strengths | Weaknesses |
|---|---|---|
| Amazing Slow Downer | Simple, proven, good audio quality | Poor file management, no stems, no structure detection |
| Transcribe! | Professional standard, spectrum analysis, ±36 semitones | Dated UI, no stems, no structure detection |
| Practice Session | Modern UI, cross-platform, $29 | No stems, no structure detection |
| Moises | AI stems, song structure detection, BPM/key detection | Web/mobile-first, subscription, no raw audio access |

**This app's target position:** Desktop-native, local files, ASD-level simplicity for basic use, with Moises-level AI features available on demand.

---

## Streaming / Search Notes

- **Spotify**: Not viable. Raw audio access is prohibited by ToS. API has been heavily locked down as of early 2026 (5 test users max, Premium required, many endpoints removed). Altering Spotify content is explicitly forbidden.
- **yt-dlp**: Best practical option for "search by name and play." Gray area legally for distribution; fine for personal use tools. Fetches best-quality audio stream from YouTube.
- **SoundCloud API**: Developer-friendly alternative; supports streamable tracks.
- **MusicBrainz**: Fully free and open metadata source. Use for library enrichment, not streaming.

---

## User's Pain Points with ASD (Features to Improve On)
- No real playlist support — only adds one track at a time
- No fine-tuning of loop start/end boundaries
- No +/-1% speed increment button
- Poor file organization — raw directory browser only, no metadata view
- No stem separation or song structure detection
- No export of processed audio

---

## Development Notes
- Skip code generation unless explicitly requested
- Prefer Kotlin for general examples, Go for Apache Beam, Python for Apache Spark
- This project: Python throughout
- Background tasks (Demucs, allin1) must run in worker threads to keep UI responsive
- Per-track analysis results should be cached to disk (avoid re-running on every open)
- allin1 requires PyTorch; provide librosa fallback for users without GPU
