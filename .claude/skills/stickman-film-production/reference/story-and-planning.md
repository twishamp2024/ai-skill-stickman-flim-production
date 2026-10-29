# Story analysis and planning

## Reading the story
1. Copy the story verbatim to `story/story.md`. It is the reference for meaning and dialogue.
2. Read to the end before designing anything (endings change what openings must set up).
3. Build `story/brief.json`:
   - `events[]`: one per action, reaction, reveal or line-carrying beat, in story order. Keep them
     small enough that each maps to one or two shots. `importance` drives coverage QA.
   - `characters[]`: `stated_gender` is what the text says ("not stated" is valid). Never infer.
   - `emotional_arc[]`: emotion per key event; guides pacing, music and framing.
   - `ambiguities[]`: issue, resolution, `blocking` (true → ask the user before production).
   - `event_to_scene`: traceability from story to scenes.

## Assumptions vs questions
Make and record an assumption when the choice does not change meaning (seat layout, an unnamed
minor character's look, default format). Ask when it would: an unclear speaker, contradictory
facts, whether a line is spoken aloud or thought, a real person's voice/likeness.

## Scene and shot decomposition
- A **scene** = one location and continuous time. A **shot** = one camera setup (it may move).
- Start from events: which must be *seen* (action, reaction, reveal) and which can be *heard*.
- Give every shot a `purpose` sentence. If you cannot, merge it.
- Duration: action complexity + dialogue (generated length + ≥0.35 s tail) + reaction time
  (≥0.6 s on emotional beats). Mark dialogue shots `timing.flex` with a `max_s` so generated
  speech can extend them; the pipeline never time-stretches or truncates speech.
- Cover reactions: a question needs the listener's face soon after.
- Avoid fragmenting: consecutive shots of the same framing and subject are usually one shot.

## Inventory and reuse
- One asset per real thing; instances position it. Repeated environment pieces get
  `tileable_period` so they scroll seamlessly.
- Composite assets (`ast_tree_row`) declare `composed_of` so changes propagate through `impact`.
- Props a character handles are shot props (`prop_*`) with `grip` points; `pick_up`/`put_down`
  actions attach and detach them.

## Coverage checklist (validated automatically)
Every essential event covered by ≥1 shot; every shot in exactly one scene; scenes ordered by
`production.sequence`; every dialogue line anchored to a shot whose cast includes the speaker
(or `offscreen: true`).
