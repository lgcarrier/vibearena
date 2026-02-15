# afterlife_arena

Afterlife Arena mod for OpenArena/ioquake3 generated from the repo scaffold and a custom qagame patch.

Behavior:
- On death, the victim spawns configurable "afterlife ghost" entities that chase the killer.
- Ghost count is randomized between `vibe_ghost_min` and `vibe_ghost_max`.
- Ghosts are weak, short-lived, and despawn automatically after `vibe_ghost_ttl_ms`.
- On proximity to target, ghosts deal `vibe_ghost_damage` and disappear.
- On each kill, killer gains stacking speed (`vibe_kill_speed_bonus`, capped by `vibe_kill_speed_cap`).
- On each kill, killer gains leech healing (`vibe_kill_leech_hp`) with overheal cap (`vibe_kill_leech_cap`).
- Debug-visible logs print ghost spawns on death and reward values on kill.

Server cvars:
- `vibe_ghost_min` (default `1`)
- `vibe_ghost_max` (default `3`)
- `vibe_ghost_ttl_ms` (default `10000`)
- `vibe_ghost_damage` (default `10`)
- `vibe_kill_speed_bonus` (default `0.03`)
- `vibe_kill_speed_cap` (default `0.30`)
- `vibe_kill_leech_hp` (default `5`)
- `vibe_kill_leech_cap` (default `25`)

Run:
```bash
./VibeArena_Build/run_afterlife_arena.sh
```

Rebuild:
```bash
./scripts/generate_default_mod.sh afterlife_arena --variant debug-visible
```

Shared test flows for all mods are documented in `/Users/lgcarrier/Documents/coding-sandbox/vibearena/README.md` under `Reusable Mod Test Flows`.
