# Audio: voices, dialogue, sound design, music, mix

Audio runs on its own task chain but shares the master timeline (`build/timeline.json`).
Not every film needs dialogue, music or effects — plan what the story implies, including silence.

## Voice identity (`voices/voc_*.json`)
`provider`, `provider_voice`, language, accent, perceived age, style, `pitch.pbas`, `rate_wpm`,
timbre, expressiveness, `pronunciations` (respellings), fallbacks, approved sample, **consent**.
Each character references exactly one voice; lines must use it (validation error otherwise,
unless `voice_override_reason`). Choosing a voice is a casting decision; do not infer it from
gender presentation alone, and record it.

### Providers
| Provider | Status | Controls | Alignment |
|---|---|---|---|
| `macos_say` (bundled adapter) | works on macOS only | voice, rate, pitch base, volume, pauses | none |
| other TTS / recorded files | add an adapter in `kagelib/audio.py`, or use `source.type: file` assets | provider-dependent | provider-dependent |

The adapter reports which delivery controls it could not honour (`approximations` in the line
record and warnings in `qa/audio.json`). Emotion with `say` is approximated by pace/volume only.
Never clone a real person's voice without documented authorization.

## Dialogue (`audio/dialogue.json`)
Line: `id, character, voice, text` (verbatim), `language`, `shot` + `offset_s`, `estimated_s`,
`delivery` {emotion, pace, volume, pauses_ms.after_word, gesture, overlap_with}, `subtitle`,
`status`. Generation writes `build/audio/lines/<id>.wav|json` (duration, RMS envelope per frame,
estimated median pitch, rendered text, approximations, cache key) and `build/dialogue_generated.json`.
Lines are generated once and reused; editing one line regenerates only that line.

Timing: shots marked `timing.flex` grow (≤ `max_s`) to fit speech + 0.35 s; otherwise the
timeline reports an error. Speech is never truncated or time-stretched.

## Speech animation (three modes)
1. **Gesture** — `talk` action auto-added over each line (always, unless `delivery.gesture: none`).
2. **Simple mouth** (`closed/open/wide`) — driven by the generated audio's RMS envelope.
3. **Viseme** (`rest, A, E, O, U, MBP, FV, L`) — supported by the engine; requires phoneme
   alignment from a provider. The bundled provider has none, so the compiler falls back to simple
   and says so. Without audio (estimates), mouths flap at ~4 Hz and are marked estimated.

## Sound design (`audio/plan.json`)
- `assets`: every one has `kind`, `source` (`synth` preset | `file` | `generated_external`) and
  **license/provenance**. Built-in synth presets (deterministic, original): `footstep`,
  `seat_creak`, `whistle`, `birds`, `train_interior`, `train_exterior`, `lullaby`.
- `events`: one-shots anchored to `{shot, offset_s}` in film time (can cross cuts).
- `animation_sfx`: footsteps placed at the engine's foot-contact events (with a quieter gain for
  children) — sound follows animation, not guesses.
- `ambience`: one bed per location, continuous across cuts within it, crossfaded between locations.
- `music`: cues anchored to shots (`offset_s: "end"` allowed), fades, gain, `duck_under_dialogue_db`.
- `silence`: deliberate silent spans with a reason.

## Mixing and delivery targets
Stems: dialogue, sfx, ambience, music (WAV, exact timeline length). Music is sidechain-ducked by
dialogue; premaster → gain to target → transparent limiter → linear loudnorm (default −16 LUFS
integrated, −1.5 dBTP; set per platform in `production.delivery`). Stems and master are copied to
`output/stems/`; subtitles to `output/<slug>.srt` and muxed as `mov_text`.

Audio QA checks: missing lines, clipping, loudness (±1 LU), true peak, stems, measured ducking vs
requested, per-voice pitch consistency, voices sharing provider voice + pitch, timeline errors.
Final QA checks each line's onset is audible in the delivered file. **Listening review is not
automated**: record it with `kage.py review --kind listening`, truthfully.

## Multilingual
Keep one line per language per beat (`translations` or a separate dialogue manifest per language),
map voices per language in the voice profile, re-run so timing is recomputed from generated
durations (languages differ in length); subtitles come from the same manifest.
