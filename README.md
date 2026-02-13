# VibeArena

One-command macOS pipeline to build `ioquake3`, package `OpenArena 0.8.8` assets, and produce a portable local install.

## Legal Snapshot

- This project is not affiliated with or endorsed by id Software, Bethesda, or ZeniMax.
- `Quake` is a trademark of its respective owner; this repo uses `ioquake3` + OpenArena as open-source technology and assets.
- Publish source and patches, not packaged proprietary content.

## Why This Project Exists

This project was created to make classic arena FPS development and mod testing reproducible on modern macOS without manual setup drift.

Problems it solves:

- Building `ioquake3` from source is easy to get wrong or forget between machines.
- Asset setup (`baseoa`) is usually a manual, error-prone step.
- New contributors need a reliable "works out of the box" local environment.
- Sharing a repo is cleaner when large generated artifacts are excluded by default.

In short: this repo turns a multi-step, brittle setup into a repeatable one-command workflow that any contributor can run and verify.

## TL;DR

From repository root:

```bash
./scripts/build.sh
./VibeArena_Build/play.sh
```

## What This Project Produces

After a successful build, you get:

- `VibeArena_Build/ioquake3.app`
- `VibeArena_Build/ioq3ded`
- `VibeArena_Build/baseoa/*.pk3`
- `VibeArena_Build/play.sh`
- `verify_client.log` (and optionally `verify_dedicated.log`)

`VibeArena_Build/` is the portable output folder.

## Requirements (macOS)

- Xcode Command Line Tools (`xcode-select --install`)
- `bash`
- `git`
- `curl`
- `unzip`
- `zip`
- CMake `>= 3.25`

If `cmake` is missing or too old, `scripts/build.sh` automatically bootstraps a local CMake under `.tools/`.

## One-Command Build

```bash
./scripts/build.sh
```

The script handles:

- ioquake3 clone/reuse + CMake build
- OpenArena 0.8.8 download + extraction
- distribution assembly into `VibeArena_Build/`
- launcher generation (`play.sh`)
- dry-run verification (`+quit`)

## Generate a Default Vibe Mod

Generate a starter mod that changes rocket behavior:

```bash
./scripts/generate_default_mod.sh
```

This creates a mod named `bounce_twice_rockets` with:

- rocket launcher projectiles bouncing twice before exploding
- generated patch source at `mods/bounce_twice_rockets/patches/rocket_bounce_twice.patch`
- built VM at `mods/bounce_twice_rockets/build/vm/qagame.qvm`
- packaged mod at `VibeArena_Build/bounce_twice_rockets/z_bounce_twice_rockets.pk3`
- ready launcher `VibeArena_Build/run_bounce_twice_rockets.sh`

Run the default mod (exact flow, from repo root):

```bash
cd /Users/lgcarrier/Documents/coding-sandbox/hallucination-arena
./scripts/build.sh
./scripts/generate_default_mod.sh
./VibeArena_Build/run_bounce_twice_rockets.sh
```

Optional: start directly in a test map:

```bash
./VibeArena_Build/run_bounce_twice_rockets.sh +devmap oa_dm1
```

Generate a debug-visible variant (recommended for quick confirmation while playing):

```bash
./scripts/generate_default_mod.sh bounce_twice_rockets_debug --variant debug-visible
./VibeArena_Build/run_bounce_twice_rockets_debug.sh
```

The `debug-visible` variant adds:

- very slow rockets (`900 -> 120`)
- bounce on any impact (world or player) with higher bounce budget
- chat/console and center-screen bounce messages
- rocket damage disabled for easier visual debugging
- extra visible bounce impact effect

Run the debug-visible variant:

```bash
./VibeArena_Build/run_bounce_twice_rockets_debug.sh
```

The generated launcher forces:

- `fs_homepath` to `VibeArena_Build/` (avoids stale user-home mod files overriding your build)
- `dedicated 0` (prevents accidental server-console mode / `tty]` startup)
- `vm_game 2` (forces VM/QVM game logic path; avoids native game module override)

Custom mod name:

```bash
./scripts/generate_default_mod.sh my_vibe_mod
./VibeArena_Build/run_my_vibe_mod.sh
```

Quick verification that your mod VM is loaded:

```bash
cd VibeArena_Build
./ioq3ded +set fs_basepath "$(pwd)" +set fs_homepath "$(pwd)" +set com_basegame baseoa +set fs_game bounce_twice_rockets +set vm_game 2 +set net_enabled 0 +map oa_dm1 +quit
```

Look for:

- `File "vm/qagame.qvm" found in ".../bounce_twice_rockets/...pk3"`

If you see this startup popup instead:

- `Client/Server game mismatch: baseoa-1/baseq3-1`

your generated mod VM was built with Quake 3 game version strings. Rebuild the mod with the current generator:

```bash
./scripts/generate_default_mod.sh bounce_twice_rockets
```

Optional sanity check:

```bash
strings mods/bounce_twice_rockets/build/vm/qagame.qvm | grep -E "baseoa-1|baseq3-1"
```

Expected result: `baseoa-1` is present and `baseq3-1` is absent.

## Running the Game

```bash
./VibeArena_Build/play.sh
```

On macOS, `play.sh` launches the app bundle through `open` for interactive runs.
If you need direct terminal-attached execution (for debugging), use:

```bash
VIBEARNA_FORCE_DIRECT=1 ./VibeArena_Build/play.sh
```

You can pass extra engine arguments:

```bash
./VibeArena_Build/play.sh +set r_mode -1
```

To sync fullscreen/window settings across `baseoa` and all mod profiles:

```bash
./scripts/set_video_defaults.sh
```

By default this updates:

- `baseoa`
- mod profile directories that contain packaged `z_*.pk3` files

To force updating every subdirectory under `VibeArena_Build/`:

```bash
./scripts/set_video_defaults.sh --include-all
```

Common overrides:

```bash
./scripts/set_video_defaults.sh --fullscreen 0 --mode 3
./scripts/set_video_defaults.sh --width 1920 --height 1080
```

## Why OpenArena Launch Flags Are Set

`ioquake3` normally expects Quake 3 retail data in `baseq3`.  
For OpenArena packaging, launcher flags are pinned to `baseoa`:

- `+set com_basegame baseoa`
- `+set dedicated 0`

`play.sh` intentionally does not force `fs_game`; this avoids the noisy `fs_game is write protected` warning and keeps base OA as default.

## Verification Expectations

`scripts/build.sh` writes verification logs in repo root.

`verify_client.log` should include:

- `Initializing OpenGL display`
- `Sound initialization successful`
- `Client Shutdown (Client quit)`

If client verification cannot complete in restricted/headless environments, the script runs dedicated-server fallback verification and writes `verify_dedicated.log`.

## Modding Hook

Drop additional `.pk3` files into:

```text
VibeArena_Build/baseoa/
```

## Vibe Coding Workflow (Recommended)

Use this setup as a fast "edit -> package -> run" loop.

1. Create a mod workspace in the repo (source-controlled):

```text
mods/<your_mod_name>/
```

2. Add game content using id Tech 3/OpenArena paths, for example:

```text
mods/<your_mod_name>/scripts/
mods/<your_mod_name>/maps/
mods/<your_mod_name>/textures/
mods/<your_mod_name>/sound/
mods/<your_mod_name>/vm/
```

3. Package your current mod files into a dev `.pk3`:

```bash
mkdir -p VibeArena_Build/<your_mod_name>
cd mods/<your_mod_name>
zip -r ../../VibeArena_Build/<your_mod_name>/z_vibe_dev.pk3 .
cd ../../
```

4. Launch your isolated mod while still using OpenArena as base content:

```bash
./VibeArena_Build/play.sh +set fs_game <your_mod_name>
```

If you want a working gameplay-code starting point, first run:

```bash
./scripts/generate_default_mod.sh <your_mod_name>
./VibeArena_Build/run_<your_mod_name>.sh
```

Because `play.sh` already sets `com_basegame baseoa`, your mod can override/add content while still inheriting OpenArena assets.

5. Repeat quickly:

- Edit files under `mods/<your_mod_name>/`
- Rebuild `z_vibe_dev.pk3`
- Relaunch and test

## Choosing Between `baseoa` and `fs_game` Modding

- Use `baseoa` direct drops (`VibeArena_Build/baseoa/*.pk3`) for very quick experiments.
- Use isolated `fs_game` mods (`VibeArena_Build/<your_mod_name>/`) for real development, cleaner diffs, and safer sharing.

## Suggested Vibe Iteration Commands

Use these launch flags while testing:

```bash
./VibeArena_Build/play.sh \
  +set fs_game <your_mod_name> \
  +set developer 1 \
  +set logfile 2
```

Helpful pattern for map work:

```bash
./VibeArena_Build/play.sh +set fs_game <your_mod_name> +devmap <map_name>
```

## Sharing Mods

For GitHub sharing, commit mod source files in `mods/<your_mod_name>/` and do not commit generated build output from `VibeArena_Build/` (already ignored by `.gitignore`).

## Publishing Checklist

Before pushing to GitHub:

1. Run `./scripts/build.sh` once to verify a clean reproducible build.
2. Run `./scripts/set_video_defaults.sh` if you want consistent fullscreen defaults locally.
3. Ensure `git status --short` does not include generated artifacts.
4. Commit only source/docs/scripts (`scripts/`, `mods/<name>/patches`, `README.md`, `.gitignore`).
5. Do not commit `VibeArena_Build/`, `quake_engine/`, downloaded archives, or logs.

## Maintenance

Re-run a clean rebuild at any time:

```bash
./scripts/build.sh
```

The script recreates `VibeArena_Build/` and refreshes assets/build outputs.

## Repository Layout

- `scripts/build.sh` - full build/package/verify workflow
- `scripts/generate_default_mod.sh` - generates a starter gameplay mod and packages it as a `.pk3`
- `scripts/set_video_defaults.sh` - synchronizes video defaults across local profiles
- `README.md` - project docs
- `LICENSE` - GPLv2 license text
- `.gitignore` - excludes generated binaries, downloads, logs, and tool cache

Ignored generated paths include:

- `.tools/`
- `.tmp/`
- `quake_engine/`
- `openarena-0.8.8.zip`
- `openarena_0.8.8/`
- `VibeArena_Build/`
- `verify_*.log`
- `mods/*/build/`
- `mods/*/*.pk3`

## Last Verified Build

- Build Date: `2026-02-13`
- Engine: `ioquake3` commit `30912dd0`
- Assets: `OpenArena 0.8.8`
- Host: `macOS arm64`

## License and Data Notes

- This repository is provided under GPLv2 (see `LICENSE`).
- `ioquake3` engine is GPLv2.
- OpenArena assets are distributed separately; verify redistribution terms before publishing packaged builds.
- Quake 3 retail `baseq3` data is not included and not required for this OpenArena setup.
