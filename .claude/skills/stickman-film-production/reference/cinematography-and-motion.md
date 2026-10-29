# Cinematography and motion

## Coordinates and units
World metres, y up, ground at y = 0 per location. Time in seconds from the shot start. Angles in
degrees. Joint sign convention (character facing +x): spine/neck positive = lean/nod forward;
shoulder/hip positive = limb forward; elbow positive = flexion; knee positive = flexion (shin back).

## Camera (`shot.camera`)
Semantic by default; compiled per output format:
- `framing`: `wide` (full bodies, ground near the bottom), `medium` (two-shot / waist-up, seated
  aware, head-room kept), `close` (head + shoulders, lead room toward the look direction),
  `extreme_close` (head), `custom` (explicit `keys`).
- `subjects`: characters the framing must hold. Width is widened automatically so all subjects
  fit the aspect; narrow aspects get a minimum width in head units for close-ups.
- `angle`: `eye | high | low` (flat style: vertical offset of the frame).
- `move`: `static`, `pan` {`to_x` or `by_x`}, `zoom` {`factor`}, `follow` {`character`,
  `smoothing` s, `offset_x`} (smoothed, deterministic trailing average).
- `keys`: explicit `{t, x, y, height (visible metres), roll, ease}` for full control.
- `variants`: per-aspect overrides, e.g. `"9:16": {"move": {"type": "follow", …}}`. Design a
  variant whenever a composition does not survive the narrower frame — never crop blindly.

Framing QA (per format): camera subjects in `wide`/`custom` framings must be fully visible; in
`medium`/`close` framings their whole head must be in frame. Non-subject cast members only warn.
List intentional partial visibility in `framing.allow_partial`.

## Continuity rules (checked at every cut within a scene)
Same facing, posture and position for characters present on both sides of the cut; characters may
only disappear if declared (`continuity.offscreen` on the next shot or `exits` on the previous);
left-to-right order is preserved (180° rule) unless `allow_screen_direction_change`; character
appearance hashes must match (variant changes are deliberate). Keep travel direction consistent
(the example: train always moves screen-right).

## Actions (engine action library)
Each action: `type, start, duration` (+ `blend_in`, `blend_out`, `hold`). Locomotion uses
`to_x` (metres or `@mark`); the engine turns, drives the leg cycle from distance travelled (no foot
sliding — tested), and emits footstep events for sound sync.

| Type | Key params | Notes |
|---|---|---|
| `walk`, `run` | `to_x` | trapezoid velocity; auto-turn toward target |
| `turn` | `facing` left/right | head and accessories stay rigid mid-turn |
| `sit`, `stand` | `seat_y`, optional `to_x` | ground lock + floor clamp; children's feet dangle |
| `fall`, `recover` | — | to/from lying |
| `gesture` | `gesture`: wave, point, shrug, nod, head_shake, hands_on_hips, arms_crossed, clap; `side`, `intensity` | |
| `talk` | `side`, `intensity` | auto-added for spoken lines (gesture-based speech) |
| `look` | `target` [x,y] or {character, joint} | head tilt |
| `lean` | `spine`, `neck`, `shift_x`, `hold` | e.g. resting on someone |
| `reach` | `side`, `target` | 2-bone IK; holding hands = reach the other's hand joint |
| `pick_up`, `put_down` | `prop`, `side`, `place_at` | prop attaches/detaches at mid-action |
| `keyframes` | `channels` {joint: [[t, deg, ease], …]} | custom motion, local time |
| `pose` | `channels` | static override (use `hold`) |

Layering: locomotion < lean/pick-up < gestures/talk/look/keyframes < reach (IK last). Use custom
`keyframes` for unusual movement instead of engine code.

## Staging tips
Give reactions time (≥0.6 s); start dialogue gestures with the line (automatic); avoid two
characters on the same x in profile; put the character nearer the camera on a higher `z`.
