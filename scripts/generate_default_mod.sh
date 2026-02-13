#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="${ROOT_DIR}/quake_engine"
DIST_DIR="${ROOT_DIR}/VibeArena_Build"
DEFAULT_MOD_NAME="bounce_twice_rockets"
DEFAULT_VARIANT="default"
MOD_NAME="$DEFAULT_MOD_NAME"
MOD_NAME_SET=0
MOD_VARIANT="$DEFAULT_VARIANT"
MAINMENU_IMAGE_ARG=""
MAINMENU_STATUS="disabled (no image provided)"

while [ $# -gt 0 ]; do
  case "$1" in
    --variant)
      shift
      if [ $# -eq 0 ]; then
        echo "ERROR: --variant requires a value: default or debug-visible." >&2
        exit 1
      fi
      MOD_VARIANT="$1"
      ;;
    --debug-visible)
      MOD_VARIANT="debug-visible"
      ;;
    --mainmenu-image)
      shift
      if [ $# -eq 0 ]; then
        echo "ERROR: --mainmenu-image requires a path to an image file." >&2
        exit 1
      fi
      MAINMENU_IMAGE_ARG="$1"
      ;;
    *)
      if [ "$MOD_NAME_SET" -eq 0 ]; then
        MOD_NAME="$1"
        MOD_NAME_SET=1
      else
        echo "ERROR: unexpected argument '$1'." >&2
        echo "Usage: ./scripts/generate_default_mod.sh [mod_name] [--variant default|debug-visible] [--mainmenu-image /path/to/image]" >&2
        exit 1
      fi
      ;;
  esac
  shift
done

REQUIRED_CMAKE_MAJOR=3
REQUIRED_CMAKE_MINOR=25
LOCAL_CMAKE_VERSION="3.31.6"
LOCAL_CMAKE_DIR="${ROOT_DIR}/.tools/cmake-${LOCAL_CMAKE_VERSION}-macos-universal"
LOCAL_CMAKE_ARCHIVE="${ROOT_DIR}/.tools/cmake-${LOCAL_CMAKE_VERSION}-macos-universal.tar.gz"
LOCAL_CMAKE_BIN="${LOCAL_CMAKE_DIR}/CMake.app/Contents/bin/cmake"

MOD_DIR="${ROOT_DIR}/mods/${MOD_NAME}"
PATCH_DIR="${MOD_DIR}/patches"
PATCH_FILE="${PATCH_DIR}/rocket_bounce_twice.patch"
MOD_BUILD_DIR="${MOD_DIR}/build"
MOD_VM_DIR="${MOD_BUILD_DIR}/vm"
MOD_DIST_DIR="${DIST_DIR}/${MOD_NAME}"
MOD_PK3="${MOD_DIST_DIR}/z_${MOD_NAME}.pk3"
MOD_LAUNCHER="${DIST_DIR}/run_${MOD_NAME}.sh"
MOD_MAINMENU_ASSETS_DIR="${MOD_DIR}/assets/mainmenu"

PK3_ASSET_DIRS=(
  "scripts"
  "maps"
  "textures"
  "sound"
  "models"
  "music"
  "gfx"
  "ui"
  "fonts"
  "sprites"
  "env"
  "video"
)

TMP_ROOT="${ROOT_DIR}/.tmp/modgen-${MOD_NAME}-$$"
TMP_ENGINE="${TMP_ROOT}/engine"
TMP_BUILD="${TMP_ENGINE}/build-mod"
WORKTREE_ADDED=0

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

cleanup() {
  if [ "$WORKTREE_ADDED" -eq 1 ]; then
    git -C "$ENGINE_DIR" worktree remove --force "$TMP_ENGINE" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_ROOT"
}

write_patch_file() {
  mkdir -p "$PATCH_DIR"
  if [ "$MOD_VARIANT" = "debug-visible" ]; then
    cat > "$PATCH_FILE" <<'PATCH'
diff --git a/code/game/g_missile.c b/code/game/g_missile.c
--- a/code/game/g_missile.c
+++ b/code/game/g_missile.c
@@ -279,7 +279,36 @@ void G_MissileImpact( gentity_t *ent, trace_t *trace ) {
-	// check for bounce
+	/* debug-visible rockets bounce on any impact for clear visual feedback */
+	if ( ent->s.weapon == WP_ROCKET_LAUNCHER &&
+		( ent->s.eFlags & ( EF_BOUNCE | EF_BOUNCE_HALF ) ) ) {
+		G_Printf( "[vibe-mod-debug] rocket impact, bounces left: %d\n", ent->count );
+		if ( ent->count <= 0 ) {
+			G_ExplodeMissile( ent );
+			return;
+		}
+		ent->count--;
+		trap_SendServerCommand( -1, va(
+			"print \"^3[VIBE DEBUG]^7 rocket bounce, %d left\n\"",
+			ent->count ) );
+		trap_SendServerCommand( -1, va(
+			"cp \"^3[VIBE DEBUG]^7 rocket bounce, %d left\"",
+			ent->count ) );
+		G_BounceMissile( ent, trace );
+		VectorMA( ent->r.currentOrigin, 12.0f, trace->plane.normal, ent->r.currentOrigin );
+		VectorCopy( ent->r.currentOrigin, ent->s.pos.trBase );
+		VectorScale( ent->s.pos.trDelta, 0.7f, ent->s.pos.trDelta );
+		if ( ent->s.pos.trDelta[2] < 170.0f ) {
+			ent->s.pos.trDelta[2] = 170.0f;
+		}
+		SnapVector( ent->s.pos.trDelta );
+		G_AddEvent( ent, EV_MISSILE_MISS, DirToByte( trace->plane.normal ) );
+		G_AddEvent( ent, EV_MISSILE_HIT, DirToByte( trace->plane.normal ) );
+		G_AddEvent( ent, EV_GRENADE_BOUNCE, 0 );
+		return;
+	}
+
+	// check for bounce (non-rocket projectiles)
 	if ( !other->takedamage &&
 		( ent->s.eFlags & ( EF_BOUNCE | EF_BOUNCE_HALF ) ) ) {
 		G_BounceMissile( ent, trace );
 		G_AddEvent( ent, EV_GRENADE_BOUNCE, 0 );
 		return;
 	}
@@ -663,11 +692,16 @@ gentity_t *fire_rocket (gentity_t *self, vec3_t start, vec3_t dir) {
 	bolt->splashMethodOfDeath = MOD_ROCKET_SPLASH;
 	bolt->clipmask = MASK_SHOT;
 	bolt->target_ent = NULL;
+	bolt->s.eFlags |= EF_BOUNCE;
+	bolt->count = 6;
+	bolt->damage = 0;
+	bolt->splashDamage = 0;
+	G_Printf( "[vibe-mod-debug] spawned rocket with bounce budget %d\n", bolt->count );
 
 	bolt->s.pos.trType = TR_LINEAR;
 	bolt->s.pos.trTime = level.time - MISSILE_PRESTEP_TIME;		// move a bit on the very first frame
 	VectorCopy( start, bolt->s.pos.trBase );
-	VectorScale( dir, 900, bolt->s.pos.trDelta );
+	VectorScale( dir, 120, bolt->s.pos.trDelta );
 	SnapVector( bolt->s.pos.trDelta );			// save net bandwidth
 	VectorCopy (start, bolt->r.currentOrigin);

diff --git a/code/game/g_local.h b/code/game/g_local.h
--- a/code/game/g_local.h
+++ b/code/game/g_local.h
@@ -30,7 +30,7 @@
 //==================================================================
 
 // the "gameversion" client command will print this plus compile date
-#define	GAMEVERSION	BASEGAME
+#define	GAMEVERSION	"baseoa"
 
 #define BODY_QUEUE_SIZE		8
 
diff --git a/code/game/bg_public.h b/code/game/bg_public.h
--- a/code/game/bg_public.h
+++ b/code/game/bg_public.h
@@ -28,7 +28,7 @@
-#define	GAME_VERSION		BASEGAME "-1"
+#define	GAME_VERSION		"baseoa-1"
 
 #define	DEFAULT_GRAVITY		800
 #define	GIB_HEALTH			-40
 #define	ARMOR_PROTECTION	0.66
 
 #define	MAX_ITEMS			256
 
 
PATCH
  else
    cat > "$PATCH_FILE" <<'PATCH'
diff --git a/code/game/g_missile.c b/code/game/g_missile.c
--- a/code/game/g_missile.c
+++ b/code/game/g_missile.c
@@ -278,11 +278,27 @@ void G_MissileImpact( gentity_t *ent, trace_t *trace ) {
 	// check for bounce
 	if ( !other->takedamage &&
 		( ent->s.eFlags & ( EF_BOUNCE | EF_BOUNCE_HALF ) ) ) {
+		/*
+		 * Vibe default mod behavior: rockets bounce twice, then explode
+		 * on the third world collision.
+		 */
+		if ( ent->s.weapon == WP_ROCKET_LAUNCHER ) {
+			G_Printf( "[vibe-mod] rocket world hit, bounces left: %d\n", ent->count );
+			if ( ent->count <= 0 ) {
+				G_ExplodeMissile( ent );
+				return;
+			}
+			ent->count--;
+		}
 		G_BounceMissile( ent, trace );
+		if ( ent->s.weapon == WP_ROCKET_LAUNCHER ) {
+			VectorScale( ent->s.pos.trDelta, 0.65f, ent->s.pos.trDelta );
+			SnapVector( ent->s.pos.trDelta );
+		}
 		G_AddEvent( ent, EV_GRENADE_BOUNCE, 0 );
 		return;
 	}
 
 #ifdef MISSIONPACK
 	if ( other->takedamage ) {
 		if ( ent->s.weapon != WP_PROX_LAUNCHER ) {
@@ -663,10 +676,14 @@ gentity_t *fire_rocket (gentity_t *self, vec3_t start, vec3_t dir) {
 	bolt->splashMethodOfDeath = MOD_ROCKET_SPLASH;
 	bolt->clipmask = MASK_SHOT;
 	bolt->target_ent = NULL;
+	/* enable rocket bounce and start with two bounces remaining */
+	bolt->s.eFlags |= EF_BOUNCE;
+	bolt->count = 2;
+	G_Printf( "[vibe-mod] spawned rocket with bounce budget %d\n", bolt->count );
 
 	bolt->s.pos.trType = TR_LINEAR;
 	bolt->s.pos.trTime = level.time - MISSILE_PRESTEP_TIME;		// move a bit on the very first frame
 	VectorCopy( start, bolt->s.pos.trBase );
-	VectorScale( dir, 900, bolt->s.pos.trDelta );
+	VectorScale( dir, 550, bolt->s.pos.trDelta );
 	SnapVector( bolt->s.pos.trDelta );			// save net bandwidth
 	VectorCopy (start, bolt->r.currentOrigin);

diff --git a/code/game/g_local.h b/code/game/g_local.h
--- a/code/game/g_local.h
+++ b/code/game/g_local.h
@@ -30,7 +30,7 @@
 //==================================================================
 
 // the "gameversion" client command will print this plus compile date
-#define	GAMEVERSION	BASEGAME
+#define	GAMEVERSION	"baseoa"
 
 #define BODY_QUEUE_SIZE		8
 
diff --git a/code/game/bg_public.h b/code/game/bg_public.h
--- a/code/game/bg_public.h
+++ b/code/game/bg_public.h
@@ -28,7 +28,7 @@
-#define	GAME_VERSION		BASEGAME "-1"
+#define	GAME_VERSION		"baseoa-1"
 
 #define	DEFAULT_GRAVITY		800
 #define	GIB_HEALTH			-40
 #define	ARMOR_PROTECTION	0.66
 
 #define	MAX_ITEMS			256
 
PATCH
  fi
}

write_mod_readme() {
  mkdir -p "$MOD_DIR"
  if [ "$MOD_VARIANT" = "debug-visible" ]; then
    cat > "${MOD_DIR}/README.md" <<EOF
# ${MOD_NAME}

Debug-visible vibe mod generated by \`scripts/generate_default_mod.sh\`.

Behavior:
- Rocket launcher projectiles bounce on any impact (including players) for clear testing.
- Rockets use a high bounce budget (6) before exploding.
- Rocket speed is reduced (900 -> 120) so bounce behavior is easy to observe.
- Direct and splash rocket damage are disabled for debugging.
- Each bounce prints both chat/console and center-screen debug messages.
- Each bounce emits an extra visible missile-miss effect.

Run:
\`\`\`bash
./VibeArena_Build/run_${MOD_NAME}.sh
\`\`\`

Mod source workflow:
- Put your content in \`mods/${MOD_NAME}/scripts\`, \`maps\`, \`textures\`, \`sound\`.
- Optional custom main menu background: place \`mods/${MOD_NAME}/assets/mainmenu/background.jpg\` (or \`.jpeg\`, \`.png\`, \`.tga\`), or pass \`--mainmenu-image /path/to/image\` when generating.
- Re-run \`scripts/generate_default_mod.sh ${MOD_NAME} --variant debug-visible\` when you want to rebuild this variant.
EOF
  else
    cat > "${MOD_DIR}/README.md" <<EOF
# ${MOD_NAME}

Default vibe mod generated by \`scripts/generate_default_mod.sh\`.

Behavior:
- Rocket launcher projectiles bounce twice on world geometry.
- On the next collision after the second bounce, rockets explode.
- Rocket speed is reduced (900 -> 550) so bounce behavior is easier to see while testing.

Run:
\`\`\`bash
./VibeArena_Build/run_${MOD_NAME}.sh
\`\`\`

Mod source workflow:
- Put your content in \`mods/${MOD_NAME}/scripts\`, \`maps\`, \`textures\`, \`sound\`.
- Optional custom main menu background: place \`mods/${MOD_NAME}/assets/mainmenu/background.jpg\` (or \`.jpeg\`, \`.png\`, \`.tga\`), or pass \`--mainmenu-image /path/to/image\` when generating.
- Re-run \`scripts/generate_default_mod.sh ${MOD_NAME}\` when you want to rebuild the default qagame VM patch.
EOF
  fi
}

write_mod_scaffold() {
  mkdir -p "${MOD_DIR}/scripts" "${MOD_DIR}/maps" "${MOD_DIR}/textures" "${MOD_DIR}/sound" "${MOD_DIR}/vm" "${MOD_MAINMENU_ASSETS_DIR}"
  touch "${MOD_DIR}/scripts/.gitkeep" "${MOD_DIR}/maps/.gitkeep" "${MOD_DIR}/textures/.gitkeep" "${MOD_DIR}/sound/.gitkeep" "${MOD_DIR}/vm/.gitkeep" "${MOD_MAINMENU_ASSETS_DIR}/.gitkeep"
}

ensure_base_setup() {
  if [ ! -x "${ROOT_DIR}/scripts/build.sh" ]; then
    echo "ERROR: scripts/build.sh not found or not executable." >&2
    exit 1
  fi

  if [ ! -f "${DIST_DIR}/play.sh" ] || [ ! -f "${DIST_DIR}/baseoa/pak0.pk3" ]; then
    echo "Base build not found; running scripts/build.sh first..."
    "${ROOT_DIR}/scripts/build.sh"
  fi

  if [ ! -d "${ENGINE_DIR}/.git" ]; then
    echo "ERROR: quake_engine source checkout not found at ${ENGINE_DIR}." >&2
    exit 1
  fi
}

build_qagame_qvm() {
  local cmake_bin="$1"
  local jobs qvm_path

  mkdir -p "$TMP_ROOT"
  git -C "$ENGINE_DIR" worktree add --detach "$TMP_ENGINE" >/dev/null
  WORKTREE_ADDED=1

  git -C "$TMP_ENGINE" apply "$PATCH_FILE"

  "$cmake_bin" -S "$TMP_ENGINE" -B "$TMP_BUILD" \
    -DCMAKE_BUILD_TYPE=Release

  jobs="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
  "$cmake_bin" --build "$TMP_BUILD" --target qagameqvm_baseq3 -j"$jobs"

  qvm_path="$(find "$TMP_BUILD" -type f -path '*/baseq3/vm/qagame.qvm' | head -n1)"
  if [ -z "$qvm_path" ]; then
    echo "ERROR: qagame.qvm was not produced." >&2
    exit 1
  fi

  rm -rf "$MOD_BUILD_DIR"
  mkdir -p "$MOD_VM_DIR"
  cp "$qvm_path" "${MOD_VM_DIR}/qagame.qvm"
}

resolve_mainmenu_image() {
  local candidate

  if [ -n "$MAINMENU_IMAGE_ARG" ]; then
    if [ ! -f "$MAINMENU_IMAGE_ARG" ]; then
      echo "ERROR: --mainmenu-image file not found: $MAINMENU_IMAGE_ARG" >&2
      exit 1
    fi
    echo "$MAINMENU_IMAGE_ARG"
    return 0
  fi

  for candidate in \
    "${MOD_MAINMENU_ASSETS_DIR}/background.jpg" \
    "${MOD_MAINMENU_ASSETS_DIR}/background.jpeg" \
    "${MOD_MAINMENU_ASSETS_DIR}/background.png" \
    "${MOD_MAINMENU_ASSETS_DIR}/background.tga"; do
    if [ -f "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done

  return 1
}

detect_oanew_shader_pk3() {
  local pk3

  for pk3 in \
    "${DIST_DIR}/baseoa/pak6-patch088.pk3" \
    "${DIST_DIR}/baseoa/pak6-patch085.pk3" \
    "${DIST_DIR}/baseoa/pak0.pk3"; do
    if [ -f "$pk3" ] && unzip -l "$pk3" "scripts/oanew.shader" >/dev/null 2>&1; then
      echo "$pk3"
      return 0
    fi
  done

  for pk3 in "${DIST_DIR}/baseoa/"*.pk3; do
    if [ -f "$pk3" ] && unzip -l "$pk3" "scripts/oanew.shader" >/dev/null 2>&1; then
      echo "$pk3"
      return 0
    fi
  done

  echo "ERROR: could not locate scripts/oanew.shader in baseoa pk3 assets." >&2
  exit 1
}

rewrite_oanew_menu_shaders() {
  local shader_file="$1"
  local menu_image_shader_path="$2"
  local rewritten_file="${shader_file}.tmp"

  MAINMENU_SHADER_IMAGE="$menu_image_shader_path" perl -0777 -pe '
    my $img = $ENV{MAINMENU_SHADER_IMAGE};

    sub make_menu_block {
      my ($name, $image_path) = @_;
      return "$name\n{\n\tnopicmip\n\t{\n\t\tclampmap $image_path\n\t\trgbGen identity\n\t}\n}\n";
    }

    s{menubacknologo_blueish\s*\{(?:[^{}]|\{[^{}]*\})*\}}{make_menu_block("menubacknologo_blueish", $img)}sge;
    s{menuback_blueish\s*\{(?:[^{}]|\{[^{}]*\})*\}}{make_menu_block("menuback_blueish", $img)}sge;
    s{menubacknologo\s*\{(?:[^{}]|\{[^{}]*\})*\}}{make_menu_block("menubacknologo", $img)}sge;
    s{menuback\s*\{(?:[^{}]|\{[^{}]*\})*\}}{make_menu_block("menuback", $img)}sge;
  ' "$shader_file" > "$rewritten_file"

  mv "$rewritten_file" "$shader_file"

  if ! grep -q "clampmap ${menu_image_shader_path}" "$shader_file"; then
    echo "ERROR: failed to rewrite main menu shader blocks in ${shader_file}." >&2
    exit 1
  fi

  cat >> "$shader_file" <<EOF

menubackRagePro
{
  nopicmip
  {
    clampmap ${menu_image_shader_path}
    rgbGen identity
  }
}
EOF
}

inject_mainmenu_background() {
  local source_image image_ext shader_image_path source_pk3

  if ! source_image="$(resolve_mainmenu_image)"; then
    return 0
  fi

  image_ext="${source_image##*.}"
  image_ext="$(echo "$image_ext" | tr '[:upper:]' '[:lower:]')"
  case "$image_ext" in
    jpg|jpeg|png|tga)
      ;;
    *)
      echo "ERROR: unsupported main menu image format '${image_ext}'. Use .jpg, .jpeg, .png, or .tga." >&2
      exit 1
      ;;
  esac

  shader_image_path="gfx/vibe/mainmenu_background.${image_ext}"
  mkdir -p "${MOD_BUILD_DIR}/gfx/vibe" "${MOD_BUILD_DIR}/scripts"
  cp "$source_image" "${MOD_BUILD_DIR}/${shader_image_path}"

  source_pk3="$(detect_oanew_shader_pk3)"
  unzip -p "$source_pk3" "scripts/oanew.shader" > "${MOD_BUILD_DIR}/scripts/oanew.shader"
  rewrite_oanew_menu_shaders "${MOD_BUILD_DIR}/scripts/oanew.shader" "$shader_image_path"

  MAINMENU_STATUS="enabled (${source_image})"
}

stage_mod_assets() {
  local asset_dir

  for asset_dir in "${PK3_ASSET_DIRS[@]}"; do
    if [ -d "${MOD_DIR}/${asset_dir}" ]; then
      cp -R "${MOD_DIR}/${asset_dir}" "${MOD_BUILD_DIR}/"
    fi
  done

  inject_mainmenu_background

  find "${MOD_BUILD_DIR}" -name '.gitkeep' -type f -delete
  find "${MOD_BUILD_DIR}" -type d -empty -delete
}

verify_qagame_identity() {
  local qvm_path="${MOD_VM_DIR}/qagame.qvm"

  if ! strings "$qvm_path" | grep -q 'baseoa-1'; then
    echo "ERROR: generated qagame.qvm does not contain baseoa-1 (OpenArena compatibility marker)." >&2
    exit 1
  fi

  if strings "$qvm_path" | grep -q 'baseq3-1'; then
    echo "ERROR: generated qagame.qvm still contains baseq3-1, which triggers client/server mismatch with OpenArena." >&2
    exit 1
  fi
}

package_mod_pk3() {
  mkdir -p "$MOD_DIST_DIR"
  rm -f "$MOD_PK3"
  (
    cd "$MOD_BUILD_DIR"
    zip -q -r "$MOD_PK3" .
  )
}

write_mod_launcher() {
  mkdir -p "$DIST_DIR"
cat > "$MOD_LAUNCHER" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/ioquake3.app/Contents/MacOS/ioquake3" \
  +set fs_basepath "$HERE" \
  +set fs_homepath "$HERE" \
  +set com_basegame baseoa \
  +set dedicated 0 \
  +set vm_game 2 \
  +set fs_game "__MOD_NAME__" \
  +set com_hunkMegs 256 \
  "$@"
EOF
  perl -0pi -e "s/__MOD_NAME__/${MOD_NAME}/g" "$MOD_LAUNCHER"
  chmod +x "$MOD_LAUNCHER"
}

main() {
  local cmake_bin

  if [[ ! "$MOD_NAME" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "ERROR: invalid mod name '$MOD_NAME'. Use letters, numbers, '_' or '-'." >&2
    exit 1
  fi

  if [ "$MOD_VARIANT" != "default" ] && [ "$MOD_VARIANT" != "debug-visible" ]; then
    echo "ERROR: invalid variant '$MOD_VARIANT'. Use 'default' or 'debug-visible'." >&2
    exit 1
  fi

  require_cmd git
  require_cmd curl
  require_cmd unzip
  require_cmd zip
  require_cmd tar
  require_cmd find
  require_cmd strings
  require_cmd perl

  cd "$ROOT_DIR"
  trap cleanup EXIT

  ensure_base_setup
  cmake_bin="$(detect_cmake)"

  write_patch_file
  write_mod_scaffold
  write_mod_readme
  build_qagame_qvm "$cmake_bin"
  verify_qagame_identity
  stage_mod_assets
  package_mod_pk3
  write_mod_launcher

  echo
  echo "Mod generated: ${MOD_NAME} (variant: ${MOD_VARIANT})"
  echo "Patch source: ${PATCH_FILE}"
  echo "Built qagame VM: ${MOD_VM_DIR}/qagame.qvm"
  echo "Packaged mod: ${MOD_PK3}"
  echo "Main menu background: ${MAINMENU_STATUS}"
  echo "Launcher: ${MOD_LAUNCHER}"
  echo "Run: ./VibeArena_Build/run_${MOD_NAME}.sh"
}

main "$@"
