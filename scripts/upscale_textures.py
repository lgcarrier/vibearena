#!/usr/bin/env python3
"""Upscale OpenArena mod player textures with Real-ESRGAN.

Supports two workflows:
1) Default non-review mode: one-pass upscale + package output PK3.
2) Review mode (--review): generate side-by-side previews, allow accept/reject/rerun
   in a local web UI, then package on demand.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


IMAGE_EXTENSIONS = {".tga", ".png", ".jpg", ".jpeg"}
GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/xinntao/Real-ESRGAN/releases/latest"
GITHUB_RELEASES_API = "https://api.github.com/repos/xinntao/Real-ESRGAN/releases?per_page=20"
SUBPROCESS_TIMEOUT_SECONDS = 120
REVIEW_STATUS_ACCEPTED = "accepted"
REVIEW_STATUS_REJECTED = "rejected"


class UpscaleError(RuntimeError):
    """Raised when the upscale workflow cannot continue."""


@dataclass
class SourceSpec:
    kind: str  # "pk3" or "dir"
    path: Path


@dataclass
class Stats:
    discovered: int = 0
    skipped_large: int = 0
    copied_original: int = 0
    upscaled: int = 0
    failed: int = 0


@dataclass
class RerunPreset:
    name: str
    model: str
    scale: int

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "model": self.model, "scale": self.scale}


@dataclass
class ImageReviewRecord:
    rel_path: str
    width: int
    height: int
    status: str
    selected_preset: str
    original_path: str
    candidate_path: str
    preview_original_path: str
    preview_candidate_path: str
    last_error: Optional[str]

    def to_manifest_dict(self) -> Dict[str, object]:
        return {
            "rel_path": self.rel_path,
            "width": self.width,
            "height": self.height,
            "status": self.status,
            "selected_preset": self.selected_preset,
            "original_path": self.original_path,
            "candidate_path": self.candidate_path,
            "preview_original_path": self.preview_original_path,
            "preview_candidate_path": self.preview_candidate_path,
            "last_error": self.last_error,
        }


def parse_args(root_dir: Path) -> argparse.Namespace:
    default_tool_bin = root_dir / ".tools" / "realesrgan-ncnn-vulkan"
    default_models_dir = root_dir / ".tools" / "realesrgan-models"
    default_work_dir = root_dir / ".tmp" / "upscale_work"

    parser = argparse.ArgumentParser(
        description=(
            "Upscale models/players textures for a selected mod and package "
            "them into a load-last PK3 override."
        )
    )
    parser.add_argument("--mod", required=True, help="Mod folder name (for example: afterlife_arena).")
    parser.add_argument("--source-pk3", type=Path, help="Optional explicit source PK3 path.")
    parser.add_argument("--source-dir", type=Path, help="Optional explicit source directory path.")
    parser.add_argument("--output-pk3", type=Path, help="Optional explicit output PK3 path.")
    parser.add_argument("--tool-bin", type=Path, default=default_tool_bin, help=f"Real-ESRGAN binary path (default: {default_tool_bin}).")
    parser.add_argument("--models-dir", type=Path, default=default_models_dir, help=f"Real-ESRGAN models directory path (default: {default_models_dir}).")
    parser.add_argument("--max-dimension", type=int, default=1024, help="Skip upscaling when width or height is greater than this value.")
    parser.add_argument("--scale", type=int, default=4, help="Real-ESRGAN scale factor.")
    parser.add_argument("--model", default="realesrgan-x4plus", help="Real-ESRGAN model name.")
    parser.add_argument("--work-dir", type=Path, default=default_work_dir, help=f"Temporary workspace path (default: {default_work_dir}).")
    parser.add_argument("--keep-work-dir", action="store_true", help="Keep temporary workspace after completion.")
    parser.add_argument("--verbose", action="store_true", help="Print extra diagnostics.")

    parser.add_argument("--review", action="store_true", help="Enable interactive side-by-side review UI before packaging.")
    parser.add_argument("--review-host", default="127.0.0.1", help="Review server host (default: 127.0.0.1).")
    parser.add_argument("--review-port", type=int, default=8765, help="Review server port (default: 8765).")
    parser.add_argument("--no-open-browser", action="store_true", help="Do not auto-open the review UI in a browser.")
    parser.add_argument("--review-manifest", type=Path, help="Optional review manifest path (default: <work-dir>/review/manifest.json).")
    parser.add_argument(
        "--rerun-preset",
        action="append",
        default=[],
        help="Repeatable rerun preset: name:model:scale (for example: anime:realesrgan-x4plus-anime:4).",
    )
    return parser.parse_args()


def log(msg: str) -> None:
    print(msg, flush=True)


def v_log(enabled: bool, msg: str) -> None:
    if enabled:
        log(msg)


def find_available_mods(root_dir: Path) -> List[str]:
    candidates = set()
    mods_root = root_dir / "mods"
    dist_root = root_dir / "VibeArena_Build"

    if mods_root.is_dir():
        for child in mods_root.iterdir():
            if child.is_dir():
                candidates.add(child.name)

    if dist_root.is_dir():
        for child in dist_root.iterdir():
            if child.is_dir() and child.name not in {"baseoa", "ioquake3.app"}:
                if (child / f"z_{child.name}.pk3").exists():
                    candidates.add(child.name)
                elif any(child.glob("z_*.pk3")):
                    candidates.add(child.name)

    return sorted(candidates)


def resolve_source(root_dir: Path, args: argparse.Namespace) -> SourceSpec:
    mod_dist_dir = root_dir / "VibeArena_Build" / args.mod
    mod_source_dir = root_dir / "mods" / args.mod

    candidates: List[SourceSpec] = []
    if args.source_pk3:
        candidates.append(SourceSpec("pk3", args.source_pk3))

    canonical_pk3 = mod_dist_dir / f"z_{args.mod}.pk3"
    if canonical_pk3.exists():
        candidates.append(SourceSpec("pk3", canonical_pk3))

    if mod_dist_dir.is_dir():
        for pk3 in sorted(mod_dist_dir.glob("z_*.pk3")):
            if pk3 != canonical_pk3:
                candidates.append(SourceSpec("pk3", pk3))

    if args.source_dir:
        candidates.append(SourceSpec("dir", args.source_dir))

    if mod_source_dir.is_dir():
        candidates.append(SourceSpec("dir", mod_source_dir))

    for candidate in candidates:
        candidate_path = candidate.path.expanduser().resolve()
        if candidate.kind == "pk3" and candidate_path.is_file():
            return SourceSpec(candidate.kind, candidate_path)
        if candidate.kind == "dir" and candidate_path.is_dir():
            return SourceSpec(candidate.kind, candidate_path)

    available_mods = find_available_mods(root_dir)
    mods_text = ", ".join(available_mods) if available_mods else "(none found)"
    raise UpscaleError(
        "Could not resolve a valid source for mod "
        f"'{args.mod}'. Checked --source-pk3, mod dist PK3s, --source-dir, "
        f"and mods/{args.mod}. Available mod candidates: {mods_text}."
    )


def resolve_output(root_dir: Path, mod: str, output_pk3: Optional[Path]) -> Path:
    if output_pk3:
        return output_pk3.expanduser().resolve()
    mod_dist_dir = root_dir / "VibeArena_Build" / mod
    return (mod_dist_dir / f"z_{mod}_upscaled_skins.pk3").resolve()


def detect_converter() -> List[str]:
    system = platform.system().lower()
    if system == "darwin":
        sips_path = shutil.which("sips")
        if sips_path:
            return [sips_path]
        raise UpscaleError("Required converter 'sips' not found on macOS.")

    if system == "linux":
        magick_path = shutil.which("magick")
        if magick_path:
            return [magick_path]
        convert_path = shutil.which("convert")
        if convert_path:
            return [convert_path]
        raise UpscaleError("Required converter not found on Linux. Install ImageMagick ('magick' or 'convert').")

    raise UpscaleError(f"Unsupported platform '{platform.system()}'. Only macOS and Linux are supported.")


def have_model_files(models_dir: Path, model_name: str) -> bool:
    return (models_dir / f"{model_name}.param").is_file() and (models_dir / f"{model_name}.bin").is_file()


def select_release_asset(assets: Iterable[Dict[str, object]]) -> Tuple[str, str]:
    system = platform.system().lower()
    if system == "darwin":
        keywords = ("macos", "darwin", "osx")
    elif system == "linux":
        keywords = ("ubuntu", "linux")
    else:
        raise UpscaleError(f"Unsupported platform '{platform.system()}'.")

    best_name = None
    best_url = None
    best_score = -1

    for asset in assets:
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        lowered = name.lower()
        if "realesrgan-ncnn-vulkan" not in lowered:
            continue
        if not any(keyword in lowered for keyword in keywords):
            continue
        if "windows" in lowered:
            continue
        if not (lowered.endswith(".zip") or lowered.endswith(".tar.gz") or lowered.endswith(".tgz")):
            continue

        score = 0
        if lowered.endswith(".zip"):
            score += 2
        if lowered.endswith(".tar.gz") or lowered.endswith(".tgz"):
            score += 1
        if "ncnn" in lowered:
            score += 1

        if score > best_score:
            best_score = score
            best_name = name
            best_url = url

    if not best_name or not best_url:
        raise UpscaleError("Could not find a compatible Real-ESRGAN release asset for this platform.")

    return best_name, best_url


def fetch_json(url: str) -> object:
    try:
        with urllib.request.urlopen(url) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise UpscaleError(f"Failed to fetch URL {url}: {exc}") from exc


def iter_release_candidates() -> Iterable[Dict[str, object]]:
    seen_ids = set()

    latest_payload = fetch_json(GITHUB_LATEST_RELEASE_API)
    if isinstance(latest_payload, dict):
        release_id = latest_payload.get("id")
        if release_id is not None:
            seen_ids.add(release_id)
        yield latest_payload

    releases_payload = fetch_json(GITHUB_RELEASES_API)
    if not isinstance(releases_payload, list):
        raise UpscaleError("GitHub releases API did not return a valid release list.")

    for release in releases_payload:
        if not isinstance(release, dict):
            continue
        release_id = release.get("id")
        if release_id in seen_ids:
            continue
        if release_id is not None:
            seen_ids.add(release_id)
        yield release


def download_realesrgan_archive(download_to: Path) -> Path:
    log("Fetching Real-ESRGAN release metadata...")
    selected_asset_name = None
    selected_asset_url = None
    selected_release_tag = None

    for release in iter_release_candidates():
        assets = release.get("assets", [])
        if not isinstance(assets, list) or not assets:
            continue
        try:
            asset_name, asset_url = select_release_asset(assets)
        except UpscaleError:
            continue
        selected_asset_name = asset_name
        selected_asset_url = asset_url
        selected_release_tag = str(release.get("tag_name", "unknown"))
        break

    if not selected_asset_name or not selected_asset_url:
        raise UpscaleError("Could not find a compatible Real-ESRGAN binary asset in recent GitHub releases.")

    archive_path = download_to / selected_asset_name
    log(f"Downloading Real-ESRGAN asset: {selected_asset_name} (release {selected_release_tag})")

    try:
        urllib.request.urlretrieve(selected_asset_url, archive_path)
    except urllib.error.URLError as exc:
        raise UpscaleError(f"Failed to download Real-ESRGAN asset: {exc}") from exc

    return archive_path


def extract_archive(archive_path: Path, extract_to: Path) -> None:
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(extract_to)
        return

    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        with tarfile.open(archive_path, mode="r:gz") as tf:
            tf.extractall(extract_to)
        return

    raise UpscaleError(f"Unsupported archive format for {archive_path}.")


def find_binary_and_models(extracted_root: Path) -> Tuple[Path, Path]:
    binary_path: Optional[Path] = None
    model_dir: Optional[Path] = None

    for path in extracted_root.rglob("*"):
        if path.is_file() and path.name == "realesrgan-ncnn-vulkan":
            binary_path = path
            break

    if binary_path is None:
        raise UpscaleError("Could not locate 'realesrgan-ncnn-vulkan' in downloaded archive.")

    for path in extracted_root.rglob("models"):
        if not path.is_dir():
            continue
        params = list(path.glob("*.param"))
        bins = list(path.glob("*.bin"))
        if params and bins:
            model_dir = path
            break

    if model_dir is None:
        raise UpscaleError("Could not locate Real-ESRGAN model files in downloaded archive.")

    return binary_path, model_dir


def ensure_realesrgan(tool_bin: Path, models_dir: Path, model: str, install_dir: Path, verbose: bool) -> None:
    if tool_bin.is_file() and os.access(tool_bin, os.X_OK) and have_model_files(models_dir, model):
        v_log(verbose, f"Using existing Real-ESRGAN binary: {tool_bin}")
        return

    install_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = install_dir / "realesrgan-install"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    archive_path = download_realesrgan_archive(staging_dir)
    extracted_dir = staging_dir / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    extract_archive(archive_path, extracted_dir)
    binary_src, models_src = find_binary_and_models(extracted_dir)

    tool_bin.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary_src, tool_bin)
    current_mode = tool_bin.stat().st_mode
    tool_bin.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    if models_dir.exists():
        shutil.rmtree(models_dir)
    shutil.copytree(models_src, models_dir)
    v_log(verbose, f"Installed Real-ESRGAN binary at {tool_bin}")
    v_log(verbose, f"Installed Real-ESRGAN models at {models_dir}")

    if not have_model_files(models_dir, model):
        raise UpscaleError(
            f"Model '{model}' not found after installation in {models_dir}. "
            "Try a different --model or verify downloaded models."
        )


def iter_source_images(source: SourceSpec) -> Iterable[Tuple[str, bytes]]:
    if source.kind == "pk3":
        with zipfile.ZipFile(source.path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename.replace("\\", "/")
                lowered = name.lower()
                if not lowered.startswith("models/players/"):
                    continue
                if Path(lowered).suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                yield name, zf.read(info)
        return

    source_root = source.path
    players_root = source_root / "models" / "players"
    if not players_root.is_dir():
        return
    for path in players_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        rel = path.relative_to(source_root).as_posix()
        yield rel, path.read_bytes()


def read_image_dimensions(path: Path) -> Tuple[int, int]:
    suffix = path.suffix.lower()
    if suffix == ".tga":
        with path.open("rb") as f:
            header = f.read(18)
        if len(header) < 18:
            raise UpscaleError(f"Invalid TGA header: {path}")
        width = header[12] | (header[13] << 8)
        height = header[14] | (header[15] << 8)
        return width, height

    if suffix == ".png":
        with path.open("rb") as f:
            signature = f.read(8)
            if signature != b"\x89PNG\r\n\x1a\n":
                raise UpscaleError(f"Invalid PNG signature: {path}")
            length = int.from_bytes(f.read(4), "big")
            chunk_type = f.read(4)
            if length != 13 or chunk_type != b"IHDR":
                raise UpscaleError(f"Invalid PNG IHDR chunk: {path}")
            width = int.from_bytes(f.read(4), "big")
            height = int.from_bytes(f.read(4), "big")
        return width, height

    if suffix in {".jpg", ".jpeg"}:
        with path.open("rb") as f:
            if f.read(2) != b"\xff\xd8":
                raise UpscaleError(f"Invalid JPEG header: {path}")
            while True:
                marker_start = f.read(1)
                if not marker_start:
                    break
                if marker_start != b"\xff":
                    continue
                marker = f.read(1)
                while marker == b"\xff":
                    marker = f.read(1)
                if not marker:
                    break
                marker_value = marker[0]
                if marker_value in (0xD8, 0xD9):
                    continue
                seg_len_bytes = f.read(2)
                if len(seg_len_bytes) < 2:
                    break
                seg_len = int.from_bytes(seg_len_bytes, "big")
                if marker_value in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    _precision = f.read(1)
                    height = int.from_bytes(f.read(2), "big")
                    width = int.from_bytes(f.read(2), "big")
                    return width, height
                f.seek(seg_len - 2, os.SEEK_CUR)
        raise UpscaleError(f"Could not parse JPEG dimensions: {path}")

    raise UpscaleError(f"Unsupported image type for dimensions: {path}")


def run_subprocess(command: List[str], verbose: bool) -> None:
    v_log(verbose, f"Running: {' '.join(command)}")
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=not verbose,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpscaleError(f"Command timed out after {SUBPROCESS_TIMEOUT_SECONDS}s: {' '.join(command)}") from exc
    except subprocess.CalledProcessError as exc:
        if verbose:
            raise UpscaleError(f"Command failed ({exc.returncode}): {' '.join(command)}") from exc
        stderr = (exc.stderr or "").strip()
        raise UpscaleError(
            f"Command failed ({exc.returncode}): {' '.join(command)}" + (f"\n{stderr}" if stderr else "")
        ) from exc
    if verbose and result.stdout:
        print(result.stdout, end="")


def _decode_tga_pixels(image_type: int, encoded: bytes, pixel_size: int, pixel_count: int) -> Tuple[bytes, int]:
    if image_type == 2:
        required = pixel_count * pixel_size
        if len(encoded) < required:
            raise UpscaleError("TGA image data is truncated.")
        return encoded[:required], required

    if image_type == 10:
        out = bytearray()
        pos = 0
        target = pixel_count * pixel_size
        while len(out) < target:
            if pos >= len(encoded):
                raise UpscaleError("TGA RLE stream is truncated.")
            packet = encoded[pos]
            pos += 1
            count = (packet & 0x7F) + 1
            if packet & 0x80:
                end = pos + pixel_size
                if end > len(encoded):
                    raise UpscaleError("TGA RLE packet is truncated.")
                px = encoded[pos:end]
                pos = end
                out.extend(px * count)
            else:
                run_size = count * pixel_size
                end = pos + run_size
                if end > len(encoded):
                    raise UpscaleError("TGA raw packet is truncated.")
                out.extend(encoded[pos:end])
                pos = end
        if len(out) != target:
            raise UpscaleError("Decoded TGA pixel count mismatch.")
        return bytes(out), pos

    raise UpscaleError(f"Unsupported TGA image type for normalization: {image_type}")


def normalize_tga_for_ioquake(path: Path, verbose: bool) -> None:
    raw = bytearray(path.read_bytes())
    if len(raw) < 18:
        raise UpscaleError(f"TGA file too short: {path}")

    id_len = raw[0]
    cmap_type = raw[1]
    image_type = raw[2]
    width = raw[12] | (raw[13] << 8)
    height = raw[14] | (raw[15] << 8)
    bpp = raw[16]
    descriptor = raw[17]

    if width <= 0 or height <= 0:
        raise UpscaleError(f"Invalid TGA dimensions in {path}.")
    if bpp not in {24, 32}:
        raise UpscaleError(f"Unsupported TGA depth {bpp} in {path}.")

    top_down = bool(descriptor & 0x20)
    if not top_down:
        return

    cmap_length = raw[5] | (raw[6] << 8)
    cmap_entry_size_bits = raw[7]
    cmap_bytes = 0
    if cmap_type == 1:
        cmap_bytes = cmap_length * ((cmap_entry_size_bits + 7) // 8)
    elif cmap_type != 0:
        raise UpscaleError(f"Unsupported TGA color map type {cmap_type} in {path}.")

    start = 18 + id_len + cmap_bytes
    if start > len(raw):
        raise UpscaleError(f"TGA header exceeds file length for {path}.")

    pixel_size = bpp // 8
    pixel_count = width * height
    encoded = bytes(raw[start:])
    pixels, consumed = _decode_tga_pixels(image_type, encoded, pixel_size, pixel_count)
    trailer = encoded[consumed:]

    row_bytes = width * pixel_size
    rows = [pixels[i * row_bytes : (i + 1) * row_bytes] for i in range(height)]
    flipped_pixels = b"".join(reversed(rows))

    raw[2] = 2  # store as uncompressed true-color for deterministic engine-compatible output
    raw[17] = descriptor & ~0x20  # force bottom-up origin

    new_bytes = bytes(raw[:start]) + flipped_pixels + trailer
    path.write_bytes(new_bytes)
    v_log(verbose, f"Normalized TGA origin for ioquake3: {path}")


def convert_image(converter_cmd: List[str], source: Path, output: Path, verbose: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    converter_bin = Path(converter_cmd[0]).name
    if converter_bin == "sips":
        ext = output.suffix.lower().lstrip(".")
        if ext not in {"png", "tga", "jpg", "jpeg"}:
            raise UpscaleError(f"sips does not support target extension '{ext}' for {output}")
        sips_fmt = "jpeg" if ext == "jpg" else ext
        command = [converter_cmd[0], "-s", "format", sips_fmt, str(source), "--out", str(output)]
    elif converter_bin in {"magick", "convert"}:
        command = converter_cmd + [str(source), str(output)]
    else:
        raise UpscaleError(f"Unsupported converter command: {converter_cmd}")
    run_subprocess(command, verbose=verbose)
    if output.suffix.lower() == ".tga" and output.is_file():
        normalize_tga_for_ioquake(output, verbose)


def make_browser_preview(converter_cmd: List[str], source: Path, preview_png: Path, verbose: bool) -> None:
    preview_png.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".png":
        shutil.copy2(source, preview_png)
        return
    convert_image(converter_cmd, source, preview_png, verbose)


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def upscale_one_image(
    src_path: Path,
    rel_path: Path,
    staging_dir: Path,
    tool_bin: Path,
    models_dir: Path,
    model: str,
    scale: int,
    converter_cmd: List[str],
    verbose: bool,
) -> None:
    base_name = rel_path.stem
    temp_dir = staging_dir / "__tmp" / rel_path.parent
    temp_dir.mkdir(parents=True, exist_ok=True)

    upscale_input = src_path
    generated_input = False
    if src_path.suffix.lower() == ".tga":
        upscale_input = temp_dir / f"{base_name}_input.png"
        convert_image(converter_cmd, src_path, upscale_input, verbose)
        generated_input = True

    upscaled_png = temp_dir / f"{base_name}_upscaled.png"
    command = [
        str(tool_bin),
        "-i",
        str(upscale_input),
        "-o",
        str(upscaled_png),
        "-n",
        model,
        "-s",
        str(scale),
        "-f",
        "png",
        "-m",
        str(models_dir),
    ]
    run_subprocess(command, verbose=verbose)

    final_path = staging_dir / rel_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    ext = rel_path.suffix.lower()
    if ext == ".tga":
        convert_image(converter_cmd, upscaled_png, final_path, verbose)
    elif ext in {".jpg", ".jpeg"}:
        convert_image(converter_cmd, upscaled_png, final_path, verbose)
    else:
        shutil.copy2(upscaled_png, final_path)

    if generated_input and upscale_input.exists():
        upscale_input.unlink()
    if upscaled_png.exists():
        upscaled_png.unlink()


def package_pk3(source_root: Path, output_pk3: Path, verbose: bool) -> None:
    output_pk3.parent.mkdir(parents=True, exist_ok=True)
    if output_pk3.exists():
        output_pk3.unlink()
    with zipfile.ZipFile(output_pk3, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_root.rglob("*")):
            if not path.is_file():
                continue
            if "__tmp" in path.parts:
                continue
            rel_path = path.relative_to(source_root).as_posix()
            zf.write(path, rel_path)
            v_log(verbose, f"Added to PK3: {rel_path}")


def build_work_dirs(base_work_dir: Path) -> Dict[str, Path]:
    return {
        "raw": base_work_dir / "raw",
        "staging": base_work_dir / "staging",
        "download": base_work_dir / "download",
    }


def build_review_dirs(base_work_dir: Path) -> Dict[str, Path]:
    review_root = base_work_dir / "review"
    return {
        "review_root": review_root,
        "original_root": review_root / "original",
        "candidate_root": review_root / "candidate",
        "preview_original_root": review_root / "preview" / "original",
        "preview_candidate_root": review_root / "preview" / "candidate",
        "final_package_root": review_root / "final_package",
    }


def parse_rerun_preset(raw: str) -> RerunPreset:
    parts = raw.split(":")
    if len(parts) != 3:
        raise UpscaleError(f"Invalid --rerun-preset '{raw}'. Expected format name:model:scale")
    name = parts[0].strip()
    model = parts[1].strip()
    scale_raw = parts[2].strip()
    if not name or not model or not scale_raw:
        raise UpscaleError(f"Invalid --rerun-preset '{raw}'. Name, model, and scale must be non-empty.")
    try:
        scale = int(scale_raw)
    except ValueError as exc:
        raise UpscaleError(f"Invalid --rerun-preset '{raw}'. Scale must be an integer.") from exc
    if scale <= 0:
        raise UpscaleError(f"Invalid --rerun-preset '{raw}'. Scale must be greater than 0.")
    return RerunPreset(name=name, model=model, scale=scale)


def build_rerun_presets(args: argparse.Namespace, models_dir: Path) -> List[RerunPreset]:
    presets: List[RerunPreset] = []
    if args.rerun_preset:
        for raw in args.rerun_preset:
            presets.append(parse_rerun_preset(raw))
    else:
        presets = [
            RerunPreset(name="default", model=args.model, scale=args.scale),
            RerunPreset(name="realesrnet", model="realesrnet-x4plus", scale=args.scale),
            RerunPreset(name="anime", model="realesrgan-x4plus-anime", scale=args.scale),
        ]

    deduped: List[RerunPreset] = []
    seen_names = set()
    for preset in presets:
        if preset.name in seen_names:
            continue
        seen_names.add(preset.name)
        deduped.append(preset)

    filtered = [preset for preset in deduped if have_model_files(models_dir, preset.model)]
    if not filtered:
        names = ", ".join(p.model for p in deduped) if deduped else "(none)"
        raise UpscaleError(f"No valid rerun presets remain after model filtering. Missing model files for: {names}")
    return filtered


def load_previous_decisions(manifest_path: Path) -> Dict[str, Dict[str, object]]:
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    images = payload.get("images")
    if not isinstance(images, list):
        return {}
    out: Dict[str, Dict[str, object]] = {}
    for item in images:
        if not isinstance(item, dict):
            continue
        rel_path = item.get("rel_path")
        if not isinstance(rel_path, str) or not rel_path:
            continue
        out[rel_path] = item
    return out


def path_to_posix_string(path: Path) -> str:
    return path.as_posix()


def ensure_inside_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def to_preview_rel_path(rel_path: str) -> str:
    return f"{rel_path}.png"


def to_url_path(rel_path: str) -> str:
    return "/" + urllib.parse.quote(rel_path.replace("\\", "/"), safe="/")


class ReviewSession:
    def __init__(
        self,
        review_root: Path,
        manifest_path: Path,
        output_pk3: Path,
        presets: List[RerunPreset],
        records: Dict[str, ImageReviewRecord],
        converter_cmd: List[str],
        tool_bin: Path,
        models_dir: Path,
        verbose: bool,
    ):
        self.review_root = review_root
        self.manifest_path = manifest_path
        self.output_pk3 = output_pk3
        self.presets = {preset.name: preset for preset in presets}
        self.preset_order = [preset.name for preset in presets]
        self.records = records
        self.converter_cmd = converter_cmd
        self.tool_bin = tool_bin
        self.models_dir = models_dir
        self.verbose = verbose
        self.lock = threading.Lock()
        self.revision = 0
        self.finalized = False
        self.finalize_summary: Optional[Dict[str, object]] = None
        self.final_package_root = self.review_root / "final_package"

    def summary(self) -> Dict[str, int]:
        total = len(self.records)
        accepted = sum(1 for rec in self.records.values() if rec.status == REVIEW_STATUS_ACCEPTED)
        rejected = sum(1 for rec in self.records.values() if rec.status == REVIEW_STATUS_REJECTED)
        return {"total": total, "accepted": accepted, "rejected": rejected}

    def manifest_payload(self) -> Dict[str, object]:
        return {
            "version": 1,
            "output_pk3": str(self.output_pk3),
            "presets": [self.presets[name].to_dict() for name in self.preset_order],
            "revision": self.revision,
            "summary": self.summary(),
            "finalized": self.finalized,
            "finalize_summary": self.finalize_summary,
            "images": [self.records[key].to_manifest_dict() for key in sorted(self.records.keys())],
        }

    def save_manifest(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.revision += 1
        payload = self.manifest_payload()
        self.manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=False))

    def api_state(self) -> Dict[str, object]:
        with self.lock:
            payload = self.manifest_payload()
            images = payload["images"]
            out_images = []
            for img in images:
                img_obj = dict(img)
                img_obj["preview_original_url"] = to_url_path(str(img_obj["preview_original_path"]))
                img_obj["preview_candidate_url"] = to_url_path(str(img_obj["preview_candidate_path"]))
                out_images.append(img_obj)
            payload["images"] = out_images
            return payload

    def set_decision(self, rel_path: str, status: str) -> Dict[str, object]:
        if status not in {REVIEW_STATUS_ACCEPTED, REVIEW_STATUS_REJECTED}:
            raise UpscaleError(f"Invalid status '{status}'. Must be accepted or rejected.")
        with self.lock:
            record = self.records.get(rel_path)
            if record is None:
                raise UpscaleError(f"Unknown rel_path '{rel_path}'.")
            record.status = status
            self.save_manifest()
            return self.summary()

    def rerun_one(self, rel_path: str, preset_name: str) -> Dict[str, object]:
        with self.lock:
            record = self.records.get(rel_path)
            if record is None:
                raise UpscaleError(f"Unknown rel_path '{rel_path}'.")
            preset = self.presets.get(preset_name)
            if preset is None:
                raise UpscaleError(f"Unknown preset '{preset_name}'.")
            if not have_model_files(self.models_dir, preset.model):
                raise UpscaleError(f"Preset model '{preset.model}' is not available in {self.models_dir}.")

            original_file = self.review_root / record.original_path
            candidate_root = self.review_root / "candidate"
            preview_candidate_file = self.review_root / record.preview_candidate_path
            previous_status = record.status

            try:
                upscale_one_image(
                    src_path=original_file,
                    rel_path=Path(record.rel_path),
                    staging_dir=candidate_root,
                    tool_bin=self.tool_bin,
                    models_dir=self.models_dir,
                    model=preset.model,
                    scale=preset.scale,
                    converter_cmd=self.converter_cmd,
                    verbose=self.verbose,
                )
                candidate_file = self.review_root / record.candidate_path
                make_browser_preview(self.converter_cmd, candidate_file, preview_candidate_file, self.verbose)
                record.selected_preset = preset.name
                record.status = REVIEW_STATUS_ACCEPTED
                record.last_error = None
            except UpscaleError as exc:
                record.status = previous_status
                record.last_error = str(exc)
                self.save_manifest()
                raise

            self.save_manifest()
            return {"summary": self.summary(), "rel_path": rel_path, "selected_preset": record.selected_preset}

    def finalize_package(self) -> Dict[str, object]:
        with self.lock:
            if self.final_package_root.exists():
                shutil.rmtree(self.final_package_root)
            self.final_package_root.mkdir(parents=True, exist_ok=True)

            copied = 0
            for rel_path in sorted(self.records.keys()):
                record = self.records[rel_path]
                selected = record.candidate_path if record.status == REVIEW_STATUS_ACCEPTED else record.original_path
                src = self.review_root / selected
                dst = self.final_package_root / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1

            package_pk3(self.final_package_root, self.output_pk3, verbose=self.verbose)
            self.finalized = True
            self.finalize_summary = {
                "output_pk3": str(self.output_pk3),
                "packaged_files": copied,
                "summary": self.summary(),
            }
            self.save_manifest()
            return dict(self.finalize_summary)


def review_index_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Upscaled Texture Review</title>
  <style>
    :root { --bg:#0f172a; --card:#111827; --muted:#9ca3af; --text:#e5e7eb; --ok:#16a34a; --bad:#dc2626; --accent:#2563eb; }
    body { margin:0; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--text); }
    .top { position:sticky; top:0; background:#0b1220; border-bottom:1px solid #1f2937; padding:12px 16px; z-index:10; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
    .summary { font-weight:600; }
    .small { color:var(--muted); font-size:12px; }
    button, select { background:#1f2937; color:var(--text); border:1px solid #374151; border-radius:8px; padding:8px 10px; cursor:pointer; }
    button.primary { background:var(--accent); border-color:#1d4ed8; }
    button.good { background:#14532d; border-color:#166534; }
    button.bad { background:#7f1d1d; border-color:#991b1b; }
    .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(560px, 1fr)); gap:12px; padding:12px; }
    .card { background:var(--card); border:1px solid #1f2937; border-radius:10px; padding:10px; }
    .title { font-size:13px; font-weight:600; word-break:break-all; margin-bottom:4px; }
    .meta { font-size:12px; color:var(--muted); margin-bottom:8px; }
    .pair { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .pane { background:#0b1220; border:1px solid #1f2937; border-radius:8px; padding:6px; }
    .pane h4 { margin:0 0 6px 0; font-size:12px; color:var(--muted); font-weight:600; }
    img { width:100%; height:240px; object-fit:contain; background:#000; border-radius:6px; }
    .actions { display:flex; gap:8px; margin-top:8px; align-items:center; flex-wrap:wrap; }
    .badge { padding:2px 6px; border-radius:999px; font-size:11px; font-weight:700; }
    .accepted { background:#14532d; color:#bbf7d0; }
    .rejected { background:#7f1d1d; color:#fecaca; }
    .error { color:#fca5a5; font-size:12px; margin-top:6px; white-space:pre-wrap; }
  </style>
</head>
<body>
  <div class="top">
    <div class="summary" id="summary">Loading...</div>
    <div class="small" id="output"></div>
    <label>Filter
      <select id="filter">
        <option value="all">all</option>
        <option value="accepted">accepted</option>
        <option value="rejected">rejected</option>
      </select>
    </label>
    <button class="primary" id="finalize">Package PK3</button>
    <button id="shutdown">Stop Server</button>
  </div>
  <div class="grid" id="grid"></div>
  <script>
    let state = null;
    const grid = document.getElementById("grid");
    const summary = document.getElementById("summary");
    const output = document.getElementById("output");
    const filter = document.getElementById("filter");

    function esc(s) {
      return String(s)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    async function api(method, path, body) {
      const res = await fetch(path, {
        method,
        headers: {"Content-Type": "application/json"},
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || ("Request failed: " + res.status));
      }
      return data;
    }

    async function loadState() {
      state = await api("GET", "/api/state");
      render();
    }

    function shouldShow(img) {
      if (filter.value === "all") return true;
      return img.status === filter.value;
    }

    function statusBadge(status) {
      const cls = status === "accepted" ? "accepted" : "rejected";
      return `<span class="badge ${cls}">${esc(status)}</span>`;
    }

    function presetOptions(img) {
      return state.presets.map((p) => {
        const sel = p.name === img.selected_preset ? "selected" : "";
        return `<option value="${esc(p.name)}" ${sel}>${esc(p.name)} (${esc(p.model)} x${p.scale})</option>`;
      }).join("");
    }

    function render() {
      const s = state.summary;
      summary.textContent = `Textures: ${s.total} | accepted: ${s.accepted} | rejected: ${s.rejected} | revision: ${state.revision}`;
      output.textContent = `Output: ${state.output_pk3}`;

      const cards = [];
      for (const img of state.images) {
        if (!shouldShow(img)) continue;
        const bust = `?rev=${state.revision}`;
        cards.push(`
          <div class="card" data-rel="${esc(img.rel_path)}">
            <div class="title">${esc(img.rel_path)} ${statusBadge(img.status)}</div>
            <div class="meta">${img.width}x${img.height} | preset: ${esc(img.selected_preset)}</div>
            <div class="pair">
              <div class="pane">
                <h4>Original</h4>
                <img loading="lazy" src="${img.preview_original_url + bust}" alt="original">
              </div>
              <div class="pane">
                <h4>Candidate</h4>
                <img loading="lazy" src="${img.preview_candidate_url + bust}" alt="candidate">
              </div>
            </div>
            <div class="actions">
              <button class="good" onclick="setDecision('${esc(img.rel_path)}','accepted')">Accept</button>
              <button class="bad" onclick="setDecision('${esc(img.rel_path)}','rejected')">Reject</button>
              <select id="preset-${esc(img.rel_path)}">${presetOptions(img)}</select>
              <button onclick="rerunOne('${esc(img.rel_path)}')">Rerun</button>
            </div>
            ${img.last_error ? `<div class="error">${esc(img.last_error)}</div>` : ""}
          </div>
        `);
      }
      grid.innerHTML = cards.join("");
    }

    async function setDecision(relPath, status) {
      await api("POST", "/api/decision", { rel_path: relPath, status });
      await loadState();
    }

    async function rerunOne(relPath) {
      const select = document.getElementById(`preset-${relPath}`);
      const preset = select.value;
      await api("POST", "/api/rerun", { rel_path: relPath, preset });
      await loadState();
    }

    document.getElementById("finalize").addEventListener("click", async () => {
      if (!confirm("Package PK3 with current decisions?")) return;
      const result = await api("POST", "/api/finalize");
      await loadState();
      alert(`Packaged: ${result.output_pk3}`);
    });

    document.getElementById("shutdown").addEventListener("click", async () => {
      await api("POST", "/api/shutdown");
      alert("Shutdown requested. You can close this tab.");
    });

    filter.addEventListener("change", render);
    loadState().catch((err) => {
      summary.textContent = "Failed to load state";
      output.textContent = err.message;
    });
  </script>
</body>
</html>
"""


def prepare_review_records(
    source: SourceSpec,
    review_dirs: Dict[str, Path],
    previous: Dict[str, Dict[str, object]],
    presets: List[RerunPreset],
    max_dimension: int,
    converter_cmd: List[str],
    tool_bin: Path,
    models_dir: Path,
    verbose: bool,
) -> Tuple[Dict[str, ImageReviewRecord], Stats]:
    records: Dict[str, ImageReviewRecord] = {}
    stats = Stats()
    preset_map = {preset.name: preset for preset in presets}
    default_preset_name = presets[0].name

    for rel_str, data in iter_source_images(source):
        stats.discovered += 1
        rel_path = Path(rel_str)

        original_path = review_dirs["original_root"] / rel_path
        write_bytes(original_path, data)
        width, height = read_image_dimensions(original_path)

        prev = previous.get(rel_str, {})
        selected_preset = str(prev.get("selected_preset", default_preset_name))
        if selected_preset not in preset_map:
            selected_preset = default_preset_name

        status = str(prev.get("status", REVIEW_STATUS_ACCEPTED))
        if status not in {REVIEW_STATUS_ACCEPTED, REVIEW_STATUS_REJECTED}:
            status = REVIEW_STATUS_ACCEPTED

        last_error: Optional[str] = None
        candidate_path = review_dirs["candidate_root"] / rel_path

        if width > max_dimension or height > max_dimension:
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original_path, candidate_path)
            stats.skipped_large += 1
            stats.copied_original += 1
        else:
            preset = preset_map[selected_preset]
            try:
                upscale_one_image(
                    src_path=original_path,
                    rel_path=rel_path,
                    staging_dir=review_dirs["candidate_root"],
                    tool_bin=tool_bin,
                    models_dir=models_dir,
                    model=preset.model,
                    scale=preset.scale,
                    converter_cmd=converter_cmd,
                    verbose=verbose,
                )
                stats.upscaled += 1
            except UpscaleError as exc:
                candidate_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original_path, candidate_path)
                stats.failed += 1
                stats.copied_original += 1
                status = REVIEW_STATUS_REJECTED
                last_error = str(exc)

        preview_rel = Path(to_preview_rel_path(rel_path.as_posix()))
        preview_original = review_dirs["preview_original_root"] / preview_rel
        preview_candidate = review_dirs["preview_candidate_root"] / preview_rel
        make_browser_preview(converter_cmd, original_path, preview_original, verbose)
        make_browser_preview(converter_cmd, candidate_path, preview_candidate, verbose)

        records[rel_path.as_posix()] = ImageReviewRecord(
            rel_path=rel_path.as_posix(),
            width=width,
            height=height,
            status=status,
            selected_preset=selected_preset,
            original_path=path_to_posix_string(Path("original") / rel_path),
            candidate_path=path_to_posix_string(Path("candidate") / rel_path),
            preview_original_path=path_to_posix_string(Path("preview") / "original" / preview_rel),
            preview_candidate_path=path_to_posix_string(Path("preview") / "candidate" / preview_rel),
            last_error=last_error,
        )

    return records, stats


def run_non_review_pipeline(
    source: SourceSpec,
    dirs: Dict[str, Path],
    args: argparse.Namespace,
    converter_cmd: List[str],
    tool_bin: Path,
    models_dir: Path,
    output_pk3: Path,
) -> Stats:
    stats = Stats()
    for rel_str, data in iter_source_images(source):
        stats.discovered += 1
        rel_path = Path(rel_str)
        raw_path = dirs["raw"] / rel_path
        write_bytes(raw_path, data)

        width, height = read_image_dimensions(raw_path)
        if width > args.max_dimension or height > args.max_dimension:
            dst_path = dirs["staging"] / rel_path
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw_path, dst_path)
            stats.skipped_large += 1
            stats.copied_original += 1
            v_log(args.verbose, f"Skipped (size {width}x{height}): {rel_path}")
            continue

        try:
            upscale_one_image(
                src_path=raw_path,
                rel_path=rel_path,
                staging_dir=dirs["staging"],
                tool_bin=tool_bin,
                models_dir=models_dir,
                model=args.model,
                scale=args.scale,
                converter_cmd=converter_cmd,
                verbose=args.verbose,
            )
            stats.upscaled += 1
            v_log(args.verbose, f"Upscaled: {rel_path}")
        except UpscaleError as exc:
            stats.failed += 1
            dst_path = dirs["staging"] / rel_path
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw_path, dst_path)
            stats.copied_original += 1
            log(f"WARNING: failed to upscale {rel_path}; copied original. Details: {exc}")

    package_pk3(dirs["staging"], output_pk3, verbose=args.verbose)
    return stats


def build_review_handler(session: ReviewSession, index_path: Path) -> type:
    class ReviewHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def _read_json_body(self) -> Dict[str, object]:
            length_text = self.headers.get("Content-Length", "0")
            try:
                length = int(length_text)
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise UpscaleError(f"Invalid JSON request body: {exc}") from exc
            if not isinstance(payload, dict):
                raise UpscaleError("JSON request body must be an object.")
            return payload

        def _send_json(self, code: int, payload: Dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path) -> None:
            if not path.is_file():
                self.send_error(404, "File not found")
                return
            guessed_type, _ = mimetypes.guess_type(path.name)
            content_type = guessed_type or "application/octet-stream"
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path

            if path in {"/", "/index.html"}:
                self._send_file(index_path)
                return

            if path == "/api/state":
                self._send_json(200, session.api_state())
                return

            if path.startswith("/preview/"):
                rel = urllib.parse.unquote(path.lstrip("/"))
                candidate = session.review_root / rel
                if not ensure_inside_root(candidate, session.review_root):
                    self.send_error(403, "Invalid path")
                    return
                self._send_file(candidate)
                return

            self.send_error(404, "Not found")

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                payload = self._read_json_body()
                if path == "/api/decision":
                    rel_path = str(payload.get("rel_path", ""))
                    status = str(payload.get("status", ""))
                    summary = session.set_decision(rel_path, status)
                    self._send_json(200, {"ok": True, "summary": summary})
                    return

                if path == "/api/rerun":
                    rel_path = str(payload.get("rel_path", ""))
                    preset = str(payload.get("preset", ""))
                    result = session.rerun_one(rel_path, preset)
                    self._send_json(200, {"ok": True, "result": result})
                    return

                if path == "/api/finalize":
                    summary = session.finalize_package()
                    self._send_json(200, {"ok": True, **summary})
                    return

                if path == "/api/shutdown":
                    self._send_json(200, {"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return

                self.send_error(404, "Not found")
            except UpscaleError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:  # defensive
                self._send_json(500, {"ok": False, "error": f"Unexpected server error: {exc}"})

    return ReviewHandler


def run_review_server(
    session: ReviewSession,
    index_path: Path,
    host: str,
    port: int,
    open_browser: bool,
) -> None:
    handler_class = build_review_handler(session, index_path)
    try:
        server = ThreadingHTTPServer((host, port), handler_class)
    except OSError as exc:
        raise UpscaleError(f"Could not start review server on {host}:{port}. {exc}") from exc

    review_url = f"http://{host}:{port}/"
    log("")
    log("Review mode enabled.")
    log(f"Open review UI: {review_url}")
    log("Use Accept/Reject/Rerun in the browser, then click Package PK3.")
    log("Click Stop Server in the UI (or Ctrl+C in terminal) to exit review mode.")

    if open_browser:
        try:
            opened = webbrowser.open(review_url)
            if not opened:
                log("WARNING: Could not auto-open browser. Open the URL manually.")
        except Exception as exc:  # defensive
            log(f"WARNING: Failed to open browser automatically: {exc}")

    try:
        server.serve_forever()
    finally:
        server.server_close()


def print_stats(stats: Stats, max_dimension: int) -> None:
    log("")
    log("Upscale complete.")
    log(f"Discovered images: {stats.discovered}")
    log(f"Upscaled: {stats.upscaled}")
    log(f"Skipped by size (> {max_dimension}): {stats.skipped_large}")
    log(f"Copied originals: {stats.copied_original}")
    log(f"Failed upscales (fell back to original): {stats.failed}")


def main() -> int:
    root_dir = Path(__file__).resolve().parents[1]
    args = parse_args(root_dir)

    if args.max_dimension <= 0:
        raise UpscaleError("--max-dimension must be greater than 0.")
    if args.scale <= 0:
        raise UpscaleError("--scale must be greater than 0.")
    if args.review_port <= 0 or args.review_port > 65535:
        raise UpscaleError("--review-port must be between 1 and 65535.")

    source = resolve_source(root_dir, args)
    output_pk3 = resolve_output(root_dir, args.mod, args.output_pk3)
    converter_cmd = detect_converter()

    work_dir = args.work_dir.expanduser().resolve()
    review_manifest_path = (
        args.review_manifest.expanduser().resolve()
        if args.review_manifest
        else work_dir / "review" / "manifest.json"
    )

    should_cleanup = False
    review_completed = False

    try:
        if work_dir.exists() and not args.review:
            shutil.rmtree(work_dir)
        work_dirs = build_work_dirs(work_dir)
        for path in work_dirs.values():
            path.mkdir(parents=True, exist_ok=True)

        log(f"Mod: {args.mod}")
        log(f"Source ({source.kind}): {source.path}")
        log(f"Output PK3: {output_pk3}")
        log(f"Work dir: {work_dir}")

        tool_bin = args.tool_bin.expanduser().resolve()
        models_dir = args.models_dir.expanduser().resolve()
        ensure_realesrgan(
            tool_bin=tool_bin,
            models_dir=models_dir,
            model=args.model,
            install_dir=work_dirs["download"],
            verbose=args.verbose,
        )

        if not args.review:
            stats = run_non_review_pipeline(
                source=source,
                dirs=work_dirs,
                args=args,
                converter_cmd=converter_cmd,
                tool_bin=tool_bin,
                models_dir=models_dir,
                output_pk3=output_pk3,
            )
            print_stats(stats, args.max_dimension)
            should_cleanup = True
            return 0

        review_dirs = build_review_dirs(work_dir)
        for key in ("original_root", "candidate_root", "preview_original_root", "preview_candidate_root"):
            review_dirs[key].mkdir(parents=True, exist_ok=True)

        presets = build_rerun_presets(args, models_dir)
        previous = load_previous_decisions(review_manifest_path)
        records, stats = prepare_review_records(
            source=source,
            review_dirs=review_dirs,
            previous=previous,
            presets=presets,
            max_dimension=args.max_dimension,
            converter_cmd=converter_cmd,
            tool_bin=tool_bin,
            models_dir=models_dir,
            verbose=args.verbose,
        )

        if not records:
            raise UpscaleError("No reviewable images found under models/players in the selected source.")

        session = ReviewSession(
            review_root=review_dirs["review_root"],
            manifest_path=review_manifest_path,
            output_pk3=output_pk3,
            presets=presets,
            records=records,
            converter_cmd=converter_cmd,
            tool_bin=tool_bin,
            models_dir=models_dir,
            verbose=args.verbose,
        )
        session.save_manifest()

        index_path = review_dirs["review_root"] / "index.html"
        index_path.write_text(review_index_html())

        log("")
        log("Initial candidate generation complete.")
        log(f"Discovered images: {stats.discovered}")
        log(f"Upscaled: {stats.upscaled}")
        log(f"Skipped by size (> {args.max_dimension}): {stats.skipped_large}")
        log(f"Copied originals: {stats.copied_original}")
        log(f"Failed upscales (fallback candidate): {stats.failed}")
        log(f"Review manifest: {review_manifest_path}")

        run_review_server(
            session=session,
            index_path=index_path,
            host=args.review_host,
            port=args.review_port,
            open_browser=not args.no_open_browser,
        )

        review_completed = session.finalized
        if session.finalize_summary:
            log("")
            log("Review packaging complete.")
            log(f"Output PK3: {session.finalize_summary['output_pk3']}")
            summary = session.finalize_summary["summary"]
            log(f"Accepted: {summary['accepted']}, Rejected (original used): {summary['rejected']}")

        should_cleanup = session.finalized
        return 0
    finally:
        if args.keep_work_dir:
            log(f"Kept work dir: {work_dir}")
        elif args.review and not review_completed:
            if work_dir.exists():
                log(f"Review session not finalized; keeping work dir for resume/inspection: {work_dir}")
        elif should_cleanup and work_dir.exists():
            shutil.rmtree(work_dir)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpscaleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
