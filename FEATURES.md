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
  - [ ] Loops table: named, saveable A/B loops per track
  - [ ] Song structure table (for future AI detection results)
- [x] Incremental re-scan — skip files whose mtime hasn't changed
- [ ] File relink dialog — when a tracked file has moved, prompt to locate it
  - [ ] Silent fallback: try filename + duration match, then content hash match before prompting
- [ ] Three-panel library UI — Artist/Album tree → track list → player
- [ ] Search and filter across all metadata fields
- [ ] Drag-and-drop import
- [ ] Playlist / queue support
- [ ] MusicBrainz metadata enrichment for incomplete tags (background job)

## Audio Search
- [ ] Search by song name via yt-dlp (fetch audio stream from YouTube)

## AI Features (deferred — core must be solid first)
- [ ] Stem separation via Demucs (vocals, drums, bass, other) — cached after first run
- [ ] Toggle individual stems on/off during playback
- [ ] Song structure detection via allin1 (intro, verse, chorus, bridge, outro)
  - [ ] Colored regions on waveform timeline
  - [ ] Click region to jump or set as loop
  - [ ] Manual rename/adjust
- [ ] librosa HPSS as lightweight fallback (harmonic vs. percussive split)
