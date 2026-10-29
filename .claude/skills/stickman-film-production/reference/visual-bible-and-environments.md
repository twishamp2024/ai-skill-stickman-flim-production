# Visual bible, assets and environments

## Visual bible (`bible/visual_bible.json`)
- `palette`: named sRGB colors. All project files reference names; raw hex in project data is a
  validation error. Keep ≤ ~12 colors per location (warning above `max_palette_colors_per_location`).
- `style.body_line_width` (metres; child/infant profiles scale it), round caps/joins, outline width.
- Fill policy: solid flat colors; no gradients, textures, shadows or glow. Opaque unless an asset
  declares `transparency: true` (strokes must always be opaque — overlapping stroke triangles
  would double-blend).
- `layering.order`, character internal z, and `camera_conventions` (projection, framings, safe area,
  screen-direction rule).
- Lock it (`status: locked`) before shot production; changes = version bump + re-approval, and
  `kage.py impact <dir> bible` lists what re-renders.

## Primitives (assets, components, engine)
`polygon` (concave OK, `holes` OK, must be simple — self-intersections are rejected with a clear
error), `polyline` (stroke, `closed` optional), `circle`, `ellipse` (`rotation` degrees), `rect`,
`bone` (characters only). `fill` and/or `stroke` + `stroke_width`; `cap` butt/round/square;
`join` miter/round/bevel; `z` for ordering inside an entity. Units: metres, asset-local, y up.

Animated parts: `parts[]` with a `pivot` and `anim` = `oscillate` (amplitude °, frequency Hz,
phase cycles), `spin` (deg/s), or `bob` (amplitude m along `axis`). Wheels spin, canopies sway,
wings flap.

## Locations (`locations/loc_*.json`)
- `layers[]`: `order`, `parallax` (fraction of horizontal camera motion; 0 = fixed to camera, 1 =
  subject plane), `parallax_y` (vertical; default = parallax — use 1.0 for scenery seen through a
  window so it stays aligned with the window as the camera height changes), `depth` (perspective).
- `instances[]`: `asset`, `layer`, `position` or `keys`, `scale`, `velocity` (constant drift, e.g.
  scenery scrolling past a moving vehicle), `tile` {period, count} for seamless repetition,
  `important` (framing QA must keep it visible).
- `marks`: named staging points (`seat_lila: [0.62, 0.45]`) used as `"@seat_lila"` in shots.
- `background`: palette color; `ambience`: audio asset ID.

## Patterns that work
- Moving vehicle: keep the vehicle static in world space; scroll background layers with
  velocities proportional to closeness (far 0.8 m/s, mid 4 m/s, sleepers 14 m/s).
- Interiors with windows: a wall polygon with window `holes`, exterior layers behind it with low
  `parallax` and `parallax_y: 1`. Extend walls/ceilings beyond any framing you use.
- Reuse: one location serves every shot in it; per-shot additions go in `shot.props`.
- Check each location at every framing and aspect its shots use (the animatic does this).
