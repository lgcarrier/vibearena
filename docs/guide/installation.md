# Installation Guide

Use this guide to install and run VibeArena on macOS.

## Prerequisites

- Xcode Command Line Tools (`xcode-select --install`)
- `bash`
- `git`
- `curl`
- `unzip`
- `zip`
- CMake `>= 3.25`

If `cmake` is missing or too old, `./scripts/build.sh` bootstraps a local CMake under `.tools/`.

## 1. Clone and Enter the Repository

```bash
git clone https://github.com/lgcarrier/vibearena.git
cd vibearena
```

If you already have the repository, run all commands below from the repo root.

## 2. Build VibeArena

```bash
./scripts/build.sh
```

This builds `ioquake3`, downloads OpenArena 0.8.8 assets, assembles `VibeArena_Build/`, and runs dry-run verification.

## 3. Run the Game

```bash
./VibeArena_Build/play.sh
```

## 4. Verify Installation

`./scripts/build.sh` writes verification logs in the repo root.

`verify_client.log` should include:

- `Initializing OpenGL display`
- `Sound initialization successful`
- `Client Shutdown (Client quit)`

If client verification cannot complete in restricted or headless environments, the script runs dedicated-server fallback verification and writes `verify_dedicated.log`.

## Optional Next Step: Generate a Default Mod

```bash
./scripts/generate_default_mod.sh
./VibeArena_Build/run_bounce_twice_rockets.sh
```
