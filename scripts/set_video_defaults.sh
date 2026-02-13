#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/VibeArena_Build"

R_MODE="-2"
R_FULLSCREEN="1"
R_NOBORDER="0"
R_CUSTOMWIDTH=""
R_CUSTOMHEIGHT=""
INCLUDE_ALL="0"

usage() {
  cat <<'EOF'
Usage: ./scripts/set_video_defaults.sh [options]

Options:
  --dist <path>         Distribution directory (default: ./VibeArena_Build)
  --mode <value>        r_mode value (default: -2)
  --fullscreen <0|1>    r_fullscreen value (default: 1)
  --noborder <0|1>      r_noborder value (default: 0)
  --width <pixels>      Optional r_customwidth value
  --height <pixels>     Optional r_customheight value
  --include-all         Include every subdirectory under dist (unsafe)
  -h, --help            Show help

Examples:
  ./scripts/set_video_defaults.sh
  ./scripts/set_video_defaults.sh --fullscreen 0 --mode 3
  ./scripts/set_video_defaults.sh --width 1920 --height 1080
  ./scripts/set_video_defaults.sh --include-all
EOF
}

require_number() {
  local value="$1"
  local label="$2"
  if [[ ! "$value" =~ ^-?[0-9]+$ ]]; then
    echo "ERROR: $label must be an integer, got '$value'." >&2
    exit 1
  fi
}

require_bool01() {
  local value="$1"
  local label="$2"
  if [ "$value" != "0" ] && [ "$value" != "1" ]; then
    echo "ERROR: $label must be 0 or 1, got '$value'." >&2
    exit 1
  fi
}

upsert_cvar() {
  local file="$1"
  local key="$2"
  local value="$3"

  # Replace existing `set`/`seta` entry if present.
  KEY="$key" VALUE="$value" perl -0pi -e '
    s/^(?:seta|set)\s+\Q$ENV{KEY}\E\s+(".*?"|\S+)\s*$/seta $ENV{KEY} "$ENV{VALUE}"/mg
  ' "$file"

  # Append if not present after replacement.
  if ! grep -Eq "^(seta|set)[[:space:]]+${key}[[:space:]]+" "$file"; then
    printf 'seta %s "%s"\n' "$key" "$value" >> "$file"
  fi
}

is_profile_dir() {
  local dir="$1"
  local name
  name="$(basename "$dir")"

  if [ "$INCLUDE_ALL" = "1" ]; then
    return 0
  fi

  # baseoa is always a valid profile.
  if [ "$name" = "baseoa" ]; then
    return 0
  fi

  # Mod profiles created by the generator contain packaged z_*.pk3 payloads.
  if find "$dir" -maxdepth 1 -type f -name 'z_*.pk3' | grep -q .; then
    return 0
  fi

  return 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dist)
      shift
      [ $# -gt 0 ] || { echo "ERROR: --dist requires a value." >&2; exit 1; }
      DIST_DIR="$1"
      ;;
    --mode)
      shift
      [ $# -gt 0 ] || { echo "ERROR: --mode requires a value." >&2; exit 1; }
      R_MODE="$1"
      ;;
    --fullscreen)
      shift
      [ $# -gt 0 ] || { echo "ERROR: --fullscreen requires a value." >&2; exit 1; }
      R_FULLSCREEN="$1"
      ;;
    --noborder)
      shift
      [ $# -gt 0 ] || { echo "ERROR: --noborder requires a value." >&2; exit 1; }
      R_NOBORDER="$1"
      ;;
    --width)
      shift
      [ $# -gt 0 ] || { echo "ERROR: --width requires a value." >&2; exit 1; }
      R_CUSTOMWIDTH="$1"
      ;;
    --height)
      shift
      [ $# -gt 0 ] || { echo "ERROR: --height requires a value." >&2; exit 1; }
      R_CUSTOMHEIGHT="$1"
      ;;
    --include-all)
      INCLUDE_ALL="1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument '$1'." >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

require_number "$R_MODE" "r_mode"
require_bool01 "$R_FULLSCREEN" "r_fullscreen"
require_bool01 "$R_NOBORDER" "r_noborder"

if [ -n "$R_CUSTOMWIDTH" ]; then
  require_number "$R_CUSTOMWIDTH" "r_customwidth"
fi
if [ -n "$R_CUSTOMHEIGHT" ]; then
  require_number "$R_CUSTOMHEIGHT" "r_customheight"
fi

if [ -n "$R_CUSTOMWIDTH" ] && [ -z "$R_CUSTOMHEIGHT" ]; then
  echo "ERROR: --width requires --height." >&2
  exit 1
fi
if [ -n "$R_CUSTOMHEIGHT" ] && [ -z "$R_CUSTOMWIDTH" ]; then
  echo "ERROR: --height requires --width." >&2
  exit 1
fi

if [ ! -d "$DIST_DIR" ]; then
  echo "ERROR: distribution directory not found: $DIST_DIR" >&2
  exit 1
fi

updated=0

for profile_dir in "$DIST_DIR"/*; do
  [ -d "$profile_dir" ] || continue
  profile_name="$(basename "$profile_dir")"
  [ "$profile_name" = "ioquake3.app" ] && continue
  if ! is_profile_dir "$profile_dir"; then
    continue
  fi

  config_file="${profile_dir}/q3config.cfg"
  [ -f "$config_file" ] || touch "$config_file"

  upsert_cvar "$config_file" "r_mode" "$R_MODE"
  upsert_cvar "$config_file" "r_fullscreen" "$R_FULLSCREEN"
  upsert_cvar "$config_file" "r_noborder" "$R_NOBORDER"

  if [ -n "$R_CUSTOMWIDTH" ]; then
    upsert_cvar "$config_file" "r_customwidth" "$R_CUSTOMWIDTH"
    upsert_cvar "$config_file" "r_customheight" "$R_CUSTOMHEIGHT"
  fi

  echo "Updated ${config_file}"
  updated=$((updated + 1))
done

if [ "$updated" -eq 0 ]; then
  echo "WARNING: no eligible profile directories found under $DIST_DIR" >&2
  if [ "$INCLUDE_ALL" != "1" ]; then
    echo "Hint: use --include-all to force updating every subdirectory." >&2
  fi
  exit 1
fi

echo
echo "Applied video defaults to ${updated} profile(s):"
echo "  r_mode=${R_MODE}"
echo "  r_fullscreen=${R_FULLSCREEN}"
echo "  r_noborder=${R_NOBORDER}"
if [ -n "$R_CUSTOMWIDTH" ]; then
  echo "  r_customwidth=${R_CUSTOMWIDTH}"
  echo "  r_customheight=${R_CUSTOMHEIGHT}"
fi
