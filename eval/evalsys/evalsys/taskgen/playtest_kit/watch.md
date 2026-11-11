# Watch checklist

Open `playtest_out/sheet.png` (24 frames, left-to-right, top-to-bottom, evenly
spaced over the filmed replay) and, if you have a viewer, `replay.mp4`. Read
the sheet next to **your own `GDD.md`**, not next to the log: the log says
which scene the tape ended in; the sheet shows whether a player would have
seen a game happen on the way there.

Answer each line with `yes`, `no`, or `unclear`. Every `no` or `unclear` is a
fix-and-refilm item, in the order listed. `watch.py --gdd submission/GDD.md`
prints this checklist with your mechanics filled in.

## 1. Is the game visible at all?

- [ ] The first tile shows the first level, not a blank, black, or
      editor-grey frame.
- [ ] Player, floor/arena, and goal or objective are distinguishable from each
      other and from the background in every tile where they should be present.
- [ ] Nothing is off-screen or clipped that the GDD says the player needs to
      see (health, score, timer, wave counter, objective marker).

## 2. Is every mechanic in the GDD's `Mechanics` section visible?

For each mechanic you wrote down (movement, jump, attack, dash, build, pick up,
…): find at least one tile in which it is happening or has just happened.

- [ ] Mechanic: ________ — visible in tile(s) ____ ?
- [ ] Mechanic: ________ — visible in tile(s) ____ ?
- [ ] Mechanic: ________ — visible in tile(s) ____ ?
- [ ] A mechanic that never appears on the sheet either is not exercised by
      `ops.json` (the evaluator's mechanic trace will not see it either) or
      does not exist yet.

## 3. Does every action give feedback?

- [ ] Movement input changes the player's position between neighbouring tiles.
- [ ] The action/attack/dash verbs produce a visible change (projectile,
      animation frame, colour flash, particle, UI number change) — not only a
      variable change in the log.
- [ ] Damage taken or dealt is visible (health bar, hit flash, enemy removed).
- [ ] Extended actions declared in `gb_levels.json` are as visible as the
      canonical ones.

## 4. Does progression show?

- [ ] Later tiles differ from earlier ones in the way the GDD's `Progression`
      section says (new room, more enemies, higher wave number, unlocked path,
      changed score).
- [ ] Level or phase transitions declared in `gb_levels.json.levels` appear as
      distinct screens.
- [ ] The pace is plausible: the game does not sit on one unchanged frame for
      more than 2–3 consecutive tiles unless the design says so.

## 5. Is the ending shown?

- [ ] The final tile(s) show the declared success ending scene as a screen a
      player would recognise (a "victory"/"complete" screen, not the last level
      frozen).
- [ ] `FILM_VERDICT ... reached_success_ending=yes` and the sheet agree.
- [ ] `null_control.sh` printed `NULL_CONTROL self_win=no` on the same tape
      length.
- [ ] If the GDD declares a failure ending, you have seen it at least once
      (film a deliberately losing tape) and it is distinct from the success
      screen.

## 6. Honesty checks before you stop

- [ ] The replay you filmed is the exact `ops.json` you will submit.
- [ ] Nothing in the project changes behaviour based on command-line
      arguments, environment variables, or the presence of an evaluator node.
- [ ] `playtest/` is not inside `submission/`.
