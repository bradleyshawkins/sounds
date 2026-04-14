# Feature Tracker

## Core Playback
- [x] Speed control (0.1× – 1.9×), pitch-preserved via pylibrb
- [x] Pitch shifting — semitones (±24) and cents (±100)
- [x] Real-time streaming playback — no pre-processing delay, starts instantly
- [x] Volume control
- [x] A/B loop points — set by button or by typing M:SS
- [x] Seek bar with editable position field
- [x] Pause preserves position; stop resets to start
- [ ] Export processed audio (slowed/pitched) to a new file
- [ ] EQ with frequency band control (pedalboard)
- [ ] +/−1% speed increment buttons (ASD pain point)
- [ ] Keyboard shortcuts, fully customizable

## Library & File Management
- [x] Recursive folder scan — import all audio files from a folder and subfolders
- [x] Read metadata from files on import (title, artist, album, year, genre) via mutagen
- [x] SQLite database to store library and per-track data
  - [x] Track table: path, content hash, mtime, title, artist, album, duration
  - [x] Loops table: named, saveable A/B loops per track
  - [x] Sections table: detected song structure segments per track
- [x] Incremental re-scan — skip files whose mtime hasn't changed
- [ ] File relink dialog — when a tracked file has moved, prompt to locate it
  - [ ] Silent fallback: try filename + duration match, then content hash match before prompting
- [ ] Three-panel library UI — Artist/Album tree → track list → player
- [ ] Search and filter across all metadata fields
- [ ] Drag-and-drop import
- [ ] Playlist / queue support
- [ ] MusicBrainz metadata enrichment for incomplete tags (background job)

## Guitar Tuning
- [ ] Tuning detection per track — chroma-based analysis infers standard, Eb, D, C#, B standard and drop tunings (Drop D, Drop C, Drop B, etc.)
  - [ ] Detected tuning stored in DB; user can override per track
  - [ ] Tuning shown as a column in the library table
  - [ ] Run as part of the Analyze flow or as a lightweight standalone pass on import
- [ ] Instrument tuning setting — user declares their guitar's tuning once (e.g. "Eb standard", "Drop D"); persisted to app config
  - [ ] Preset list covers common tunings; free semitone entry for anything else
  - [ ] Drop tunings stored as a pair: all-string offset + lowest string extra drop (e.g. Drop D = 0 offset, -2 on low string; Drop C = -2 offset, -2 on low string; Drop B = -3 offset, -2 on low string)
  - [ ] NOTE: pitch auto-compensation for drop-tuned songs is inherently ambiguous — a whole-track pitch shift can match the standard strings but the low string interval stays unique. Decide whether to compensate for the standard strings and warn the user, or skip auto-compensation entirely for drop tunings and flag them for manual handling.
- [ ] Pitch auto-compensation on track load — compute offset between song tuning and instrument tuning and pre-set the pitch slider accordingly
  - [ ] Still manually adjustable after auto-set
  - [ ] Tracks with unknown tuning skip auto-compensation

## Audio Search
- [ ] Search by song name via yt-dlp (fetch audio stream from YouTube)

## AI Features (deferred — core must be solid first)
- [ ] Stem separation via Demucs (vocals, drums, bass, other) — cached after first run
- [ ] Toggle individual stems on/off during playback
- [ ] Song structure detection via allin1 (intro, verse, chorus, bridge, outro)
  - [x] On-demand analysis via Analyze button; results cached in DB
  - [x] Colored section bands on seek bar
  - [ ] Semantic labels (verse, chorus, etc.) — requires allin1/PyTorch; current impl uses librosa with A/B/C labels
  - [ ] Click region to jump or set as loop
  - [ ] Manual rename/adjust
- [ ] librosa HPSS as lightweight fallback (harmonic vs. percussive split)
