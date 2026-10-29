# Characters: identity, gender presentation, age

## One skeleton for everyone
All characters use the same rig: `pelvis, chest, neck, head, shoulder/elbow/hand (l, r),
hip/knee/ankle/toe (l, r)`. There is no gendered rig or gendered animation set. Differences come
only from:

| Control | Field | Drives |
|---|---|---|
| Age | `identity.age_category` (+ `age_years`) | proportions via `library/age_profiles.json` (heads-tall, leg/arm fractions, stride, energy, posture) |
| Height / build | `body.height_m`, `body.build` | scale; garment width (`slim/average/broad/round`) |
| Proportion tweaks | `body.proportion_overrides` (multipliers) | individual traits |
| Appearance | `components[]` + `palette` slots | hair, clothing, accessories, colors |
| Motion style | `motion.energy`, `posture_spine/neck` | amplitude and posture |
| Presentation | `identity.presentation` | **recorded only** — never read by geometry |
| Gender identity | `identity.gender_identity` | **recorded only**, from the story |

Tests assert that changing `presentation`/`gender_identity` leaves the compiled character
byte-identical. Pick components for the person described, not for a stereotype: any hair or
garment fits any presentation (the example mother has cropped hair and trousers on purpose).

## Age profiles
`infant, young_child, child, teen, adult, older_adult`. A child gets a larger head ratio
(5.6 heads tall vs 7.2), shorter legs relative to height, shorter stride, more energy; older
adults get a stoop and reduced arm swing. Children are never uniformly scaled adults (tested).
Seated children's feet dangle above the floor automatically.

## Components (`library/components.json`)
Categories: `hair.*` (none, short_crop, spiky, bob, long, ponytail, bun, curly), `top.*` (tshirt,
long_sleeve, jacket, dress), `bottom.*` (trousers, shorts, skirt), `shoes.basic`, `acc.*`
(shoulder_bag, backpack, uniform_cap, sun_hat, glasses, scarf, hair_bow). Each primitive binds to
joints (`bind`, per-point `{bind, p}`, or `bone` segments between joints), so clothing and
accessories follow every pose and never detach. Colors are palette **slots** (`$hair`, `$top`,
`$bottom`, `$shoes`, `$accessory`, `$line`, `$head_fill`) mapped per character to bible colors;
per-component overrides via `colors`.

Add a component by editing the library (versioned): unit (`head`/`torso`/`leg`), z relative to
the body parts (back arm −3, back leg −2, torso 0, front leg 1, head 2, face 2.5, front arm 3),
stroke widths as multiples of the body line width. Garment bones get an outline bone underneath
so limbs stay readable over same-colored clothing.

## Identity registry fields (`characters/chr_*.json`)
ID, name, version, status, role, relationships, identity, body, palette, components, face
(`profile`/`front`, `mouth_mode`), motion, voice (locked), `distinguishing_features` (≥1),
`variants` (named temporary changes with `remove`/`add`, reason and applicability). A shot selects a
variant with `cast[].variant`; the canonical profile is never edited for a temporary change.

## Distinguishability (identity QA)
`kage.py run <dir> --only identity` renders final, flat, silhouette and small-screen lineups and
computes pairwise silhouette IoU plus feature differences (`qa/identity.json`):
- IoU > 0.9 and only color differs → **error** (must change shape: hair/headwear/build/height).
- IoU > 0.75 with fewer than two shape differences → warning; consider a stronger silhouette cue.
Never rely on color alone. Also look at the small-screen lineup: identities must survive wide
shots and phones.
