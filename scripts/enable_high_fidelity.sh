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

updated=0

for profile_dir in "$DIST_DIR"/*; do
  [ -d "$profile_dir" ] || continue
  profile_name="$(basename "$profile_dir")"
  [ "$profile_name" = "ioquake3.app" ] && continue
  if ! is_profile_dir "$profile_dir"; then
    continue
  fi

  config_file="${profile_dir}/autoexec.cfg"
  cat > "$config_file" <<'EOF'
// VibeArena High Fidelity Defaults
seta cl_renderer "opengl2"
seta r_hdr "1"
seta r_toneMap "1"
seta r_sunlightMode "1"
seta r_postProcess "1"
seta r_dynamiclight "2"
seta r_shadowFilter "1"
seta r_detailtextures "1"
seta r_ext_texture_filter_anisotropic "16"
seta r_picmip "0"
echo "VibeArena High Fidelity Loaded"
EOF

  echo "Updated ${config_file}"
  updated=$((updated + 1))
done

if [ "$updated" -eq 0 ]; then
  echo "WARNING: no eligible profile directories found under $DIST_DIR" >&2
  exit 1
fi

echo
echo "Applied high-fidelity defaults to ${updated} profile(s)."
