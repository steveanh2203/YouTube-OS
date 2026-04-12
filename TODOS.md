# TODOS — AutoCapCut / MasterOS

Last updated: 2026-03-21

## In Progress
<!-- trống -->

## Backlog

### Audio Visualizer (from /plan-ceo-review 2026-03-30)

- [ ] **[P2] Style thumbnails in visualizer picker** — Add 256x128px static preview images for each
  of the 8 visualizer styles so creators can choose visually. Effort: S.
  Why: Text-only list feels cheap; thumbnails make the tool feel premium.

- [ ] **[P2] Batch render for podcast series** — Select N audio files, apply one preset,
  render all in a queue. Effort: M.
  Why: Podcasters produce series; re-configuring per episode kills workflow.
  Depends on: Audio Visualizer core shipped + presets stable.

- [ ] **[P2] Preset scoping by parent project** — Currently presets are global. Add
  `parent_id` FK to `visualizer_presets` table so each channel/parent project has its
  own preset library. Effort: S. Why: Creators with multiple channels need isolated branding.
  Depends on: v1 presets shipped and stable.

- [ ] **[P3] AI-reactive mode (beat detection)** — scipy beat/energy detection driving
  visualizer intensity dynamically. Colors pulse on bass hits. Effort: L (CC: ~1hr).
  Why: The 10x differentiator vs VEED/EchoWave. Build after batch render ships.

### Sora Extension

- [ ] **[P2] Add Jest test infrastructure to Chrome extension** — Set up Jest/Vitest for
  `extensions/autocapcut-flow-helper/` with tests for `fetchNoWatermarkDirect()`.
  Why: New function has 0 automated tests. Catches regressions if Sora API shape changes.
  Effort: S (CC: ~15min). Depends on: replace-snapsora PR shipped.

### General

- [ ] Add unit tests for srt_generator.py
- [ ] Add unit tests for sync_captions.py
- [ ] Document API endpoints với examples

## Done
- [x] Initial README.md
- [x] FastAPI + Tauri integration
- [x] SQLModel database layer
- [x] Rust SRT engine (optional)
- [x] Claude project setup (CLAUDE.md, settings.json, per-directory docs)
