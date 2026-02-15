#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/VibeArena_Build"

is_profile_dir() {
  local dir="$1"
  local name
  name="$(basename "$dir")"

  if [ "$name" = "baseoa" ]; then
    return 0
  fi

  if find "$dir" -maxdepth 1 -type f -name 'z_*.pk3' | grep -q .; then
    return 0
  fi

  return 1
}

if [ ! -d "$DIST_DIR" ]; then
  echo "ERROR: distribution directory not found: $DIST_DIR" >&2
  exit 1
fi

removed=0
skipped=0

for profile_dir in "$DIST_DIR"/*; do
  [ -d "$profile_dir" ] || continue
  profile_name="$(basename "$profile_dir")"
  [ "$profile_name" = "ioquake3.app" ] && continue
  if ! is_profile_dir "$profile_dir"; then
    continue
  fi

  config_file="${profile_dir}/autoexec.cfg"
  if [ ! -f "$config_file" ]; then
    continue
  fi

  if grep -q 'VibeArena High Fidelity Defaults' "$config_file"; then
    rm -f "$config_file"
    echo "Removed ${config_file}"
    removed=$((removed + 1))
  else
    echo "Skipped ${config_file} (not VibeArena-managed)" >&2
    skipped=$((skipped + 1))
  fi
done

echo
echo "Removed high-fidelity autoexec from ${removed} profile(s)."
if [ "$skipped" -gt 0 ]; then
  echo "Skipped ${skipped} non-managed autoexec file(s)." >&2
fi
