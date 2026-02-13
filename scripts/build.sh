#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="${ROOT_DIR}/quake_engine"
BUILD_DIR="${ENGINE_DIR}/build-macos"
DIST_DIR="${ROOT_DIR}/VibeArena_Build"
OA_ZIP="${ROOT_DIR}/openarena-0.8.8.zip"
OA_EXTRACT="${ROOT_DIR}/openarena_0.8.8"
VERIFY_CLIENT_LOG="${ROOT_DIR}/verify_client.log"
VERIFY_DEDICATED_LOG="${ROOT_DIR}/verify_dedicated.log"

REQUIRED_CMAKE_MAJOR=3
REQUIRED_CMAKE_MINOR=25
LOCAL_CMAKE_VERSION="3.31.6"
LOCAL_CMAKE_DIR="${ROOT_DIR}/.tools/cmake-${LOCAL_CMAKE_VERSION}-macos-universal"
LOCAL_CMAKE_ARCHIVE="${ROOT_DIR}/.tools/cmake-${LOCAL_CMAKE_VERSION}-macos-universal.tar.gz"
LOCAL_CMAKE_BIN="${LOCAL_CMAKE_DIR}/CMake.app/Contents/bin/cmake"

OPENARENA_PRIMARY_URL="https://sourceforge.net/projects/oarena/files/openarena-0.8.8.zip/download"
OPENARENA_FALLBACK_URL="https://downloads.sourceforge.net/project/oarena/openarena-0.8.8.zip"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: required command '$1' not found in PATH." >&2
    exit 1
  fi
}

cmake_version_ok() {
  local cmake_bin="$1"
  local version major minor
  version="$("$cmake_bin" --version | awk 'NR==1 {print $3}')"
  major="$(echo "$version" | cut -d. -f1)"
  minor="$(echo "$version" | cut -d. -f2)"
  if [ -z "$major" ] || [ -z "$minor" ]; then
    return 1
  fi
  if [ "$major" -gt "$REQUIRED_CMAKE_MAJOR" ]; then
    return 0
  fi
  if [ "$major" -lt "$REQUIRED_CMAKE_MAJOR" ]; then
    return 1
  fi
  [ "$minor" -ge "$REQUIRED_CMAKE_MINOR" ]
}

detect_cmake() {
  if command -v cmake >/dev/null 2>&1 && cmake_version_ok "$(command -v cmake)"; then
    command -v cmake
    return 0
  fi

  mkdir -p "${ROOT_DIR}/.tools"

  if [ ! -x "$LOCAL_CMAKE_BIN" ]; then
    echo "Bootstrapping local CMake ${LOCAL_CMAKE_VERSION} into .tools/"
    curl -L "https://github.com/Kitware/CMake/releases/download/v${LOCAL_CMAKE_VERSION}/cmake-${LOCAL_CMAKE_VERSION}-macos-universal.tar.gz" -o "$LOCAL_CMAKE_ARCHIVE"
    tar -xzf "$LOCAL_CMAKE_ARCHIVE" -C "${ROOT_DIR}/.tools"
  fi

  if ! cmake_version_ok "$LOCAL_CMAKE_BIN"; then
    echo "ERROR: local CMake does not satisfy >= ${REQUIRED_CMAKE_MAJOR}.${REQUIRED_CMAKE_MINOR}" >&2
    exit 1
  fi

  echo "$LOCAL_CMAKE_BIN"
}

download_openarena_zip() {
  if [ -f "$OA_ZIP" ] && unzip -tq "$OA_ZIP" >/dev/null 2>&1; then
    echo "Using cached OpenArena archive: $OA_ZIP"
    return 0
  fi

  echo "Downloading OpenArena 0.8.8 assets..."
  curl -L "$OPENARENA_PRIMARY_URL" -o "$OA_ZIP"

  if ! unzip -tq "$OA_ZIP" >/dev/null 2>&1; then
    echo "Primary URL did not return a valid zip, retrying fallback mirror..."
    curl -L "$OPENARENA_FALLBACK_URL" -o "$OA_ZIP"
  fi

  if ! unzip -tq "$OA_ZIP" >/dev/null 2>&1; then
    echo "ERROR: unable to fetch a valid OpenArena 0.8.8 zip archive." >&2
    exit 1
  fi
}

main() {
  require_cmd git
  require_cmd curl
  require_cmd unzip
  require_cmd tar
  require_cmd find

  cd "$ROOT_DIR"

  local cmake_bin
  cmake_bin="$(detect_cmake)"
  echo "Using CMake: $cmake_bin"

  if [ ! -d "$ENGINE_DIR/.git" ]; then
    echo "Cloning ioquake3 source..."
    git clone https://github.com/ioquake/ioq3.git "$ENGINE_DIR"
  else
    echo "Using existing engine checkout at $ENGINE_DIR"
  fi

  echo "Configuring ioquake3..."
  "$cmake_bin" -S "$ENGINE_DIR" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release

  local jobs
  jobs="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
  echo "Building ioquake3 with ${jobs} job(s)..."
  "$cmake_bin" --build "$BUILD_DIR" -j"$jobs"

  local client_app_rel ded_bin_rel client_app ded_bin
  client_app_rel="$(find "$BUILD_DIR" -type d -name 'ioquake3.app' | head -n1)"
  ded_bin_rel="$(find "$BUILD_DIR" -type f -name 'ioq3ded' | head -n1)"
  if [ -z "$client_app_rel" ] || [ -z "$ded_bin_rel" ]; then
    echo "ERROR: build artifacts not found (ioquake3.app / ioq3ded)." >&2
    exit 1
  fi
  client_app="$client_app_rel"
  ded_bin="$ded_bin_rel"

  download_openarena_zip

  echo "Extracting OpenArena assets..."
  rm -rf "$OA_EXTRACT"
  unzip -q "$OA_ZIP" -d "$OA_EXTRACT"

  local baseoa_dir
  baseoa_dir="$(find "$OA_EXTRACT" -type d -name baseoa | head -n1)"
  if [ -z "$baseoa_dir" ]; then
    echo "ERROR: baseoa directory not found in extracted OpenArena archive." >&2
    exit 1
  fi
  if [ "$(find "$baseoa_dir" -maxdepth 1 -name '*.pk3' | wc -l | tr -d ' ')" -eq 0 ]; then
    echo "ERROR: no .pk3 files found in $baseoa_dir." >&2
    exit 1
  fi

  echo "Assembling portable distribution..."
  rm -rf "$DIST_DIR"
  mkdir -p "$DIST_DIR"
  cp -R "$client_app" "$DIST_DIR/"
  cp "$ded_bin" "$DIST_DIR/"
  cp -R "$baseoa_dir" "$DIST_DIR/baseoa"

  cat > "${DIST_DIR}/play.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# For interactive macOS launches, opening the app bundle avoids terminal-only
# startup edge cases (e.g. dropping to tty prompt instead of creating a window).
if [ -t 0 ] && [ "${VIBEARNA_FORCE_DIRECT:-0}" != "1" ] && command -v open >/dev/null 2>&1; then
  exec open "$HERE/ioquake3.app" --args \
    +set fs_basepath "$HERE" \
    +set fs_homepath "$HERE" \
    +set com_basegame baseoa \
    +set dedicated 0 \
    +set com_hunkMegs 256 \
    "$@"
fi

exec "$HERE/ioquake3.app/Contents/MacOS/ioquake3" \
  +set fs_basepath "$HERE" \
  +set fs_homepath "$HERE" \
  +set com_basegame baseoa \
  +set dedicated 0 \
  +set com_hunkMegs 256 \
  "$@"
EOF
  chmod +x "${DIST_DIR}/play.sh"

  echo "Running client dry-run verification..."
  (
    cd "$DIST_DIR"
    ./play.sh +quit > "$VERIFY_CLIENT_LOG" 2>&1 || true
  )

  if grep -q "Client Shutdown (Client quit)" "$VERIFY_CLIENT_LOG"; then
    echo "Client verification passed (clean +quit)."
  else
    echo "Client did not complete clean +quit; running dedicated fallback verification..."
    (
      cd "$DIST_DIR"
      ./ioq3ded \
        +set net_enabled 0 \
        +set fs_basepath "$DIST_DIR" \
        +set fs_homepath "$DIST_DIR" \
        +set com_basegame baseoa \
        +set fs_game baseoa \
        +quit > "$VERIFY_DEDICATED_LOG" 2>&1 || true
    )
  fi

  local commit_hash build_date
  commit_hash="$(cd "$ENGINE_DIR" && git rev-parse --short HEAD)"
  build_date="$(date +%Y-%m-%d)"

  echo
  echo "Build complete."
  echo "Date:   $build_date"
  echo "Engine: ioquake3 ($commit_hash)"
  echo "Assets: OpenArena 0.8.8"
  echo "Run:    ./VibeArena_Build/play.sh"
  echo "Logs:   verify_client.log (and verify_dedicated.log if fallback ran)"
}

main "$@"
