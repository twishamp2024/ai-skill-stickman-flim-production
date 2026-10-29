# Delivery

## Pipeline
1. Each shot renders to a lossless FFV1/MKV intermediate (`build/<fmt>/shots/<shot>.mkv`, RGB
   `bgr0`), verified for exact frame count.
2. Assembly (`assemble:<fmt>`): fades (`transition_in/out: fade`) and dissolves (`dissolve`, shots
   overlap on the timeline) via FFmpeg filters → H.264 (`crf` from `production.delivery`,
   `yuv420p`) + AAC 48 kHz stereo master + `mov_text` subtitles, `+faststart`.
3. Shot exports: `output/shots/<fmt>/<shot>.mp4` with that shot's slice of the final mix.
4. Export QA (`qa/<fmt>/final.json`), contact sheet `output/review/contact_<fmt>.png`.
5. `output/delivery.json`: films (path, aspect, SHA-256, bytes, QA report), stems, SRT, shot
   exports, all QA reports, subjective reviews, tool versions, provenance.

## Defaults and platform targets
Default: MP4, H.264 `yuv420p`, CRF 18, AAC 192 kb/s, 48 kHz stereo, −16 LUFS / −1.5 dBTP.
Common adjustments (`production.delivery` + `formats`):
| Target | Format | Loudness |
|---|---|---|
| YouTube / web | 16:9 1920×1080 or 1280×720 | −14 to −16 LUFS |
| Reels / Shorts / TikTok | 9:16 1080×1920 | −14 LUFS |
| Feed posts | 4:5 1080×1350 or 1:1 1080×1080 | −14 LUFS |
| Broadcast-style | 16:9, 25 fps where required | −23 LUFS (EBU R128) |

Resolutions must be even and match the aspect (validated). All formats share assets, timeline and
audio; only cameras (and their variants) differ.

## Final report template
- Deliverables: paths, formats, duration, frames, SHA-256 (from `delivery.json`).
- Verified: list the QA reports and key numbers (frame counts, PSNR range, loudness, true peak).
- Reviews: who looked/listened (from `qa/reviews.json`); explicitly state what nobody reviewed.
- Assumptions (from `production.json`) and provider approximations (from `qa/audio.json`).
- Limitations and suggested next steps.
