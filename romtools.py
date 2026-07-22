"""
ROMTools - FastAPI application
Run with: uvicorn romtools:app --reload --port 8000
Or:        python romtools.py
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import os
# Each request only ever has one job in flight in this pool at a time (it
# awaits every run_in_executor call before submitting the next), so the
# worker count is really "how many browser tabs can run a long scan
# simultaneously before they start queueing behind each other" - scale
# with the machine instead of a flat 2, which stalled a 3rd concurrent
# scan behind the first two.
_thread_pool = ThreadPoolExecutor(max_workers=max(4, os.cpu_count() or 4))
import hashlib
import json
import re
import platform
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from xml.sax.saxutils import escape, unescape
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path
from typing import AsyncGenerator

import py7zr
import uvicorn
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="ROMTools")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FOLDER_EXCL_BASE  = ["media","images","manuals","maps","ports",
                      "advancemame","mame2003","mame2003plus",
                      "mame2010","mame2015","gamelink"]
FOLDER_EXCL_EXTRA = ["DATA","games"]   # RetroBat only

# Single canonical file-extension exclusion list for every function that
# examines ROM folders for ROM files (ROM File List, DAT Scanner folder/
# coverage counts, Game Manager, Duplicates, Compare). Deliberately NOT used
# for media-folder scanning (Media Cleaner matches by exact gamelist media
# reference, not by extension). Shown/editable as the default in the ROM
# File List tab; every other endpoint below uses it as a fixed constant.
ROM_FILE_EXCL = [".txt",".png",".pdf",".backup",".xml",".keep",".cfg",".conf",
                 ".jpg",".jpeg",".old",".bak",".nfo",".sfv",".cue",".m3u",
                 ".json",".dat",".db",".log",".ini",".bat",".sh",".srm",
                 ".state",".state1",".state2",".state3",".state4"]
ROM_FILE_EXCL_SET = set(ROM_FILE_EXCL)  # fast-lookup form for hot per-file checks
FOLDER_EXCL_MD5   = ["media","images","manuals","maps","ports"]

def list_rom_candidate_files(folder_path: Path, file_excl: set[str] | None = None) -> list[Path]:
    """ROM files directly in folder_path (non-recursive). Matches by
    endswith(), not Path.suffix - a bare dotfile like ".keep" has an empty
    .suffix (pathlib treats a leading dot as a hidden-file marker, not an
    extension separator), so suffix-based matching would silently fail to
    exclude it.

    Uses os.scandir() rather than Path.iterdir() + Path.is_file(): scandir's
    directory entries carry cached type info from the single OS-level
    listing call, so the is-file check costs nothing extra. Path.is_file()
    instead issues its own separate stat() per entry - on a local disk that's
    negligible, but on a network share each of those is a real round trip,
    and it multiplies across every file in every folder scanned."""
    excl = file_excl if file_excl is not None else ROM_FILE_EXCL_SET
    files: list[Path] = []
    try:
        with os.scandir(folder_path) as it:
            for entry in it:
                if entry.is_file() and not any(entry.name.lower().endswith(e) for e in excl):
                    files.append(Path(entry.path))
    except OSError:
        return []
    return files

# ---------------------------------------------------------------------------
# Shared filesystem helpers
# ---------------------------------------------------------------------------

def list_system_folders(rompath: str, folder_excl: list[str]) -> dict[str, list[str]]:
    """Return {system_folder: [xml_files]} for every system subfolder
    that contains at least one .xml file."""
    result: dict[str, list[str]] = {}
    base = Path(rompath)
    if not base.is_dir():
        return result
    try:
        with os.scandir(base) as it:
            top_entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return result
    for item in top_entries:
        if not item.is_dir() or item.name in folder_excl:
            continue
        try:
            with os.scandir(item.path) as it:
                xmls = [e.name for e in it if e.is_file() and e.name.endswith(".xml")]
        except OSError:
            continue
        if xmls:
            result[item.name] = xmls
    return result


def detect_media_mode(rompath: str, folder_excl: list[str]) -> str:
    """Return 'retrobat', 'recalbox', or 'unknown' by inspecting
    the first valid system subfolder."""
    base = Path(rompath)
    if not base.is_dir():
        return "unknown"
    for item in sorted(base.iterdir()):
        if not item.is_dir() or item.name in folder_excl:
            continue
        if (item / "images").is_dir():
            return "retrobat"
        if (item / "media" / "images").is_dir():
            return "recalbox"
    return "unknown"


# RetroBat media fields and their subfolders
RETROBAT_MEDIA_FIELDS = {
    "image":     "images",
    "marquee":   "images",
    "thumbnail": "images",
    "bezel":     "images",
    "boxback":   "images",
    "fanart":    "images",
    "video":     "videos",
    "manual":    "manuals",
}

# Recalbox media fields and their subfolders
RECALBOX_MEDIA_FIELDS = {
    "image": "media/images",
    "video": "media/videos",
}

# Game Manager — full gamelist.xml field sets per platform (superset of every
# child tag observed on <game> across sample RetroBat / Recalbox gamelists).
RETROBAT_GAMELIST_FIELDS = [
    "path","name","desc","genre","image","marquee","thumbnail","bezel",
    "boxback","fanart","rating","releasedate","developer","publisher",
    "family","players","md5","lang","region","scrap","arcadesystemname",
    "crc32","core","emulator","favorite","gametime","hidden","lastplayed",
    "manual","playcount","video","multidisk",
]
RECALBOX_GAMELIST_FIELDS = [
    "hash","region","publisher","developer","releasedate","image","video",
    "desc","rating","name","path","genre","genreid","players","aliases",
    "licences","adult","rotation","thumbnail",
]
# De-duplicated display order: identity/media fields first, then the rest.
GAMELIST_FIELDS_ALL = list(dict.fromkeys(
    ["path","name","desc"] + RETROBAT_GAMELIST_FIELDS + RECALBOX_GAMELIST_FIELDS
))


def _walk_gamelist_media(xml_path: Path, mode: str) -> list[tuple[str, str, dict[str, str]]]:
    """Parse a gamelist.xml once and return, per <game>:
        (raw_path_text, rom_filename, {media_field: relative_path})
    using the field set appropriate for mode (RetroBat: image/marquee/
    thumbnail/video/manual; Recalbox: image only). Shared by
    get_xml_media_refs() (Media Cleaner's aggregate orphan-detection view)
    and delete_unverified() (the per-ROM view it needs to know which media
    files belong to a specific deleted ROM) so the field list and path
    handling live in exactly one place instead of two copies that could
    drift apart."""
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        return []

    field_map = RETROBAT_MEDIA_FIELDS if mode == "retrobat" else RECALBOX_MEDIA_FIELDS
    out: list[tuple[str, str, dict[str, str]]] = []
    for game in find_game_elements(tree.getroot()):
        path_el = game.find("path")
        if path_el is None or not path_el.text:
            continue
        raw_path = path_el.text
        rom_name = Path(raw_path.lstrip("./")).name
        fields: dict[str, str] = {}
        for field in field_map:
            el = game.find(field)
            if el is not None and el.text:
                fields[field] = el.text.lstrip("./")
        out.append((raw_path, rom_name, fields))
    return out


# Recalbox's classic gamelist.xml schema has no <manual> field at all, but
# its scraper still names every media file "<title> <32-hex-char-hash>.ext"
# (the hash differs per media type, but the title prefix is shared). Strips
# that suffix so manuals can be matched to a known game by title even
# though there's no XML tag pointing at them.
_MEDIA_HASH_SUFFIX_RE = re.compile(r"^(.*) [0-9a-f]{32}\.[^.]+$", re.IGNORECASE)


def _media_base_name(filename: str) -> str | None:
    """Strip a Recalbox-style scraped media filename's ' <hash>.<ext>'
    suffix, returning the shared title prefix (e.g. 'SoulCalibur
    a1c6d2e0b20c4a046bc99ef08583239a.png' -> 'SoulCalibur'). Returns None
    if the filename doesn't match that convention."""
    m = _MEDIA_HASH_SUFFIX_RE.match(filename)
    return m.group(1) if m else None


def get_xml_media_refs(xml_path: Path, mode: str) -> tuple[set[str], int, list[str], dict[str, str], set[str], dict[str, str]]:
    """Parse a gamelist.xml and return:
      - set of ALL media file full paths referenced (images, videos, manuals)
      - count of games with no image tag
      - list of rom paths with no image tag
      - ref_types: full path -> gamelist.xml field name (image/marquee/
        thumbnail/bezel/boxback/fanart/video/manual), so callers can show
        which media type a given reference is without re-parsing the XML
      - media_base_names: title prefixes (see _media_base_name()) of every
        referenced file - lets Recalbox manuals (which have no XML field)
        be matched to a known game by naming convention instead of an
        exact tag lookup
      - ref_rom_paths: full media path -> the owning game's raw <path>
        text, so callers can trace a missing media reference back to the
        ROM it belongs to (e.g. to remove that <game> entry)
    """
    refs: set[str] = set()
    ref_types: dict[str, str] = {}
    media_base_names: set[str] = set()
    ref_rom_paths: dict[str, str] = {}
    no_img_count   = 0
    no_img_list:  list[str] = []
    system_dir = xml_path.parent

    for raw_path, _rom_name, fields in _walk_gamelist_media(xml_path, mode):
        for field, relative in fields.items():
            full = str(system_dir / relative)
            refs.add(full)
            ref_types[full] = field
            ref_rom_paths[full] = raw_path
            base = _media_base_name(Path(relative).name)
            if base:
                media_base_names.add(base)
        if "image" not in fields:
            no_img_count += 1
            no_img_list.append(raw_path)

    return refs, no_img_count, no_img_list, ref_types, media_base_names, ref_rom_paths


# Some scraped filenames abbreviate their gamelist.xml field name (e.g. the
# "thumbnail" field is written as "-thumb.png"). Maps the on-disk suffix
# back to the XML field name so Media Cleaner's Orphans and Missing tables
# use the same Type vocabulary even though orphans have no XML field to
# read the type from directly (they're inferred from the filename).
MEDIA_TYPE_SUFFIX_ALIASES = {"thumb": "thumbnail"}
KNOWN_MEDIA_TYPES = {"image", "marquee", "thumbnail", "bezel", "boxback",
                      "fanart", "video", "manual"}
# Recalbox filenames are "<title> <hash>.ext" with no type suffix at all
# (and titles routinely contain dashes, e.g. "Alone in the Dark - The New
# Nightmare"), so the RetroBat-style suffix heuristic below would extract
# garbage. Recalbox instead keeps each media type in its own dedicated
# folder, so the folder name alone is authoritative there.
_FOLDER_TYPE_FALLBACK = {"images": "image", "videos": "video", "manuals": "manual"}


def guess_media_type(filename: str, dir_name: str = "") -> str:
    """Infer a media type (image/marquee/thumbnail/bezel/boxback/fanart/
    video/manual) for an orphaned file, which by definition has no
    gamelist.xml field to read the type from directly. Tries the RetroBat
    '<romname>-<type>.<ext>' suffix convention first; falls back to
    dir_name (the file's containing media folder) for layouts like
    Recalbox's where the filename carries no type suffix."""
    stem = Path(filename).stem
    if "-" in stem:
        suffix = stem.rsplit("-", 1)[1]
        canonical = MEDIA_TYPE_SUFFIX_ALIASES.get(suffix, suffix)
        if canonical in KNOWN_MEDIA_TYPES:
            return canonical
    return _FOLDER_TYPE_FALLBACK.get(dir_name, "unknown")


def get_media_dirs(system_path: Path, mode: str) -> list[Path]:
    """Return all media subdirectories that exist for this system."""
    if mode == "retrobat":
        candidates = ["images", "videos", "manuals"]
    else:
        candidates = ["media/images", "media/videos", "media/manuals"]
    return [system_path / c for c in candidates if (system_path / c).is_dir()]


def scan_image_dir(imgdir: Path) -> dict[str, str]:
    """Return {filename: full_path_str} for all files in imgdir. Uses
    os.scandir() so the is-file check reuses the cached directory listing
    instead of a separate stat() per file - matters a lot on a network
    share with thousands of scraped media files."""
    entries: list[tuple[str, str]] = []
    try:
        with os.scandir(imgdir) as it:
            for entry in it:
                if entry.is_file():
                    entries.append((entry.name, entry.path))
    except OSError:
        return {}
    entries.sort(key=lambda t: t[0])
    return dict(entries)


def md5_of_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def crc32_of_file(path: Path, chunk: int = 1 << 20) -> str:
    crc = 0
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            crc = zlib.crc32(block, crc)
    return f"{crc & 0xFFFFFFFF:08x}"


def list_roms(rompath: str,
              file_excl: list[str],
              folder_excl: list[str],
              base_rel: str = "") -> dict[str, list[str]]:
    """Recursively list ROM files, returning {folder: [filenames]}.
    When rompath is a single system folder (not a root), uses the
    folder name itself as the bucket so the Folder column is populated.

    base_rel prefixes every bucket key as if rompath were already nested
    under a folder of that name (e.g. base_rel="nes" turns bucket "media"
    into "nes/media"). Used by compare_scan() to scan one system folder at
    a time (for per-system SSE progress) while still producing the exact
    same bucket keys as a single whole-tree call would.
    """
    result: dict[str, list[str]] = {}
    base = Path(rompath)
    if not base.is_dir():
        return result

    # Detect if this is a single system folder or a root with subfolders.
    # A single system folder has ROM files directly in it. Uses os.scandir()
    # so the is-dir check reuses the cached directory listing instead of a
    # separate stat() per entry - matters a lot on a network share.
    has_subfolders = False
    try:
        with os.scandir(base) as it:
            for entry in it:
                if entry.is_dir() and entry.name not in folder_excl:
                    has_subfolders = True
                    break
    except OSError:
        pass
    # Use folder name as top-level bucket when scanning a single system
    top_label = "" if has_subfolders else base.name

    def _recurse(directory: Path, rel: str) -> None:
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if entry.is_dir():
                if entry.name not in folder_excl:
                    _recurse(Path(entry.path), f"{rel}/{entry.name}" if rel else entry.name)
            else:
                # Case-insensitive, suffix-only match - same rule as
                # list_rom_candidate_files() (used by DAT Scanner/Game
                # Manager/Duplicates), so MD5 Scan and Compare (the callers
                # of this function) treat exclusions identically to every
                # other tab instead of case-sensitive substring-anywhere
                # matching, which let uppercase non-ROM files (e.g. a
                # README.TXT) slip through here as if they were ROMs.
                name_lower = entry.name.lower()
                if not any(name_lower.endswith(e.lower()) for e in file_excl):
                    bucket = rel or top_label or base.name
                    result.setdefault(bucket, []).append(entry.name)

    _recurse(base, base_rel)
    return result


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------

def sse_event(data: dict) -> str:
    """Format a dict as a Server-Sent Event string."""
    return f"data: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# Routes - pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("base.html", {
        "request": request,
        "active_tab": "home",
    })


# ---------------------------------------------------------------------------
# Routes - API: folder browser
# ---------------------------------------------------------------------------

@app.get("/api/browse")
async def browse(dir: str = Query(default=""),
                  files: bool = Query(default=False),
                  ext: str = Query(default="")):
    """List subdirectories (and, opt-in, files) of a directory for the
    frontend's folder/file browser modal. files=True is used by the file-
    picking mode (e.g. selecting chdman.exe); ext optionally filters the
    file list to one extension (e.g. ".exe")."""
    if not dir:
        # Return drive roots on Windows, / on Unix
        if platform.system() == "Windows":
            roots = [f"{l}:\\" for l in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                     if Path(f"{l}:\\").exists()]
            return JSONResponse({"current": "", "parent": None, "dirs": roots, "files": []})
        else:
            dirs = sorted(str(p) for p in Path("/").iterdir() if p.is_dir())
            return JSONResponse({"current": "/", "parent": None, "dirs": dirs, "files": []})

    p = Path(dir).resolve()
    if not p.is_dir():
        return JSONResponse({"error": f"Not a directory: {dir}"}, status_code=400)

    parent = str(p.parent) if p != p.parent else None
    dirs   = sorted(str(c) for c in p.iterdir() if c.is_dir())
    file_list: list[str] = []
    if files:
        ext_lower = ext.lower()
        file_list = sorted(
            str(c) for c in p.iterdir()
            if c.is_file() and (not ext_lower or c.name.lower().endswith(ext_lower))
        )
    return JSONResponse({"current": str(p), "parent": parent, "dirs": dirs, "files": file_list})


# ---------------------------------------------------------------------------
# Routes - API: App settings (currently just chdman's path)
# ---------------------------------------------------------------------------

@app.get("/api/settings")
async def get_settings():
    config = load_app_config()
    resolved = get_chdman_path()
    return JSONResponse({
        "chdman_path":      config.get("chdman_path", ""),
        "chdman_resolved":  str(resolved) if resolved else "",
        "chdman_available": resolved is not None,
    })


@app.post("/api/settings")
async def save_settings(request: Request):
    body   = await request.json()
    config = load_app_config()
    if "chdman_path" in body and body["chdman_path"] != config.get("chdman_path", ""):
        config["chdman_path"] = body["chdman_path"]
        save_app_config(config)
        # verify_results caches CHD folders as "unknown" when chdman isn't
        # resolvable; the cache signature has no dependency on chdman config,
        # so a stale "unknown" result would otherwise survive re-pointing at
        # a working chdman.exe until manually cleared.
        clear_cache_table("verify_results")
    else:
        save_app_config(config)
    resolved = get_chdman_path()
    return JSONResponse({
        "status":           "saved",
        "chdman_resolved":  str(resolved) if resolved else "",
        "chdman_available": resolved is not None,
    })


@app.get("/api/settings/cache")
async def get_cache_stats():
    return JSONResponse(cache_stats())


@app.post("/api/settings/cache/clear")
async def post_clear_cache():
    counts = clear_cache()
    return JSONResponse({"status": "cleared", "cleared": counts})


@app.post("/api/dat/verify-cache/clear")
async def post_clear_verify_cache(rompath: str = Form(...), datroot: str = Form(...)):
    """Clear only this rompath+datroot's verify_results cache rows - the
    server-side counterpart to the ROM Scanner Verify page's 'Clear cached
    results' button, so it invalidates the actual cache instead of just
    the client-side localStorage replay copy."""
    removed = clear_verify_results_for(rompath, datroot)
    return JSONResponse({"status": "cleared", "removed": removed})


@app.post("/api/dat/verify-cache/lookup")
async def post_verify_cache_lookup(
    rompath: str       = Form(...),
    datroot: str       = Form(...),
    folders: list[str] = Form(default=[]),
):
    """Batch-fetch cached verify_results for a set of folders WITHOUT
    touching the DAT XML at all - no load_dat_hashes/descriptions/
    game_details/clone_info calls, just cheap file stats. Lets the ROM
    Scanner Verify page show last-scan results instantly on every visit
    (the job localStorage replay used to do) straight from the uncapped
    SQLite cache, instead of paying the DAT-dict-rebuild cost of a full
    /api/dat/verify-folders call just to check what's already cached."""
    mapping  = load_dat_mapping(datroot)
    dat_root = Path(datroot)
    rom_root = Path(rompath)

    # One signature per folder, computed the same way verify_folders does,
    # but without ever loading DAT contents - only stat()s.
    signatures: dict[str, str] = {}
    for folder in folders:
        dat_files = [dat_root / d for d in mapping.get(folder, []) if (dat_root / d).exists()]
        rom_files = list_rom_candidate_files(rom_root / folder)
        signatures[folder] = compute_verify_signature(rom_files, dat_files)

    conn = _cache_conn()
    placeholders = ",".join("?" for _ in folders)
    rows = []
    if folders:
        rows = conn.execute(
            f"SELECT folder, hash_type, signature, result_json, updated_at "
            f"FROM verify_results WHERE rompath = ? AND datroot = ? AND folder IN ({placeholders}) "
            f"ORDER BY updated_at DESC",
            (rompath, datroot, *folders),
        ).fetchall()

    # Keep only the most-recently-updated row per folder (a folder can have
    # separate cached rows per hash_type from different past scans).
    latest_by_folder: dict[str, tuple] = {}
    for folder, hash_type, signature, result_json, updated_at in rows:
        if folder not in latest_by_folder:
            latest_by_folder[folder] = (hash_type, signature, result_json, updated_at)

    out = {}
    for folder in folders:
        row = latest_by_folder.get(folder)
        if row is None:
            out[folder] = {"status": "none", "hash_type": None, "result": None, "updated_at": None}
            continue
        hash_type, signature, result_json, updated_at = row
        if signature == signatures.get(folder):
            out[folder] = {
                "status":     "hit",
                "hash_type":  hash_type,
                "result":     json.loads(result_json),
                "updated_at": updated_at,
            }
        else:
            out[folder] = {"status": "stale", "hash_type": hash_type, "result": None, "updated_at": updated_at}

    return JSONResponse({"folders": out})


# ---------------------------------------------------------------------------
# Routes - API: Update check
# ---------------------------------------------------------------------------
GITHUB_REPO       = "WaxTools/pyrom-manager"
APP_VERSION       = (Path(__file__).parent / "VERSION").read_text(encoding="utf-8").strip() \
                     if (Path(__file__).parent / "VERSION").exists() else "0.0.0"
_update_check_cache: dict = {}
_UPDATE_CHECK_TTL = 6 * 3600  # don't hit the GitHub API more than once per 6h


def _parse_version(v: str) -> tuple:
    parts = v.lstrip("vV").split(".")
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            break
    return tuple(out) or (0,)


@app.get("/api/update-check")
async def update_check():
    now = time.time()
    if _update_check_cache and now - _update_check_cache.get("checked_at", 0) < _UPDATE_CHECK_TTL:
        return JSONResponse(_update_check_cache["result"])

    result = {
        "current":          APP_VERSION,
        "latest":            None,
        "update_available": False,
        "release_url":       f"https://github.com/{GITHUB_REPO}/releases/latest",
        "checked":           False,
    }
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "PYRom-Manager"},
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = data.get("tag_name", "")
        result["latest"]      = latest
        result["release_url"] = data.get("html_url", result["release_url"])
        result["update_available"] = _parse_version(latest) > _parse_version(APP_VERSION)
        result["checked"] = True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        pass  # offline, rate-limited, or no releases published yet - just skip silently

    _update_check_cache["checked_at"] = now
    _update_check_cache["result"]     = result
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Routes - API: Media Cleaner - scan (SSE)
# ---------------------------------------------------------------------------

@app.post("/api/media/scan")
async def media_scan(
    rompath: str = Form(...),
):
    """Always auto-detects RetroBat vs Recalbox media layout - no manual
    override, since auto-detection has been reliable enough in practice
    that the escape hatch was unused complexity."""
    async def generate() -> AsyncGenerator[str, None]:
        folder_excl = FOLDER_EXCL_BASE[:]
        base        = Path(rompath)

        # Detect if rompath is a single system folder (has gamelist.xml directly)
        # or a root folder containing multiple system subfolders
        xml_files_in_root = list(base.glob("gamelist.xml"))
        is_single_system  = len(xml_files_in_root) > 0

        if is_single_system:
            # Detect mode by checking for images/ or media/images/ in this folder
            if (base / "images").is_dir():
                resolved = "retrobat"
            elif (base / "media" / "images").is_dir():
                resolved = "recalbox"
            else:
                resolved = "retrobat"  # safe fallback
        else:
            resolved = detect_media_mode(rompath, FOLDER_EXCL_BASE)
            if resolved == "unknown":
                resolved = "retrobat"
        yield sse_event({"type": "mode", "mode": resolved})

        if resolved == "retrobat":
            folder_excl += FOLDER_EXCL_EXTRA

        # Build systems dict - either one system or multiple
        if is_single_system:
            system_name = base.name
            xmls        = [f.name for f in xml_files_in_root]
            systems     = {system_name: xmls}
            # For single system, rompath is the system folder itself
            scan_root   = base.parent
        else:
            systems   = list_system_folders(rompath, folder_excl)
            scan_root = base

        if not systems:
            yield sse_event({"type": "error",
                             "message": f"No system folders found in {rompath}"})
            return

        yield sse_event({"type": "start", "total": len(systems)})

        for system, xml_files in systems.items():
            system_path = scan_root / system
            media_dirs  = get_media_dirs(system_path, resolved)

            for xml_file in xml_files:
                xml_path = system_path / xml_file
                xml_refs, no_img_count, no_img_list, ref_types, media_base_names, ref_rom_paths = \
                    get_xml_media_refs(xml_path, resolved)

                # Scan ALL media subdirectories present on disk
                all_disk: dict[str, str] = {}
                for mdir in media_dirs:
                    all_disk.update(scan_image_dir(mdir))

                # Orphans: on disk but full path not in xml_refs.
                # Group by subfolder for clearer reporting.
                # Special case: Recalbox's gamelist.xml schema has no
                # <manual> field at all, so manuals can never appear in
                # xml_refs by path - only by naming convention. Treat a
                # manual as valid if its title prefix matches a game that
                # *is* referenced (via image/video), instead of flagging
                # every Recalbox manual as orphaned regardless of content.
                orphans_by_dir: dict[str, list[str]] = {}
                for name, full in all_disk.items():
                    if full in xml_refs:
                        continue
                    if resolved == "recalbox" and Path(full).parent.name == "manuals":
                        base = _media_base_name(name)
                        if base and base in media_base_names:
                            continue
                    mdir_str = str(Path(full).parent)
                    orphans_by_dir.setdefault(mdir_str, []).append(name)

                # Flat list for backwards compat with delete endpoint
                orphans = [name for files in orphans_by_dir.values() for name in files]
                # Media type per orphan filename (inferred from the
                # filename suffix, or the containing folder as a fallback -
                # see guess_media_type()), keyed by filename so the
                # frontend can add a Type column without touching
                # orphans_by_dir's shape (that one's sent as-is to the
                # delete endpoint and must stay filename-only).
                orphan_types: dict[str, str] = {}
                for dir_path, files in orphans_by_dir.items():
                    dir_name = Path(dir_path).name
                    for name in files:
                        orphan_types[name] = guess_media_type(name, dir_name)

                # Missing: in xml_refs but not on disk. Includes each ref's
                # gamelist.xml field name (Type column) and the owning
                # game's raw <path> text (rom_path) so the frontend can
                # offer to remove that stale <game> entry.
                disk_paths = set(all_disk.values())
                missing    = [
                    {"path": p, "type": ref_types.get(p, "unknown"),
                     "rom_path": ref_rom_paths.get(p, "")}
                    for p in xml_refs if p not in disk_paths
                ]

                yield sse_event({
                    "type":           "folder",
                    "system":         system,
                    "imgdir":         str(system_path),  # base path now
                    "media_dirs":     [str(d) for d in media_dirs],
                    "orphans_by_dir": orphans_by_dir,
                    "orphan_types":   orphan_types,
                    "xml_file":       xml_file,
                    "orphans":        orphans,
                    "missing":        missing,
                    "no_img_count":   no_img_count,
                    "no_img_list":    no_img_list,
                    "disk_count":     len(all_disk),
                    "xml_count":      len(xml_refs),
                })

                await asyncio.sleep(0)

        yield sse_event({"type": "done"})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Routes - API: Media Cleaner - delete orphans for one folder
# ---------------------------------------------------------------------------

@app.post("/api/media/delete")
async def media_delete(request: Request):
    """Delete orphaned media files.
    Accepts either:
    - {imgdir, files: [filename,...]}  - legacy single-dir mode
    - {files_by_dir: {dir: [filename,...]}}  - multi-dir mode
    """
    body    = await request.json()
    results = []

    # Build list of (filepath) to delete
    to_delete: list[Path] = []

    if "files_by_dir" in body:
        for dirpath, filenames in body["files_by_dir"].items():
            base = Path(dirpath)
            if not base.is_dir():
                continue
            for filename in filenames:
                to_delete.append(base / Path(filename).name)
    else:
        # Legacy mode
        imgdir = body.get("imgdir", "")
        files  = body.get("files", [])
        if not imgdir or not Path(imgdir).is_dir():
            return JSONResponse({"error": f"Invalid directory: {imgdir}"}, status_code=400)
        for filename in files:
            to_delete.append(Path(imgdir) / Path(filename).name)

    for filepath in to_delete:
        if not filepath.exists():
            results.append({"file": filepath.name, "status": "not_found"})
        else:
            try:
                filepath.unlink()
                results.append({"file": filepath.name, "status": "deleted"})
            except OSError as e:
                results.append({"file": filepath.name, "status": "error", "detail": str(e)})

    return JSONResponse({"results": results})


@app.post("/api/media/remove-gamelist-entries")
async def media_remove_gamelist_entries(request: Request):
    """Remove <game> entries from gamelist.xml for ROMs Media Cleaner
    flagged as having no image tag or missing media files - there's no
    orphaned file to delete for these two cases, just a stale/incomplete
    XML entry, so this removes the entry instead (backed up first via the
    same remove_gamelist_entries() convention every other tab uses).
    Body: {system_path, xml_file, files: [rom_path,...]}."""
    body        = await request.json()
    system_path = Path(body.get("system_path", ""))
    xml_file    = body.get("xml_file", "")
    files       = body.get("files", [])

    gamelist_path = system_path / xml_file
    if not gamelist_path.is_file():
        return JSONResponse({"error": f"gamelist.xml not found: {gamelist_path}"}, status_code=400)

    filenames = {Path(f).name for f in files}
    results, backup_path = remove_gamelist_entries(gamelist_path, filenames)
    return JSONResponse({"results": results, "backup": backup_path})


# ---------------------------------------------------------------------------
# Routes - API: MD5 scan (SSE)
# ---------------------------------------------------------------------------

@app.post("/api/md5/scan")
async def md5_scan(
    rompath:      str  = Form(...),
    file_excl:    str  = Form(default=""),
    folder_excl:  str  = Form(default=""),
    show_crc:     bool = Form(default=False),
    show_md5:     bool = Form(default=False),
    show_sha1:    bool = Form(default=False),
    show_inner:   bool = Form(default=False),
):
    fexcl = [x.strip() for x in file_excl.split(",")] if file_excl else ROM_FILE_EXCL
    dexcl = [x.strip() for x in folder_excl.split(",")] if folder_excl else FOLDER_EXCL_MD5

    async def generate() -> AsyncGenerator[str, None]:
        rom_map = list_roms(rompath, fexcl, dexcl)
        if not rom_map:
            yield sse_event({"type": "error", "message": f"No files found in {rompath}"})
            return

        total = sum(len(v) for v in rom_map.values())
        yield sse_event({"type": "start", "total": total})

        for folder, filenames in sorted(rom_map.items()):
            for filename in filenames:
                base = Path(rompath)
                if folder and (base / folder).is_dir():
                    filepath = base / folder / filename
                else:
                    filepath = base / filename
                entry: dict = {
                    "type":        "file",
                    "folder":      folder,
                    "filename":    filename,
                    "filepath":    str(filepath),
                    "zip_entries": [],
                    "crc":         None,
                    "md5":         None,
                    "sha1":        None,
                }
                ext        = filepath.suffix.lower()
                is_archive = ext in (".zip", ".7z")

                if show_crc or show_md5 or show_sha1:
                    try:
                        fstat = filepath.stat()
                    except OSError:
                        fstat = None

                    if is_archive and show_inner:
                        for name, data in read_archive_entries(filepath).items():
                            cached = get_cached_simple(filepath, name, fstat.st_mtime, len(data)) if fstat else None
                            crc_val, md5_val, sha1_val = (cached.get("crc"), cached.get("md5"), cached.get("sha1")) if cached else (None, None, None)
                            ze: dict = {"name": name}
                            if show_crc:
                                if crc_val is None: crc_val = f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
                                ze["crc"] = crc_val
                            if show_md5:
                                if md5_val is None: md5_val = hashlib.md5(data).hexdigest()
                                ze["md5"] = md5_val
                            if show_sha1:
                                if sha1_val is None: sha1_val = hashlib.sha1(data).hexdigest()
                                ze["sha1"] = sha1_val
                            entry["zip_entries"].append(ze)
                            if fstat:
                                set_cached_simple(filepath, name, fstat.st_mtime, len(data), crc=crc_val, md5=md5_val, sha1=sha1_val)
                    else:
                        cached = get_cached_simple(filepath, "", fstat.st_mtime, fstat.st_size) if fstat else None
                        crc_val, md5_val, sha1_val = (cached.get("crc"), cached.get("md5"), cached.get("sha1")) if cached else (None, None, None)

                        if show_crc and crc_val is None:
                            crc_val = crc32_of_file(filepath)
                        if show_md5 and md5_val is None:
                            md5_val = md5_of_file(filepath)
                        if show_sha1 and sha1_val is None:
                            h = hashlib.sha1()
                            with open(filepath, "rb") as f:
                                for block in iter(lambda: f.read(1 << 20), b""):
                                    h.update(block)
                            sha1_val = h.hexdigest()

                        if show_crc:  entry["crc"]  = crc_val
                        if show_md5:  entry["md5"]  = md5_val
                        if show_sha1: entry["sha1"] = sha1_val
                        if fstat:
                            set_cached_simple(filepath, "", fstat.st_mtime, fstat.st_size, crc=crc_val, md5=md5_val, sha1=sha1_val)

                yield sse_event(entry)
                await asyncio.sleep(0)

        yield sse_event({"type": "done"})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Routes - API: Recalbox readme cleaner
# ---------------------------------------------------------------------------

README_FILES = {
    "_readme.txt",
    "_leeme.txt",
    "_leggime.txt",
    "_leiame.txt",
    "_liesmich.txt",
    "_lisezmoi.txt",
}

@app.post("/api/dup/clean-readmes")
async def clean_readmes(request: Request):
    body    = await request.json()
    rompath = body.get("rompath", "")
    results = []

    root = Path(rompath)
    if not root.exists():
        return JSONResponse({"error": f"Path does not exist: {rompath}"}, status_code=400)

    for filepath in sorted(root.rglob("*")):
        if not filepath.is_file():
            continue
        if filepath.name.lower() not in README_FILES:
            continue
        try:
            filepath.unlink()
            results.append({"file": str(filepath), "status": "deleted"})
        except OSError as e:
            results.append({"file": str(filepath), "status": "error", "detail": str(e)})

    return JSONResponse({"results": results})


# ---------------------------------------------------------------------------
# DAT helpers
# ---------------------------------------------------------------------------

def find_game_elements(root: ET.Element) -> list[ET.Element]:
    """Return game/machine elements from a DAT root.
    MAME DATs use <machine> tags; No-Intro/Redump/FBNeo use <game> tags.
    """
    games = root.findall("game")
    if not games:
        games = root.findall("machine")
    return games


def is_clone_element(el: ET.Element) -> bool:
    """True if this game/machine element is a clone (cloneofid/cloneof/romof)."""
    return bool(el.get("cloneofid") or el.get("cloneof") or el.get("romof"))


def detect_dat_format(header: ET.Element) -> str:
    """Return 'nointro', 'redump', 'mame', or 'unknown' based on DAT header."""
    homepage = header.findtext("homepage", "").lower()
    url      = header.findtext("url", "").lower()
    author   = header.findtext("author", "").lower()
    name     = header.findtext("name", "").lower()
    if "no-intro" in homepage or "no-intro" in url:
        return "nointro"
    if "redump" in homepage or "redump" in url or "redump" in author:
        return "redump"
    # FBNeo's own DAT spells its name out in full ("FinalBurn Neo - Arcade
    # Games") rather than using the "fbneo"/"fba" abbreviation, so match
    # "finalburn" too - and check author as well as name, since either
    # field can carry the identifying text depending on the DAT source.
    mame_keywords = ("mame", "fbneo", "fba", "finalburn")
    if any(kw in name or kw in author for kw in mame_keywords):
        return "mame"
    return "unknown"


# DAT files can be huge (MAME's is ~80MB) and multiple endpoints/folders
# often need the same file parsed independently within one request (e.g. a
# DAT mapped to two folders, or the catalogue+coverage scans back to back).
# Cache the parsed tree by path+mtime+size so the expensive full XML parse
# only happens once per file version - every caller still recomputes its
# own derived results fresh from the tree, so outputs are unchanged.
# Bounded to the N most recently used DAT files (LRU via OrderedDict) so a
# long-running process doesn't keep every DAT ever parsed resident forever
# - a parsed ElementTree typically occupies several times its raw file
# size in memory, and MAME's alone is ~80MB on disk.
_DAT_TREE_CACHE_MAX = 8
_dat_tree_cache: "OrderedDict[str, tuple[float, int, ET.Element]]" = OrderedDict()


def _parse_dat_cached(dat_path: Path) -> ET.Element:
    """Return the parsed root Element for a DAT file, memoized by
    path+mtime+size. Raises ET.ParseError exactly like ET.parse() would -
    callers keep their existing try/except handling unchanged."""
    key = str(dat_path)
    st = dat_path.stat()
    cached = _dat_tree_cache.get(key)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        _dat_tree_cache.move_to_end(key)
        return cached[2]
    root = ET.parse(dat_path).getroot()
    _dat_tree_cache[key] = (st.st_mtime, st.st_size, root)
    _dat_tree_cache.move_to_end(key)
    if len(_dat_tree_cache) > _DAT_TREE_CACHE_MAX:
        _dat_tree_cache.popitem(last=False)
    return root


# ---------------------------------------------------------------------------
# SQLite result cache — persists expensive per-file hashes and per-folder
# DAT-verify results across requests/restarts, so repeat scans of unchanged
# folders skip re-hashing (a CHD extraction alone costs 3-13s per file).
# ---------------------------------------------------------------------------

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DB_PATH = CACHE_DIR / "cache.db"
_cache_local = threading.local()


def _init_cache_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS file_hashes (
            path        TEXT NOT NULL,
            hash_type   TEXT NOT NULL,
            mtime       REAL NOT NULL,
            size        INTEGER NOT NULL,
            hashes_json TEXT NOT NULL,
            updated_at  REAL NOT NULL,
            PRIMARY KEY (path, hash_type)
        );
        CREATE TABLE IF NOT EXISTS verify_results (
            rompath     TEXT NOT NULL,
            datroot     TEXT NOT NULL,
            folder      TEXT NOT NULL,
            hash_type   TEXT NOT NULL,
            signature   TEXT NOT NULL,
            result_json TEXT NOT NULL,
            updated_at  REAL NOT NULL,
            PRIMARY KEY (rompath, datroot, folder, hash_type)
        );
        CREATE TABLE IF NOT EXISTS simple_hashes (
            path        TEXT NOT NULL,
            inner_name  TEXT NOT NULL DEFAULT '',
            mtime       REAL NOT NULL,
            size        INTEGER NOT NULL,
            crc         TEXT,
            md5         TEXT,
            sha1        TEXT,
            updated_at  REAL NOT NULL,
            PRIMARY KEY (path, inner_name)
        );
    """)
    conn.commit()


def _cache_conn() -> sqlite3.Connection:
    """One connection per worker thread (sqlite3 connections aren't
    thread-safe); WAL mode lets concurrent readers/writers from the shared
    thread pool coexist without 'database is locked' errors."""
    conn = getattr(_cache_local, "conn", None)
    if conn is None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(CACHE_DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _init_cache_schema(conn)
        _cache_local.conn = conn
    return conn


def get_cached_hashes(path: Path, hash_type: str, mtime: float, size: int) -> list[tuple[str, str]] | None:
    conn = _cache_conn()
    row = conn.execute(
        "SELECT mtime, size, hashes_json FROM file_hashes WHERE path = ? AND hash_type = ?",
        (str(path), hash_type),
    ).fetchone()
    if row and row[0] == mtime and row[1] == size:
        return [tuple(pair) for pair in json.loads(row[2])]
    return None


def set_cached_hashes(path: Path, hash_type: str, mtime: float, size: int, hashes: list[tuple[str, str]]) -> None:
    conn = _cache_conn()
    conn.execute(
        "INSERT OR REPLACE INTO file_hashes (path, hash_type, mtime, size, hashes_json, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(path), hash_type, mtime, size, json.dumps(hashes), datetime.now().timestamp()),
    )
    conn.commit()


def compute_verify_signature(rom_files: list[Path], dat_files: list[Path]) -> str:
    """Cheap fingerprint of a folder's ROM files + its mapped DAT files, so
    the verify-results cache invalidates correctly on either a ROM change
    or a DAT re-map/update. One extra stat() per file — trivial next to the
    hash/CHD-extract work this is meant to let us skip."""
    parts: list[str] = []
    for f in sorted(rom_files) + sorted(dat_files):
        try:
            st = f.stat()
            parts.append(f"{f.name}|{st.st_size}|{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{f.name}|?|?")
    joined = "\n".join(parts)
    checksum = zlib.crc32(joined.encode("utf-8", "surrogateescape")) & 0xFFFFFFFF
    return f"{checksum:08x}"


def get_cached_verify_result(rompath: str, datroot: str, folder: str, hash_type: str, signature: str) -> dict | None:
    conn = _cache_conn()
    row = conn.execute(
        "SELECT signature, result_json FROM verify_results "
        "WHERE rompath = ? AND datroot = ? AND folder = ? AND hash_type = ?",
        (rompath, datroot, folder, hash_type),
    ).fetchone()
    if row and row[0] == signature:
        return json.loads(row[1])
    return None


def set_cached_verify_result(rompath: str, datroot: str, folder: str, hash_type: str, signature: str, result: dict) -> None:
    conn = _cache_conn()
    conn.execute(
        "INSERT OR REPLACE INTO verify_results "
        "(rompath, datroot, folder, hash_type, signature, result_json, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (rompath, datroot, folder, hash_type, signature, json.dumps(result), datetime.now().timestamp()),
    )
    conn.commit()


def clear_verify_results_for(rompath: str, datroot: str) -> int:
    """Delete every verify_results row for this rompath+datroot (every
    folder/hash_type under this DAT Scanner scan). Scoped to verify_results
    only - leaves file_hashes/simple_hashes untouched, since those aren't
    specific to a DAT verify scan. Used by the ROM Scanner Verify page's
    'Clear cached results' button, which used to only clear the client-side
    localStorage replay copy (a leftover from before this SQLite cache
    existed) - leaving the real cache untouched, so a "cleared" folder
    would still silently replay a stale server-side result on the next
    scan instead of rehashing."""
    conn = _cache_conn()
    cur = conn.execute(
        "DELETE FROM verify_results WHERE rompath = ? AND datroot = ?",
        (rompath, datroot),
    )
    conn.commit()
    return cur.rowcount


def get_cached_simple(path: Path, inner_name: str, mtime: float, size: int) -> dict | None:
    conn = _cache_conn()
    row = conn.execute(
        "SELECT mtime, size, crc, md5, sha1 FROM simple_hashes WHERE path = ? AND inner_name = ?",
        (str(path), inner_name),
    ).fetchone()
    if row and row[0] == mtime and row[1] == size:
        return {"crc": row[2], "md5": row[3], "sha1": row[4]}
    return None


def set_cached_simple(path: Path, inner_name: str, mtime: float, size: int,
                       crc: str | None = None, md5: str | None = None, sha1: str | None = None) -> None:
    """Upsert, only overwriting columns actually computed this call - a
    later request that also wants sha1 backfills that column instead of
    losing a previously-cached crc/md5."""
    conn = _cache_conn()
    existing = conn.execute(
        "SELECT crc, md5, sha1 FROM simple_hashes WHERE path = ? AND inner_name = ? AND mtime = ? AND size = ?",
        (str(path), inner_name, mtime, size),
    ).fetchone()
    if existing:
        crc  = crc  if crc  is not None else existing[0]
        md5  = md5  if md5  is not None else existing[1]
        sha1 = sha1 if sha1 is not None else existing[2]
    conn.execute(
        "INSERT OR REPLACE INTO simple_hashes (path, inner_name, mtime, size, crc, md5, sha1, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(path), inner_name, mtime, size, crc, md5, sha1, datetime.now().timestamp()),
    )
    conn.commit()


def clear_cache_table(table: str) -> int:
    """Delete every row from one cache table (table name is always one of
    our own hardcoded constants at the call site, never user input)."""
    conn = _cache_conn()
    cur = conn.execute(f"DELETE FROM {table}")
    conn.commit()
    return cur.rowcount


def clear_cache() -> dict:
    conn = _cache_conn()
    counts = {}
    for table in ("file_hashes", "verify_results", "simple_hashes"):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.execute("VACUUM")
    return counts


def cache_stats() -> dict:
    conn = _cache_conn()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("file_hashes", "verify_results", "simple_hashes")}
    counts["db_size_bytes"] = CACHE_DB_PATH.stat().st_size if CACHE_DB_PATH.exists() else 0
    return counts


def parse_dat_header(filepath: Path) -> dict:
    """Parse just the header of a DAT file quickly."""
    try:
        root = _parse_dat_cached(filepath)
        header = root.find("header")
        if header is None:
            return {"error": "No header found"}

        fmt = detect_dat_format(header)
        games = find_game_elements(root)

        # Count categories
        categories: dict[str, int] = {}
        verified = 0
        clones   = 0
        for g in games:
            cat = g.findtext("category", "Games")
            categories[cat] = categories.get(cat, 0) + 1
            if is_clone_element(g):
                clones += 1
            for rom in g.findall("rom"):
                if rom.get("status") == "verified":
                    verified += 1

        # Redump: version is a date string, No-Intro uses a version stamp
        version = header.findtext("version", "") or header.findtext("date", "")

        return {
            "filename":    filepath.name,
            "filepath":    str(filepath),
            "format":      fmt,
            "id":          header.findtext("id", ""),
            "name":        header.findtext("name", filepath.stem),
            "description": header.findtext("description", ""),
            "version":     version,
            "homepage":    header.findtext("homepage", ""),
            "url":         header.findtext("url", ""),
            "total_games": len(games),
            "clones":      clones,
            "unique":      len(games) - clones,
            "verified":    verified,
            "categories":  categories,
            "multi_rom":   fmt == "redump",  # flag for UI to show disc note
        }
    except ET.ParseError as e:
        return {"error": str(e), "filename": filepath.name, "filepath": str(filepath)}


def load_dat_mapping(datroot: str) -> dict[str, list[str]]:
    """Load folder->[dat filenames] mapping from mapping.json.
    Returns empty dict if file doesn't exist yet."""
    mapping_path = Path(datroot) / "mapping.json"
    if not mapping_path.exists():
        return {}
    try:
        with open(mapping_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_dat_mapping(datroot: str, mapping: dict) -> None:
    """Save folder->[dat filenames] mapping to mapping.json."""
    mapping_path = Path(datroot) / "mapping.json"
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)


# App-level settings (currently just chdman's path). Separate from
# mapping.json, which is DAT-root-specific - this is a single project-wide
# config, so it lives next to romtools.py itself rather than under a
# user-chosen DatRoot.
CONFIG_PATH = Path(__file__).parent / "config.json"


def load_app_config() -> dict:
    """Load app-level settings from config.json. Returns empty dict if the
    file doesn't exist yet or is corrupt."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_app_config(config: dict) -> None:
    """Save app-level settings to config.json."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_chdman_path() -> Path | None:
    """Return the configured chdman.exe path if set and it exists, else
    the default project-relative location (chdman/chdman.exe next to this
    script) if that exists, else None. chdman (MAME's CD-image tool) is
    used to extract real track data from .chd files for hash verification
    against Redump DATs - hashing a .chd's own compressed bytes can never
    match, since Redump hashes are computed from the original uncompressed
    CUE/BIN data."""
    configured = load_app_config().get("chdman_path", "")
    if configured:
        p = Path(configured)
        if p.is_file():
            return p
    default = Path(__file__).parent / "chdman" / "chdman.exe"
    if default.is_file():
        return default
    return None


# Keyword map for auto-matching DAT names to ROM folder names
# Format: folder_name: [keywords that might appear in DAT name]
FOLDER_KEYWORDS: dict[str, list[str]] = {
    # Nintendo
    "snes":         ["super nintendo", "snes", "super famicom"],
    "nes":          ["nintendo entertainment system", " nes ", "famicom"],
    "gb":           ["__regex__:game boy(?!\\s+(color|colour|advance|pocket|light|micro))"],
    "gameboy":      ["game boy - original"],
    "gbc":          ["game boy color"],
    "gba":          ["game boy advance"],
    "n64":          ["nintendo 64", "n64"],
    "nds":          ["nintendo ds", " nds "],
    "3ds":          ["nintendo 3ds"],
    "gamecube":     ["nintendo - gamecube", "nintendo - game cube", "gamecube", "game cube"],
    "wii":          ["nintendo - wii"],
    "wiiu":         ["wii u"],
    "switch":       ["nintendo switch"],
    # Sega
    "megadrive":    ["mega drive", "genesis", "megadrive"],
    "mastersystem": ["master system", "sega mark"],
    "gamegear":     ["game gear"],
    "saturn":       ["sega - saturn", " saturn"],
    "dreamcast":    ["sega - dreamcast", "dreamcast"],
    "megacd":       ["mega cd", "sega cd", "mega-cd"],
    "segacd":       ["mega cd", "sega cd"],
    # Sony
    "psx":          ["__regex__:sony - playstation(?!\\s+(2|3|portable))"],
    "ps2":          ["sony - playstation 2", "playstation 2"],
    "ps3":          ["sony - playstation 3", "playstation 3"],
    "psp":          ["sony - playstation portable"],
    # Atari
    "atari2600":    ["atari - 2600", "atari 2600"],
    "atari7800":    ["atari - 7800", "atari 7800"],
    "lynx":         ["atari - lynx", "atari lynx"],
    "jaguar":       ["atari - jaguar"],
    # NEC
    "pcengine":     ["__regex__:nec - pc engine(?!\\s+cd)", "__regex__:turbografx-16(?!\\s+cd)"],
    "pcenginecd":   ["nec - pc engine cd", "turbografx cd", "pc engine cd &"],
    "pcfx":         ["nec - pc-fx", "pc-fx"],
    # SNK
    "neogeo":       ["__regex__:neo.geo(?!\\s+cd)", "__regex__:snk - neo.geo(?!\\s+cd)"],
    "neogeocd":     ["neo geo cd", "snk - neo geo cd", "neo-geo cd"],
    # Home computers
    "msx":          ["microsoft - msx"],
    "amiga":        ["commodore - amiga"],
    "c64":          ["commodore 64", "commodore - 64"],
    "dos":          ["- dos (", "ms-dos"],
    "zxspectrum":   ["zx spectrum", "sinclair - zx"],
    "colecovision": ["colecovision", "coleco - coleco"],
    # Other
    "3do":          ["3do interactive", "panasonic - 3do"],
    "mame":         ["arcade", "mame"],
}


def auto_match_dat(dat_name: str) -> list[str]:
    """Given a DAT name string, return list of likely folder names.
    Keywords prefixed with __regex__: are treated as regular expressions."""
    name_lower = dat_name.lower()
    matches = []
    for folder, keywords in FOLDER_KEYWORDS.items():
        for kw in keywords:
            if kw.startswith("__regex__:"):
                pattern = kw[len("__regex__:"):]
                if re.search(pattern, name_lower):
                    matches.append(folder)
                    break
            elif kw in name_lower:
                matches.append(folder)
                break
    return matches


# ---------------------------------------------------------------------------
# Routes - DAT Manager
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers - DAT extension checking
# ---------------------------------------------------------------------------

ARCHIVE_EXTS = {".zip", ".7z", ".rar"}


def detect_chd_track_format(chd_path: Path) -> str | None:
    """Peek at a CHD v5 file's own header + metadata (no chdman needed -
    just reading the file's binary header) to determine whether it's a
    standard CD-track image or a GD-ROM image.

    Redump-format DATs (Saturn/PSX/Sega CD/etc.) hash the original
    uncompressed CUE/BIN track data, and chdman's `extractcd` reliably
    reconstructs that from a CHD carrying the `CHT2`/`CHTR` per-track
    metadata tags - verified against real files. Dreamcast's GD-ROM CHDs
    use a different tag (`CHGD`) for their dual-density disc layout, and
    the same extraction does NOT reliably reproduce Redump-matching bytes
    for those (confirmed empirically: ~7% track match rate on real
    samples, vs 100% for CHT2-tagged files) - so this is used as a gate to
    only offer CHD verification where it's actually known to work.

    Returns "cdrom" (CHT2/CHTR - verifiable), "gdrom" (CHGD - not
    supported), or None (not a recognized/parseable CHD)."""
    try:
        with open(chd_path, "rb") as f:
            if f.read(8) != b"MComprHD":
                return None
            header_len_and_version = f.read(8)
            version = struct.unpack(">I", header_len_and_version[4:8])[0]
            if version != 5:
                return None  # only v5 header layout is handled here
            f.seek(48)  # metaoffset field's byte offset within the v5 header
            metaoffset = struct.unpack(">Q", f.read(8))[0]

            offset = metaoffset
            for _ in range(20):  # a handful of entries is enough to find the first track tag
                if offset == 0:
                    break
                f.seek(offset)
                entry_header = f.read(16)
                if len(entry_header) < 16:
                    break
                tag = entry_header[0:4]
                next_offset = struct.unpack(">Q", entry_header[8:16])[0]
                if tag in (b"CHT2", b"CHTR"):
                    return "cdrom"
                if tag == b"CHGD":
                    return "gdrom"
                offset = next_offset
    except (OSError, struct.error):
        pass
    return None


def sample_chd_verifiable(folder_path: Path, sample: int = 3) -> bool | None:
    """Peek at up to `sample` .chd files in a folder to decide whether the
    folder's CHDs are verifiable against a Redump DAT. Checked files are
    assumed to share one format within a system folder (true in practice -
    a Saturn folder won't mix CD and GD-ROM images).
    Returns True (verifiable), False (known-unsupported format), or None
    (no .chd files present, or format couldn't be determined)."""
    count = 0
    for f in folder_path.glob("*.chd"):
        fmt = detect_chd_track_format(f)
        if fmt == "cdrom":
            return True
        if fmt == "gdrom":
            return False
        count += 1
        if count >= sample:
            break
    return None


def get_dat_extensions(dat_path: Path) -> dict[str, int]:
    """Return {ext: count} of ROM filename extensions in a DAT file."""
    exts: dict[str, int] = {}
    try:
        root = _parse_dat_cached(dat_path)
        for game in find_game_elements(root):
            for rom in game.findall("rom"):
                ext = Path(rom.get("name", "")).suffix.lower()
                if ext:
                    exts[ext] = exts.get(ext, 0) + 1
    except ET.ParseError:
        pass
    return exts


def get_folder_extensions(folder_path: Path, sample: int = 200) -> dict[str, int]:
    """Return {ext: count} of file extensions in a ROM folder (sampled)."""
    exts: dict[str, int] = {}
    count = 0
    try:
        for f in list_rom_candidate_files(folder_path):
            ext = f.suffix.lower()
            exts[ext] = exts.get(ext, 0) + 1
            count += 1
            if count >= sample:
                break
    except OSError:
        pass
    return exts


def check_extension_match(dat_exts: dict[str, int],
                           folder_exts: dict[str, int],
                           chd_verifiable: bool | None = None) -> dict:
    """Compare DAT extensions vs folder extensions.
    Archives (.zip/.7z) in folder are treated as compatible with any DAT ext.
    chd_verifiable (see sample_chd_verifiable()): when the folder has .chd
    files, True treats them as compatible the same way archives are
    (chdman can extract Redump-matching track data from them); False
    means they're a recognized-but-currently-unsupported format (GD-ROM)
    and stays a mismatch, with a clearer note than the generic
    "DAT expects X but folder has Y"; None means no .chd present.
    """
    dat_set    = set(dat_exts.keys())
    folder_set = set(folder_exts.keys())

    has_archives = bool(folder_set & ARCHIVE_EXTS)
    has_chd      = ".chd" in folder_set
    chd_ok       = has_chd and chd_verifiable is True
    direct_match = bool(dat_set & folder_set)
    match        = direct_match or has_archives or chd_ok

    if chd_ok and not direct_match:
        note = "Folder contains CHD - verifiable via chdman"
    elif has_chd and chd_verifiable is False and not direct_match:
        note = "Folder contains CHD, but its internal format (GD-ROM) isn't supported for verification yet"
    elif has_archives and not direct_match:
        note = "Folder contains archives (.zip/.7z) - likely compatible"
    elif not match:
        d_exts = ", ".join(sorted(dat_set)[:3])
        f_exts = ", ".join(sorted(folder_set - {".xml", ".txt"})[:3])
        note = f"DAT expects {d_exts} but folder has {f_exts}"
    else:
        note = ""

    return {
        "match":          match,
        "has_archives":   has_archives,
        "has_chd":        has_chd,
        "chd_verifiable": chd_verifiable,
        "dat_exts":    sorted(dat_exts.items(), key=lambda x: -x[1])[:5],
        "folder_exts": sorted(folder_exts.items(), key=lambda x: -x[1])[:5],
        "note":         note,
    }


# ---------------------------------------------------------------------------
def _build_dat_catalogue(dat_files: list[Path]) -> list[dict]:
    """Parse every DAT file's header + extension list. Runs in the thread
    pool - DAT files can be tens of MB (MAME's is ~80MB) and parsing them
    synchronously on the event loop would freeze the whole server."""
    catalogue = []
    for dat_path in dat_files:
        info = parse_dat_header(dat_path)
        info["suggested_folders"] = auto_match_dat(info.get("name", dat_path.stem))
        info["dat_exts"] = sorted(get_dat_extensions(dat_path).items(), key=lambda x: -x[1])[:5]
        catalogue.append(info)
    return catalogue


@app.get("/api/dat/scan-dats")
async def scan_dats(datroot: str = Query(...)):
    """Scan DatRoot folder and return catalogue of all DAT files."""
    root = Path(datroot)
    if not root.is_dir():
        return JSONResponse({"error": f"Not a directory: {datroot}"}, status_code=400)

    dat_files = sorted(root.glob("*.dat"))
    if not dat_files:
        return JSONResponse({"error": f"No .dat files found in {datroot}"}, status_code=404)

    loop = asyncio.get_event_loop()
    catalogue = await loop.run_in_executor(_thread_pool, _build_dat_catalogue, dat_files)

    return JSONResponse({
        "datroot":   datroot,
        "total":     len(catalogue),
        "catalogue": catalogue,
    })


def _build_scan_overview(rompath: str, datroot: str, folder_excl: list[str]) -> dict:
    """Compare ROM folders against DAT mapping, auto-matching unmapped
    folders and saving results to mapping.json. Runs in the thread pool -
    parses every DAT file's header plus per-folder extension checks (DAT
    files can be tens of MB), which would otherwise block the event loop
    for the whole server, not just this request."""
    rom_root = Path(rompath)
    dat_root = Path(datroot)

    # Load existing saved mapping
    mapping = load_dat_mapping(datroot)

    # Scan all DAT files
    dat_catalogue: dict[str, dict] = {}
    for dat_path in sorted(dat_root.glob("*.dat")):
        info = parse_dat_header(dat_path)
        dat_catalogue[dat_path.name] = info

    # Build reverse lookup: folder -> [dat filenames] from auto-match
    auto_matched: dict[str, list[str]] = {}
    for dat_filename, info in dat_catalogue.items():
        folders = auto_match_dat(info.get("name", ""))
        for folder in folders:
            auto_matched.setdefault(folder, []).append(dat_filename)

    # Scan ROM folders
    mapping_updated = False
    systems = []
    try:
        with os.scandir(rom_root) as it:
            top_entries = sorted(it, key=lambda e: e.name)
    except OSError:
        top_entries = []
    for item in top_entries:
        if not item.is_dir() or item.name in folder_excl:
            continue

        # Count ROMs
        rom_files = list_rom_candidate_files(Path(item.path))

        # If not in saved mapping, apply auto-match and save it
        if item.name not in mapping and item.name in auto_matched:
            mapping[item.name] = auto_matched[item.name]
            mapping_updated = True

        mapped_dats  = mapping.get(item.name, [])
        mapped_info  = [dat_catalogue[d] for d in mapped_dats if d in dat_catalogue]

        # Extension compatibility check per mapped DAT
        ext_checks = []
        folder_path = Path(rompath) / item.name
        if mapped_dats and folder_path.is_dir():
            folder_exts = get_folder_extensions(folder_path)
            chd_verifiable = sample_chd_verifiable(folder_path) if ".chd" in folder_exts else None
            for dat_file in mapped_dats:
                dp = Path(datroot) / dat_file
                if dp.exists():
                    chk = check_extension_match(get_dat_extensions(dp), folder_exts, chd_verifiable)
                    chk["dat_file"] = dat_file
                    ext_checks.append(chk)

        systems.append({
            "folder":      item.name,
            "rom_count":   len(rom_files),
            "has_roms":    len(rom_files) > 0,
            "has_dat":     len(mapped_info) > 0,
            "mapped_dats": mapped_info,
            "auto_mapped": item.name in auto_matched,
            "ext_checks":  ext_checks,
        })

    # Persist any new auto-matches
    if mapping_updated:
        save_dat_mapping(datroot, mapping)

    return {
        "rompath":       rompath,
        "datroot":       datroot,
        "systems":       systems,
        "mapping":       mapping,
        "dat_catalogue": list(dat_catalogue.values()),
        "auto_saved":    mapping_updated,
    }


@app.post("/api/dat/scan-overview")
async def scan_overview(request: Request):
    """Compare ROM folders against DAT mapping.
    Auto-matches unmapped folders and saves results to mapping.json."""
    body        = await request.json()
    rompath     = body.get("rompath", "")
    datroot     = body.get("datroot", "")
    folder_excl = body.get("folder_excl", FOLDER_EXCL_BASE + FOLDER_EXCL_EXTRA)

    if not Path(rompath).is_dir():
        return JSONResponse({"error": f"ROM path not found: {rompath}"}, status_code=400)
    if not Path(datroot).is_dir():
        return JSONResponse({"error": f"DAT root not found: {datroot}"}, status_code=400)

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(_thread_pool, _build_scan_overview, rompath, datroot, folder_excl)
    return JSONResponse(result)


def _build_dat_view(p: Path) -> dict:
    """Parse a DAT file into a flat list of game-row dicts plus a truncated
    raw-XML preview. Runs in the thread pool - DAT files can be tens of MB
    (MAME's is ~80MB) with tens of thousands of games."""
    root = _parse_dat_cached(p)
    header = root.find("header")
    games  = []

    for game in find_game_elements(root):
        entry: dict = {}
        # Game-level attributes
        entry["name"]      = game.get("name", "")
        entry["cloneofid"] = game.get("cloneofid", "") or game.get("cloneof", "")

        # Game-level child elements
        for field in ("description","year","manufacturer","category","region","languages","sourcefile","serial"):
            el = game.find(field)
            if el is not None and el.text:
                entry[field] = el.text.strip()

        # ROM entries - flatten first ROM's attributes into the game row
        # For multi-ROM games (Redump), add one row per ROM
        roms = game.findall("rom")
        if not roms:
            games.append(entry)
        elif len(roms) == 1:
            rom = roms[0]
            entry["rom_name"] = rom.get("name", "")
            for attr in ("size","crc","md5","sha1","sha256","status","serial","header"):
                val = rom.get(attr, "")
                if val: entry[attr] = val
            games.append(entry)
        else:
            # Multi-ROM (Redump disc): one row per track
            for rom in roms:
                row = dict(entry)
                row["rom_name"] = rom.get("name", "")
                for attr in ("size","crc","md5","sha1","status"):
                    val = rom.get(attr, "")
                    if val: row[attr] = val
                games.append(row)

    # Raw XML preview truncated to 500KB - read only enough bytes to cover
    # that instead of the whole file (DAT files can be 80MB+).
    with open(p, "rb") as f:
        raw_bytes = f.read(1_000_000)
    raw_xml = raw_bytes.decode("utf-8", errors="replace")
    if len(raw_xml) > 500_000:
        raw_xml = raw_xml[:500_000] + "\n\n[... truncated ...]"

    return {
        "filepath": str(p),
        "name":     header.findtext("name", p.stem) if header else p.stem,
        "total":    len(games),
        "games":    games,
        "raw_xml":  raw_xml,
    }


@app.get("/api/dat/view-file")
async def view_dat_file(filepath: str = Query(...)):
    """Parse a DAT file and return all game entries as a flat list of dicts,
    plus the raw XML. Handles both No-Intro and Redump formats."""
    p = Path(filepath)
    if not p.exists() or not p.is_file():
        return JSONResponse({"error": f"File not found: {filepath}"}, status_code=404)

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(_thread_pool, _build_dat_view, p)
    except ET.ParseError as e:
        return JSONResponse({"error": f"XML parse error: {e}"}, status_code=400)

    return JSONResponse(result)


@app.post("/api/dat/save-mapping")
async def save_mapping(request: Request):
    """Save updated folder->DAT mapping."""
    body    = await request.json()
    datroot = body.get("datroot", "")
    mapping = body.get("mapping", {})

    if not Path(datroot).is_dir():
        return JSONResponse({"error": f"DAT root not found: {datroot}"}, status_code=400)

    try:
        save_dat_mapping(datroot, mapping)
        return JSONResponse({"status": "saved", "path": str(Path(datroot) / "mapping.json")})
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Routes - ROM Scanner: Step 1 quick folder scan
# ---------------------------------------------------------------------------

# Alias kept for readability at existing call sites.
FILE_EXCL_SCAN = ROM_FILE_EXCL_SET

def _count_rom_files_with_size(folder_path: Path) -> tuple[int, int]:
    """(count, total_bytes) of ROM candidate files directly in folder_path,
    in one os.scandir() pass so size comes from the same cached directory
    listing as the exclusion check - no second stat() round trip per file
    (matters a lot on a network share)."""
    count = 0
    total_size = 0
    try:
        with os.scandir(folder_path) as it:
            for entry in it:
                if entry.is_file() and not any(entry.name.lower().endswith(e) for e in ROM_FILE_EXCL_SET):
                    count += 1
                    total_size += entry.stat().st_size
    except OSError:
        pass
    return count, total_size


def _build_folder_scan(root: Path) -> list[dict]:
    """Runs in the thread pool - iterates every system folder and stats
    every ROM file in each, which is cheap locally but can take tens of
    seconds over a network share, and would otherwise block the event loop
    for the whole server, not just this request."""
    folders = []
    try:
        with os.scandir(root) as it:
            top_entries = sorted(it, key=lambda e: e.name)
    except OSError:
        top_entries = []
    for item in top_entries:
        if not item.is_dir() or item.name in (FOLDER_EXCL_BASE + FOLDER_EXCL_EXTRA):
            continue
        rom_count, total_size = _count_rom_files_with_size(Path(item.path))
        folders.append({
            "folder":     item.name,
            "rom_count":  rom_count,
            "size_bytes": total_size,
        })
    return folders


@app.get("/api/dat/scan-folders")
async def scan_folders(rompath: str = Query(...)):
    """Step 1 - Fast folder scan: count and size only, no hashing."""
    root = Path(rompath)
    if not root.is_dir():
        return JSONResponse({"error": f"Not a directory: {rompath}"}, status_code=400)

    loop = asyncio.get_event_loop()
    folders = await loop.run_in_executor(_thread_pool, _build_folder_scan, root)
    return JSONResponse({"rompath": rompath, "folders": folders})


def load_dat_descriptions(dat_paths: list[Path]) -> dict[str, str]:
    """Parse DAT files and return {game_name: description}.
    MAME/FBNeo <game name> is often a bare code ('1942abl'); <description>
    holds the full name ('1942 (Revision A, bootleg)') with region/version data.
    Keyed by name AND by description so already-full names also resolve."""
    out: dict[str, str] = {}
    for dat_path in dat_paths:
        try:
            root = _parse_dat_cached(dat_path)
        except ET.ParseError:
            continue
        for game in find_game_elements(root):
            name = game.get("name", "")
            desc = (game.findtext("description", "") or "").strip()
            if not desc:
                continue
            if name:
                out[name] = desc
            out.setdefault(desc, desc)
    return out


def load_dat_game_details(dat_paths: list[Path]) -> dict[str, dict]:
    """Parse DAT files and return {game_name: {description, category, size,
    crc, md5, sha1, rom_file, rom_count}} for the Verify page's Missing-list
    detail modal.
    size is the sum of every <rom size=...> in the game (meaningful for
    both single-rom No-Intro/MAME entries and multi-track Redump discs).
    crc/md5/sha1 are only populated when the game has exactly one <rom> -
    a multi-track Redump game (e.g. Dreamcast, one <rom> per CD track) has
    no single representative hash, so those three stay blank for it.
    rom_file is a single "primary" filename to represent the whole game in
    a one-row-per-game report even when it has many <rom> entries (e.g. a
    Redump disc's many CD tracks): prefers a .cue, then .iso/.m3u (the file
    an emulator/frontend actually loads), then falls back to the first
    <rom> in DAT order. rom_count is the total number of <rom> entries, so
    callers can show "+N more" for multi-file games."""
    out: dict[str, dict] = {}
    for dat_path in dat_paths:
        try:
            root = _parse_dat_cached(dat_path)
        except ET.ParseError:
            continue
        for game in find_game_elements(root):
            name = game.get("name", "")
            if not name:
                continue
            roms = game.findall("rom")
            total_size = 0
            has_size   = False
            for rom in roms:
                size_attr = rom.get("size", "")
                if size_attr:
                    try:
                        total_size += int(size_attr)
                        has_size = True
                    except ValueError:
                        pass
            single = roms[0] if len(roms) == 1 else None

            rom_file = ""
            for want_ext in (".cue", ".iso", ".m3u"):
                match = next((r for r in roms if r.get("name", "").lower().endswith(want_ext)), None)
                if match is not None:
                    rom_file = match.get("name", "")
                    break
            if not rom_file and roms:
                rom_file = roms[0].get("name", "")

            out[name] = {
                "description": (game.findtext("description", "") or "").strip(),
                "category":    (game.findtext("category", "") or "").strip(),
                "size":        total_size if has_size else None,
                "crc":         single.get("crc", "") if single is not None else "",
                "md5":         single.get("md5", "") if single is not None else "",
                "sha1":        single.get("sha1", "") if single is not None else "",
                "rom_file":    rom_file,
                "rom_count":   len(roms),
            }
    return out


# Generic BIOS/device parent names that are NOT real game parents — a
# cloneof/romof pointing at one of these must be ignored for grouping, or
# unrelated games sharing a BIOS (e.g. all neogeo games -> "neogeo") would be
# wrongly merged into one duplicate group.
GENERIC_PARENTS = {
    "neogeo", "neogeo1", "neogeocd",
}


def load_dat_clone_info(dat_paths: list[Path]) -> dict[str, dict]:
    """Parse DAT files and return per-game clone metadata keyed by game name:
        { name: {"cloneof": str, "romof": str} }
    A cloneof/romof pointing at a generic BIOS/device (GENERIC_PARENTS) is
    blanked so it won't be used to group games."""
    out: dict[str, dict] = {}
    for dat_path in dat_paths:
        try:
            root = _parse_dat_cached(dat_path)
        except ET.ParseError:
            continue
        for game in find_game_elements(root):
            name = game.get("name", "")
            if not name:
                continue
            cloneof = game.get("cloneof", "") or game.get("cloneofid", "")
            romof   = game.get("romof", "")
            if cloneof in GENERIC_PARENTS:
                cloneof = ""
            if romof in GENERIC_PARENTS:
                romof = ""
            out[name] = {"cloneof": cloneof, "romof": romof}
    return out


@app.post("/api/dat/descriptions")
async def dat_descriptions(
    rompath: str = Form(...),
    datroot: str = Form(...),
    folder:  str = Form(default=""),
):
    """Return {game_name: description} + clone_info for the DAT(s) mapped to a
    folder, with all-DATs and recursive-scan fallbacks."""
    mapping  = load_dat_mapping(datroot)
    dat_root = Path(datroot)
    if not dat_root.is_dir():
        return JSONResponse({"descriptions": {}, "clone_info": {}, "count": 0, "note": "datroot not found"})

    dat_names: list[str] = list(mapping.get(folder, [])) if folder else []
    if not dat_names:
        for v in mapping.values():
            dat_names.extend(v)

    seen, dat_files = set(), []
    for d in dat_names:
        if d in seen:
            continue
        seen.add(d)
        p = dat_root / d
        if p.exists():
            dat_files.append(p)
    if not dat_files:
        # Top-level DatRoot only - matches every other DAT-scanning route
        # (scan_dats, scan_overview). A subfolder like DatRoot\Archives
        # typically holds not-yet-extracted .zip downloads, not usable
        # DATs, and shouldn't be silently swept in as a fallback.
        for p in sorted(dat_root.glob("*")):
            if p.suffix.lower() in (".dat", ".xml") and p.is_file():
                dat_files.append(p)

    if not dat_files:
        return JSONResponse({"descriptions": {}, "clone_info": {}, "count": 0, "note": "no DAT files found"})

    loop = asyncio.get_event_loop()
    descriptions = await loop.run_in_executor(_thread_pool, load_dat_descriptions, dat_files)
    clone_info   = await loop.run_in_executor(_thread_pool, load_dat_clone_info, dat_files)
    return JSONResponse({
        "descriptions": descriptions,
        "clone_info":   clone_info,
        "count":        len(descriptions),
        "dat_count":    len(dat_files),
    })



def load_dat_hashes(dat_paths: list[Path], hash_type: str) -> tuple[dict, list[str], str, dict, dict, dict]:
    """Parse DAT files and return:
    - hash_lookup:      {hash_value: game_name}         for fast O(1) matching
    - all_game_names:   [name, ...]                     for missing-from-collection report
    - fmt:              'nointro' | 'redump' | 'unknown'
    - rom_name_lookup:  {hash_value: canonical_rom_name} for rename checking
    - game_hashes:      {game_name: set(hashes)}         for Redump full-game matching
    - baddump_hashes:   {game_name: set(hashes)}         hashes flagged status="baddump",
                         scoped per game - the same CRC is commonly a good
                         dump for one machine/clone and a flagged baddump for
                         another (MAME reuses identical chip ROMs across many
                         machines), so this must never be checked as one flat
                         set across the whole DAT.
    """
    hash_lookup:     dict[str, str]       = {}
    all_game_names:  list[str]            = []
    rom_name_lookup: dict[str, str]       = {}
    game_hashes:     dict[str, set[str]]  = {}
    baddump_hashes:  dict[str, set[str]]  = {}
    fmt = "unknown"

    attr = hash_type

    for dat_path in dat_paths:
        try:
            root = _parse_dat_cached(dat_path)
        except ET.ParseError:
            continue

        header = root.find("header")
        if header is not None:
            fmt = detect_dat_format(header)

        for game in find_game_elements(root):
            name = game.get("name", "")
            all_game_names.append(name)
            roms = game.findall("rom")
            g_hashes: set[str] = set()

            for rom in roms:
                val      = rom.get(attr, "").lower()
                rom_name = rom.get("name", "")
                if val:
                    hash_lookup[val]     = name
                    rom_name_lookup[val] = rom_name
                    # Redump DATs list a .cue entry alongside each disc's
                    # .bin/.chd track(s), but nothing on the verify side ever
                    # produces a hash for it (CHD extraction only yields
                    # track .bin data; the .cue chdman regenerates is never
                    # hashed). Leaving the .cue hash in g_hashes made the
                    # full-set match (g_hashes.issubset(file_hash_set))
                    # permanently unreachable, and forced every game onto the
                    # len(file_hash_set) > 1 fallback below - which silently
                    # excluded every single-track game (one .bin, no cue
                    # counterpart) even when its hash was perfectly correct.
                    if not rom_name.lower().endswith(".cue"):
                        g_hashes.add(val)
                    # No-Intro/MAME mark known-bad dumps with status="baddump".
                    # A collection file whose CRC matches one of these for the
                    # SAME game IS a bad dump and can be flagged for deletion.
                    # Scoped per game_name, not a flat set: MAME reuses the
                    # identical chip-ROM CRC across many machines, and it's
                    # routinely baddump for one and a normal good dump for
                    # another - a flat global set would wrongly flag every
                    # game that happens to share that CRC.
                    if (rom.get("status", "") or "").lower() == "baddump":
                        baddump_hashes.setdefault(name, set()).add(val)

            if g_hashes:
                game_hashes[name] = g_hashes

    return hash_lookup, all_game_names, fmt, rom_name_lookup, game_hashes, baddump_hashes


def hash_file_raw(filepath: Path, hash_type: str) -> str | None:
    """Hash a file directly from disk."""
    try:
        if hash_type == "crc":
            crc = 0
            with open(filepath, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    crc = zlib.crc32(block, crc)
            return f"{crc & 0xFFFFFFFF:08x}"
        elif hash_type == "md5":
            return md5_of_file(filepath)
        elif hash_type == "sha1":
            h = hashlib.sha1()
            with open(filepath, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            return h.hexdigest()
    except OSError:
        return None


def hash_data(data: bytes, hash_type: str) -> str:
    """Hash raw bytes with the given algorithm."""
    if hash_type == "crc":
        return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
    elif hash_type == "md5":
        return hashlib.md5(data).hexdigest()
    else:
        return hashlib.sha1(data).hexdigest()


def read_archive_entries(filepath: Path) -> dict[str, bytes]:
    """Return {inner_filename: raw_bytes} for every non-directory entry in a
    .zip or .7z archive (empty dict for any other extension). Shared by
    hash_rom_file() and the MD5 Scan route's inner-file mode so the "how do
    I open a zip vs 7z and iterate its entries" logic - which differs
    enough between the two archive libraries to be easy to get subtly
    wrong twice - lives in exactly one place. Errors are swallowed the
    same way both original call sites did: an unreadable/corrupt archive
    just yields no entries rather than raising."""
    entries: dict[str, bytes] = {}
    ext = filepath.suffix.lower()
    try:
        if ext == ".zip":
            with zipfile.ZipFile(filepath, "r") as zf:
                for info in zf.infolist():
                    if info.filename.endswith("/"):
                        continue
                    entries[info.filename] = zf.read(info.filename)
        elif ext == ".7z":
            with py7zr.SevenZipFile(filepath, mode="r") as sz:
                for name, bio in (sz.readall() or {}).items():
                    entries[name] = bio.read()
    except (OSError, zipfile.BadZipFile, py7zr.Bad7zFile):
        pass
    return entries


def hash_chd_file(filepath: Path, hash_type: str) -> list[tuple[str, str]]:
    """Return [(track_filename, hash), ...] for a .chd's underlying CD
    track data, by extracting it via chdman (external tool - MAME's own
    CD-image tool, see get_chdman_path()) and hashing each resulting
    track. This is the only way to get Redump-matching hashes out of a
    CHD: the compressed .chd bytes themselves never match, since Redump
    hashes are computed from the original uncompressed CUE/BIN data.

    Returns [] (no hashes, treated by callers the same as any unreadable
    file) if chdman isn't configured/found, or the CHD isn't the
    "cdrom"-format detect_chd_track_format() recognizes as verifiable
    (GD-ROM CHDs don't currently extract to Redump-matching data - see
    that function's docstring). This double-checks what the ext-mismatch
    gate in check_extension_match() already keeps out of the normal UI
    flow, so a direct API call can't waste time on a doomed extraction."""
    chdman_path = get_chdman_path()
    if not chdman_path:
        return []
    if detect_chd_track_format(filepath) != "cdrom":
        return []

    with tempfile.TemporaryDirectory(prefix="romtools_chd_") as tmp:
        tmp_path = Path(tmp)
        cue_path = tmp_path / "track.cue"
        bin_path = tmp_path / "track%t.bin"
        try:
            proc = subprocess.run(
                [str(chdman_path), "extractcd", "-i", str(filepath),
                 "-o", str(cue_path), "-ob", str(bin_path), "-sb", "-f"],
                capture_output=True, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if proc.returncode != 0:
            return []

        results: list[tuple[str, str]] = []
        for bf in sorted(tmp_path.glob("*.bin")):
            data = bf.read_bytes()
            results.append((bf.name, hash_data(data, hash_type)))
        return results


def hash_rom_file(filepath: Path, hash_type: str) -> list[tuple[str, str]]:
    """Return list of (filename, hash) tuples for a ROM file.
    - ZIP:  hashes each file inside
    - 7Z:   hashes each file inside using py7zr
    - CHD:  extracts CD track data via chdman and hashes each track
    - CUE/BIN: hashes directly (Redump disc images)
    - Everything else: single hash of the file
    """
    try:
        st = filepath.stat()
    except OSError:
        st = None

    if st is not None:
        cached = get_cached_hashes(filepath, hash_type, st.st_mtime, st.st_size)
        if cached is not None:
            return cached

    ext = filepath.suffix.lower()
    results: list[tuple[str, str]] = []
    if ext in (".zip", ".7z"):
        for name, data in read_archive_entries(filepath).items():
            results.append((name, hash_data(data, hash_type)))
    elif ext == ".chd":
        results = hash_chd_file(filepath, hash_type)
    else:
        h = hash_file_raw(filepath, hash_type)
        if h:
            results.append((filepath.name, h))

    # For Redump disc images: prioritise .bin tracks over .cue
    results.sort(key=lambda x: (0 if x[0].lower().endswith('.bin') else 1))

    if st is not None and results:
        set_cached_hashes(filepath, hash_type, st.st_mtime, st.st_size, results)

    return results



# ---------------------------------------------------------------------------
# Routes - ROM Scanner: Step 3 deep verify (SSE)
# ---------------------------------------------------------------------------

@app.post("/api/dat/verify-folders")
async def verify_folders(
    rompath:   str       = Form(...),
    datroot:   str       = Form(...),
    hash_type: str       = Form(default="crc"),
    folders:   list[str] = Form(default=[]),
):
    """Step 3 - Deep verify: hash each ROM and compare against DAT."""

    async def generate() -> AsyncGenerator[str, None]:
        mapping  = load_dat_mapping(datroot)
        dat_root = Path(datroot)
        rom_root = Path(rompath)

        loop = asyncio.get_event_loop()
        for folder in folders:
            yield sse_event({"type": "folder_start", "folder": folder})

            # Load DAT hashes for this folder
            dat_files = [dat_root / d for d in mapping.get(folder, [])
                         if (dat_root / d).exists()]
            if not dat_files:
                yield sse_event({
                    "type":    "error",
                    "message": f"{folder}: no DAT files found in mapping"
                })
                continue

            hash_lookup, all_names, dat_fmt, rom_name_lookup, game_hashes, baddump_hashes = await loop.run_in_executor(
                _thread_pool, load_dat_hashes, dat_files, hash_type
            )
            name_desc = await loop.run_in_executor(
                _thread_pool, load_dat_descriptions, dat_files
            )
            game_details = await loop.run_in_executor(
                _thread_pool, load_dat_game_details, dat_files
            )
            clone_info = await loop.run_in_executor(
                _thread_pool, load_dat_clone_info, dat_files
            )
            matched_names: set[str] = set()
            wrong_name_list: list[dict] = []   # files with correct CRC but wrong filename

            folder_path = rom_root / folder
            if not folder_path.is_dir():
                yield sse_event({
                    "type":    "error",
                    "message": f"{folder}: folder not found at {folder_path}"
                })
                continue

            rom_files = sorted(list_rom_candidate_files(folder_path))

            signature = await loop.run_in_executor(
                _thread_pool, compute_verify_signature, rom_files, dat_files
            )
            cached_result = get_cached_verify_result(rompath, datroot, folder, hash_type, signature)
            if cached_result is not None:
                yield sse_event({**cached_result, "from_cache": True})
                continue

            # Progress stride: every file for small/slow folders (e.g. a
            # CHD-heavy folder where each file takes several seconds to
            # extract+hash) so the bar doesn't sit still for minutes, but
            # capped to ~100 events per folder for huge sets (a full MAME
            # folder can have 25,000+ near-instantly-hashed entries, where
            # per-file SSE+DOM updates would be needless overhead).
            progress_stride = max(1, len(rom_files) // 100)

            verified     = 0
            unknown      = 0
            unknown_list: list[dict] = []
            baddump_list: list[dict] = []   # files matching a status="baddump" DAT entry

            for rom_file in rom_files:
                # Run in thread pool — file hashing is CPU/IO bound and would block event loop
                hashes = await loop.run_in_executor(
                    _thread_pool, hash_rom_file, rom_file, hash_type
                )
                if not hashes:
                    unknown += 1
                    unknown_list.append({"file": rom_file.name, "hash": "read error"})
                    await asyncio.sleep(0)
                    continue

                file_matched = False
                matched_game_name: str | None = None
                file_hash_set = {h for _, h in hashes}

                if dat_fmt == "redump":
                    # For Redump: match only if ALL tracks in the zip match a single game
                    # This avoids false positives from shared audio tracks
                    for game_name, g_hashes in game_hashes.items():
                        if g_hashes and g_hashes.issubset(file_hash_set):
                            verified += 1
                            matched_names.add(game_name)
                            file_matched = True
                            matched_game_name = game_name
                            break
                        # Also accept if file hashes are a subset of game hashes
                        # (some tracks may be omitted in compressed sets)
                        elif file_hash_set and file_hash_set.issubset(g_hashes) and len(file_hash_set) > 1:
                            verified += 1
                            matched_names.add(game_name)
                            file_matched = True
                            matched_game_name = game_name
                            break
                else:
                    # No-Intro / MAME / FBNeo. Multi-chip zips use Jaccard
                    # full-set matching; single-inner No-Intro uses first-CRC-wins.
                    file_hash_set = {h for _, h in hashes}

                    if len(hashes) > 1 and game_hashes:
                        best_game_name = None
                        best_score     = -1.0
                        for gname, g_crcs in game_hashes.items():
                            if not g_crcs:
                                continue
                            overlap = len(file_hash_set & g_crcs)
                            if overlap == 0:
                                continue
                            score = overlap / len(file_hash_set | g_crcs)
                            if score > best_score:
                                best_score     = score
                                best_game_name = gname
                        if best_game_name is not None:
                            verified += 1
                            matched_names.add(best_game_name)
                            file_matched = True
                            matched_game_name = best_game_name
                            actual_ext = rom_file.suffix.lower()
                            if actual_ext in (".zip", ".7z"):
                                expected = best_game_name + actual_ext
                                if rom_file.name != expected:
                                    rep_hash = next((h for _, h in hashes if h in hash_lookup), "")
                                    wrong_name_list.append({
                                        "file":     rom_file.name,
                                        "expected": expected,
                                        "hash":     rep_hash,
                                    })
                    else:
                        for inner_name, actual_hash in hashes:
                            if actual_hash in hash_lookup:
                                if not file_matched:
                                    verified += 1
                                    game_name = hash_lookup[actual_hash]
                                    matched_names.add(game_name)
                                    file_matched = True
                                    matched_game_name = game_name
                                    actual_ext = rom_file.suffix.lower()
                                    is_archive = actual_ext in (".zip", ".7z")
                                    if is_archive:
                                        expected = game_name + actual_ext
                                    else:
                                        canonical_rom = rom_name_lookup.get(actual_hash, "")
                                        expected = canonical_rom if canonical_rom else ""
                                    if expected and rom_file.name != expected:
                                        wrong_name_list.append({
                                            "file":     rom_file.name,
                                            "expected": expected,
                                            "hash":     actual_hash,
                                        })

                if not file_matched:
                    first_hash = hashes[0][1] if hashes else "?"
                    unknown += 1
                    unknown_list.append({"file": rom_file.name, "hash": first_hash})

                # Baddump check: if any of this file's hashes matches a rom
                # flagged status="baddump" for the SAME game it matched -
                # not any game in the DAT. The same CRC is routinely a good
                # dump for one machine and a flagged baddump for another
                # (MAME reuses identical chip ROMs across many machines), so
                # checking a flat set across the whole DAT would wrongly
                # flag good dumps whenever they happen to share a chip CRC
                # with an unrelated machine's known-bad dump.
                if matched_game_name is not None:
                    game_bad_hashes = baddump_hashes.get(matched_game_name)
                    if game_bad_hashes:
                        bd_hash = next((h for _, h in hashes if h in game_bad_hashes), None)
                        if bd_hash:
                            baddump_list.append({"file": rom_file.name, "hash": bd_hash})

                # Emit progress at progress_stride - every file for small
                # folders (a CHD file can take several seconds each, so a
                # fixed "every 20" batching could go minutes with no
                # visible update), but capped for huge folders so a 25k+
                # entry MAME set doesn't push tens of thousands of SSE
                # events/DOM writes for near-instant per-file hashing.
                files_done = verified + unknown
                if files_done % progress_stride == 0 or files_done == len(rom_files):
                    yield sse_event({
                        "type":   "file_progress",
                        "folder": folder,
                        "done":   files_done,
                        "total":  len(rom_files),
                    })
                await asyncio.sleep(0)  # yield between files

            # Emit folder results
            missing_list = [n for n in all_names if n not in matched_names]

            # When a DAT game name is a bare arcade code (no "(...)" region/
            # version tag), substitute its <description> which carries that data.
            def display_name(n: str) -> str:
                return n if "(" in n else name_desc.get(n, n)

            missing_display = [display_name(n) for n in missing_list]
            owned_display   = [display_name(n) for n in sorted(matched_names)]

            # Per-game DAT columns for the Missing-list detail modal, keyed
            # by the same display name used in missing_list so the frontend
            # can look each one up without re-touching the DAT.
            missing_details = []
            for n in missing_list:
                d = game_details.get(n, {})
                c = clone_info.get(n, {})
                missing_details.append({
                    "name":        display_name(n),
                    "description": d.get("description", ""),
                    "category":    d.get("category", ""),
                    "size":        d.get("size"),
                    "crc":         d.get("crc", ""),
                    "md5":         d.get("md5", ""),
                    "sha1":        d.get("sha1", ""),
                    "rom_file":    d.get("rom_file", ""),
                    "rom_count":   d.get("rom_count", 0),
                    "cloneof":     c.get("cloneof", ""),
                    "romof":       c.get("romof", ""),
                })

            folder_result = {
                "type":            "folder_done",
                "folder":          folder,
                "total":           len(rom_files),
                "verified":        verified,
                "unknown":         unknown,
                "missing":         len(missing_list),
                "wrong_name":      len(wrong_name_list),
                "unknown_list":    unknown_list,
                "baddump":         len(baddump_list),
                "baddump_list":    baddump_list,
                "missing_list":    missing_display,
                "missing_details": missing_details,
                "wrong_name_list": wrong_name_list,
                "owned_names":     owned_display,
            }
            set_cached_verify_result(rompath, datroot, folder, hash_type, signature, folder_result)
            yield sse_event({**folder_result, "from_cache": False})

        yield sse_event({"type": "done"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ---------------------------------------------------------------------------
# Game Manager — merge gamelist.xml + DAT data into one filterable/deletable
# per-ROM table.
# ---------------------------------------------------------------------------

def list_gamelist_folders(rompath: str, folder_excl: list[str]) -> list[dict]:
    """Return [{"folder","path","gamelist"}] for every subfolder of rompath
    that contains a gamelist.xml directly. If rompath itself is a single
    system folder (has its own gamelist.xml), return just that one entry -
    same "is this a root or a single system folder" detection media_scan()
    already uses."""
    base = Path(rompath)
    if not base.is_dir():
        return []

    own_gamelist = base / "gamelist.xml"
    if own_gamelist.is_file():
        return [{"folder": base.name, "path": str(base), "gamelist": str(own_gamelist)}]

    result: list[dict] = []
    try:
        with os.scandir(base) as it:
            top_entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return result
    for item in top_entries:
        if not item.is_dir() or item.name in folder_excl:
            continue
        gl = Path(item.path) / "gamelist.xml"
        if gl.is_file():
            result.append({"folder": item.name, "path": item.path, "gamelist": str(gl)})
    return result


def parse_gamelist_rows(xml_path: Path) -> list[dict]:
    """Parse every <game> in a gamelist.xml into a flat dict keyed by every
    child tag actually present (RetroBat and Recalbox gamelists never mix
    field sets, so no mode parameter is needed - just read what's there)."""
    rows: list[dict] = []
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        return rows

    for game in tree.getroot().findall("game"):
        row: dict = {}
        for child in game:
            if child.text and child.text.strip():
                row[child.tag] = child.text.strip()
        if "path" in row:
            row["path"] = Path(row["path"].lstrip("./")).name
        rows.append(row)
    return rows


def load_dat_categories(dat_paths: list[Path]) -> dict[str, str]:
    """Parse DAT files and return {game_name: category}. No-Intro v3 (e.g.
    Atari) and MAME/FBNeo DATs have no <category> tag on <game>/<machine>,
    so those names simply have no entry - callers must .get(name, "")."""
    out: dict[str, str] = {}
    for dat_path in dat_paths:
        try:
            root = _parse_dat_cached(dat_path)
        except ET.ParseError:
            continue
        for game in find_game_elements(root):
            name = game.get("name", "")
            cat  = game.findtext("category", "")
            if name and cat:
                out[name] = cat
    return out


def load_dat_id_to_name(dat_paths: list[Path]) -> dict[str, str]:
    """Parse DAT files and return {game_id: game_name}. No-Intro dats link a
    clone to its parent via a numeric id attribute (cloneofid) rather than a
    name, so load_dat_clone_info()'s cloneof/romof value is that raw id for
    No-Intro and needs resolving back to a display name via this map.
    MAME/FBNeo's cloneof/romof already hold the parent's name directly, so
    looking those up here is a harmless no-op (id not found -> unchanged)."""
    out: dict[str, str] = {}
    for dat_path in dat_paths:
        try:
            root = _parse_dat_cached(dat_path)
        except ET.ParseError:
            continue
        for game in find_game_elements(root):
            gid  = game.get("id", "")
            name = game.get("name", "")
            if gid and name:
                out[gid] = name
    return out


def match_rom_hashes_to_dat_game(
    hashes:      list[tuple[str, str]],
    dat_fmt:     str,
    hash_lookup: dict[str, str],
    game_hashes: dict[str, set[str]],
) -> str | None:
    """Return the matched DAT game name for one ROM's hash_rom_file() output,
    or None if unmatched. Self-contained copy of the matching logic used by
    /api/dat/verify-folders (Redump subset-match / MAME+No-Intro Jaccard
    overlap for multi-rom files / first-CRC-wins for single-rom files) -
    duplicated intentionally rather than shared, so this feature can't
    regress the existing DAT Scanner."""
    if not hashes:
        return None
    file_hash_set = {h for _, h in hashes}

    if dat_fmt == "redump":
        for game_name, g_hashes in game_hashes.items():
            if g_hashes and g_hashes.issubset(file_hash_set):
                return game_name
            if file_hash_set and file_hash_set.issubset(g_hashes) and len(file_hash_set) > 1:
                return game_name
        return None

    if len(hashes) > 1 and game_hashes:
        best_game_name = None
        best_score     = -1.0
        for gname, g_crcs in game_hashes.items():
            if not g_crcs:
                continue
            overlap = len(file_hash_set & g_crcs)
            if overlap == 0:
                continue
            score = overlap / len(file_hash_set | g_crcs)
            if score > best_score:
                best_score     = score
                best_game_name = gname
        return best_game_name

    for _, actual_hash in hashes:
        if actual_hash in hash_lookup:
            return hash_lookup[actual_hash]
    return None


@app.get("/api/gamemanager/folders")
async def gamemanager_folders(rompath: str = Query(...)):
    """List only the subfolders of rompath that contain a gamelist.xml."""
    folders = list_gamelist_folders(rompath, FOLDER_EXCL_BASE + FOLDER_EXCL_EXTRA)
    return JSONResponse({"rompath": rompath, "folders": folders})


@app.get("/api/gamemanager/image")
async def gamemanager_image(path: str = Query(...), rompath: str = Query(...)):
    """Serve a gamelist-referenced media file (image/thumbnail) by absolute
    path, for the Game Manager thumbnail column. Restricted to files inside
    rompath so this can't be used to read arbitrary files off disk."""
    root_p = Path(rompath).resolve()
    p = Path(path).resolve()
    if p != root_p and root_p not in p.parents:
        return JSONResponse({"error": "path is outside the ROM collection root"}, status_code=400)
    if not p.is_file():
        return JSONResponse({"error": f"File not found: {path}"}, status_code=404)
    return FileResponse(p)


@app.post("/api/gamemanager/build")
async def gamemanager_build(
    rompath:   str       = Form(...),
    datroot:   str       = Form(default=""),
    folders:   list[str] = Form(default=[]),
    hash_type: str       = Form(default="off"),
):
    """Build a merged gamelist + DAT row per ROM, streamed as SSE. folders=[]
    means every folder that has a gamelist.xml."""

    async def generate() -> AsyncGenerator[str, None]:
        all_folders = list_gamelist_folders(rompath, FOLDER_EXCL_BASE + FOLDER_EXCL_EXTRA)
        if not all_folders:
            yield sse_event({"type": "error", "message": f"No gamelist.xml found under {rompath}"})
            return

        target_names = set(folders) if folders else None
        selected = [f for f in all_folders if target_names is None or f["folder"] in target_names]
        if not selected:
            yield sse_event({"type": "error", "message": "No matching folders to scan"})
            return

        mapping  = load_dat_mapping(datroot) if datroot else {}
        dat_root = Path(datroot) if datroot else None
        loop     = asyncio.get_event_loop()

        yield sse_event({"type": "start", "total": len(selected)})

        for entry in selected:
            folder      = entry["folder"]
            folder_path = Path(entry["path"])
            yield sse_event({"type": "folder_start", "folder": folder})

            gamelist_rows = await loop.run_in_executor(
                _thread_pool, parse_gamelist_rows, Path(entry["gamelist"])
            )
            by_filename = {r["path"]: r for r in gamelist_rows if r.get("path")}

            # hash_type "off" (the default) skips all DAT hashing/matching -
            # gamelist-only, fast browse mode. Hashing is opt-in.
            dat_files = []
            if dat_root is not None and hash_type != "off":
                dat_files = [dat_root / d for d in mapping.get(folder, []) if (dat_root / d).exists()]

            hash_lookup: dict[str, str]      = {}
            game_hashes: dict[str, set[str]] = {}
            categories:  dict[str, str]      = {}
            clone_info:  dict[str, dict]     = {}
            id_to_name:  dict[str, str]      = {}
            dat_fmt = "unknown"

            if dat_files:
                hash_lookup, _all_names, dat_fmt, _rn, game_hashes, _bd = await loop.run_in_executor(
                    _thread_pool, load_dat_hashes, dat_files, hash_type
                )
                categories = await loop.run_in_executor(_thread_pool, load_dat_categories, dat_files)
                clone_info = await loop.run_in_executor(_thread_pool, load_dat_clone_info, dat_files)
                id_to_name = await loop.run_in_executor(_thread_pool, load_dat_id_to_name, dat_files)

                # MAME/FBNeo dats (and some older No-Intro dats) only carry a
                # crc attribute - selecting MD5/SHA1 there silently matches
                # nothing, which looks identical to "unverified ROM set" from
                # the UI. Flag it explicitly instead of leaving it a mystery.
                if not hash_lookup and not game_hashes:
                    yield sse_event({
                        "type":    "warning",
                        "folder":  folder,
                        "message": f"{folder}: the mapped DAT has no {hash_type.upper()} hashes "
                                    "(common for MAME/FBNeo dats, which only provide CRC). "
                                    "Switch to CRC32 hashing to match ROMs in this folder.",
                    })

            rom_files = {f.name: f for f in list_rom_candidate_files(folder_path)}

            # Multi-disc games (PSX/PS2/Saturn m3u playlists) list their
            # constituent disc images in <multidisk> - those disc files are
            # part of the m3u's row, not standalone games, so exclude them
            # from the union or they'd show up as blank "in_gamelist: false"
            # ghost rows next to the real (correctly populated) m3u row.
            consumed_discs: set[str] = set()
            for r in gamelist_rows:
                md = r.get("multidisk", "")
                if not md:
                    continue
                try:
                    for disc in json.loads(md):
                        consumed_discs.add(Path(disc.lstrip("./")).name)
                except (ValueError, TypeError):
                    pass

            all_filenames = sorted((set(by_filename.keys()) | set(rom_files.keys())) - consumed_discs)
            done = 0

            for filename in all_filenames:
                gl_row  = by_filename.get(filename, {})
                # rom_files excludes sidecar extensions (.m3u/.cue/etc, see
                # FILE_EXCL_SCAN) that aren't hashable ROM payloads - but an
                # m3u playlist genuinely existing on disk is still a real,
                # present multi-disc game, so fall back to a direct check.
                on_disk = filename in rom_files or (folder_path / filename).is_file()

                dat_game_name = None
                category      = ""
                clone_status  = ""
                clone_parent  = ""

                # filename in rom_files excludes multi-disc .m3u playlists
                # (on_disk via the fallback check above, but never a key in
                # rom_files - .m3u is in the non-ROM extension exclusion
                # list). Hashing the .m3u itself couldn't determine real
                # DAT-match status anyway (it's a text playlist, not ROM
                # data, and there's no logic here to roll up the discs it
                # references into one combined result) - leaving these rows
                # unmatched is correct, not just a crash workaround.
                if on_disk and dat_files and filename in rom_files:
                    hashes = await loop.run_in_executor(
                        _thread_pool, hash_rom_file, rom_files[filename], hash_type
                    )
                    dat_game_name = match_rom_hashes_to_dat_game(hashes, dat_fmt, hash_lookup, game_hashes)
                    if dat_game_name:
                        category = categories.get(dat_game_name, "")
                        clone    = clone_info.get(dat_game_name, {})
                        clone_parent = clone.get("cloneof") or clone.get("romof") or ""
                        clone_parent = id_to_name.get(clone_parent, clone_parent)
                        clone_status = "clone" if clone_parent else "parent"

                # Prefer the dedicated thumbnail (RetroBat) over the full image,
                # falling back to Recalbox's single "image" tag.
                thumb_rel = gl_row.get("thumbnail") or gl_row.get("image") or ""
                thumb_path = str(folder_path / thumb_rel.lstrip("./")) if thumb_rel else ""

                row = {
                    "type":          "row",
                    "folder":        folder,
                    "filename":      filename,
                    "on_disk":       on_disk,
                    "in_gamelist":   filename in by_filename,
                    "dat_matched":   dat_game_name is not None,
                    "dat_game_name": dat_game_name or "",
                    "category":      category,
                    "clone_status":  clone_status,
                    "clone_parent":  clone_parent,
                    "thumb_path":    thumb_path,
                }
                for field in GAMELIST_FIELDS_ALL:
                    if field == "path":
                        continue
                    row[field] = gl_row.get(field, "")

                # RetroBat's scraper has no <adult> tag at all (that's a
                # Recalbox-only field) - it flags mature content via
                # <genre>Adults</genre> instead. Without this, the Adult
                # column is silently empty for every RetroBat game even
                # when the scraper *did* mark it as mature.
                if not row["adult"] and row["genre"].strip().lower() in ("adult", "adults"):
                    row["adult"] = "true"

                yield sse_event(row)

                done += 1
                if done % 20 == 0:
                    yield sse_event({"type": "file_progress", "folder": folder,
                                      "done": done, "total": len(all_filenames)})
                await asyncio.sleep(0)

            yield sse_event({"type": "folder_done", "folder": folder, "total": len(all_filenames)})

        yield sse_event({"type": "done"})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# Matches one top-level <game>...</game> block. Requires the tag to start a
# line (optionally after any run of spaces/tabs) rather than a literal
# single tab, since not every gamelist.xml on disk uses tab indentation -
# e.g. files scraped by third-party tools like Skraper use spaces. Verified
# against the full real collection (18,062 <game> entries across 53 files):
# the strict single-tab version matched 18,056; this version matches all
# 18,062, with identical output on every previously-matching entry.
_GAME_BLOCK_RE = re.compile(r"^[ \t]*<game\b.*?</game>[ \t]*\r?\n?", re.DOTALL | re.MULTILINE)
_GAME_PATH_RE  = re.compile(r"<path>(.*?)</path>", re.DOTALL)


def remove_gamelist_entries(gamelist_path: Path, filenames: set[str]) -> tuple[list[dict], str]:
    """Remove <game> entries whose <path> matches one of filenames from a
    gamelist.xml, preserving the exact formatting of every untouched entry
    (text-splice, not a full XML tree rewrite - RetroBat/Recalbox own this
    file's formatting, not us). Handles the common case where a ROM was
    deleted outside gamelist-aware tooling and the frontend never re-scraped,
    leaving a stale <game> entry with no matching file on disk.
    Returns (per-file results, backup file path or "" if nothing was written)."""
    # newline="" disables Python's universal-newline translation on both ends -
    # these files are LF-only on disk; without this, write_text() would
    # silently rewrite every line to CRLF on Windows, bloating the file.
    with open(gamelist_path, encoding="utf-8", newline="") as f:
        raw = f.read()
    found: set[str] = set()

    def _strip(match: re.Match) -> str:
        block = match.group(0)
        path_m = _GAME_PATH_RE.search(block)
        if path_m:
            # <path> text is raw XML - entities like "&amp;" must be decoded
            # back to "&" before comparing against real on-disk filenames
            # (e.g. "Tunnels &amp; Trolls..." -> "Tunnels & Trolls...").
            raw_path = unescape(path_m.group(1).strip())
            fname = Path(raw_path.lstrip("./")).name
            if fname in filenames:
                found.add(fname)
                return ""
        return block

    new_raw = _GAME_BLOCK_RE.sub(_strip, raw)

    results = []
    for fname in filenames:
        if fname in found:
            results.append({"file": fname, "status": "removed"})
        else:
            results.append({"file": fname, "status": "not_found"})

    backup_path = ""
    if found:
        timestamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup       = gamelist_path.with_name(f"{gamelist_path.name}.{timestamp}.bak")
        shutil.copy2(gamelist_path, backup)
        backup_path  = str(backup)

        with open(gamelist_path, "w", encoding="utf-8", newline="") as f:
            f.write(new_raw)

    return results, backup_path


def rename_gamelist_entries(gamelist_path: Path, renames: dict[str, str]) -> tuple[list[dict], str]:
    """Update the <path> text of entries whose current filename matches a key
    in renames, replacing only the filename portion (preserving any
    directory prefix like "./") with the new name. Same text-splice
    approach as remove_gamelist_entries() - preserves formatting of every
    untouched entry. Only call this with filenames that were actually
    renamed on disk - renames not found in the gamelist are reported but
    otherwise harmless.
    Returns (per-file results, backup file path or "" if nothing was written)."""
    with open(gamelist_path, encoding="utf-8", newline="") as f:
        raw = f.read()
    found: set[str] = set()

    def _rename(match: re.Match) -> str:
        block = match.group(0)
        path_m = _GAME_PATH_RE.search(block)
        if path_m:
            raw_path = unescape(path_m.group(1).strip())
            head, sep, tail = raw_path.rpartition("/")
            old_fname = Path(tail).name
            if old_fname in renames:
                found.add(old_fname)
                new_path = f"{head}{sep}{renames[old_fname]}"
                new_text = escape(new_path)
                return block[:path_m.start(1)] + new_text + block[path_m.end(1):]
        return block

    new_raw = _GAME_BLOCK_RE.sub(_rename, raw)

    results = []
    for fname, new_name in renames.items():
        if fname in found:
            results.append({"file": fname, "expected": new_name, "status": "renamed"})
        else:
            results.append({"file": fname, "expected": new_name, "status": "not_found"})

    backup_path = ""
    if found:
        timestamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup       = gamelist_path.with_name(f"{gamelist_path.name}.{timestamp}.bak")
        shutil.copy2(gamelist_path, backup)
        backup_path  = str(backup)

        with open(gamelist_path, "w", encoding="utf-8", newline="") as f:
            f.write(new_raw)

    return results, backup_path


@app.post("/api/gamemanager/remove-gamelist-entries")
async def gamemanager_remove_gamelist_entries(request: Request):
    """Remove stale <game> entries (ROM no longer on disk) from a folder's
    gamelist.xml. Body: {rompath, folder, files: [filename,...]}."""
    body    = await request.json()
    rompath = body.get("rompath", "")
    folder  = body.get("folder", "")
    files   = body.get("files", [])

    system_path  = Path(rompath) / folder
    gamelist_path = system_path / "gamelist.xml"
    if not gamelist_path.is_file():
        return JSONResponse({"error": f"gamelist.xml not found: {gamelist_path}"}, status_code=400)

    filenames = {Path(f).name for f in files}
    results, backup_path = remove_gamelist_entries(gamelist_path, filenames)
    return JSONResponse({"results": results, "backup": backup_path})


# ---------------------------------------------------------------------------
# Routes - Delete unverified ROMs + media, and rename wrong-named files
# ---------------------------------------------------------------------------

@app.post("/api/dat/delete-unverified")
async def delete_unverified(request: Request):
    """Delete ROM files not in DAT plus their gamelist.xml media references,
    and strip the matching <game> entries from gamelist.xml so no orphaned
    record is left behind. Shared by the DAT Scanner, Duplicates, and Game
    Manager delete flows.
    Body: {rompath, folder, files: [filename,...]}
    """
    body       = await request.json()
    rompath    = body.get("rompath", "")
    folder     = body.get("folder", "")
    files      = body.get("files", [])

    system_path = Path(rompath) / folder
    if not system_path.is_dir():
        return JSONResponse({"error": f"Folder not found: {system_path}"}, status_code=400)

    mode = "retrobat" if (system_path / "images").is_dir() else "recalbox"
    xml_path = system_path / "gamelist.xml"
    rom_to_media: dict[str, list[Path]] = {}

    if xml_path.exists():
        for _raw_path, rom_name, fields in _walk_gamelist_media(xml_path, mode):
            media_files = [mp for relative in fields.values()
                           if (mp := system_path / relative).exists()]
            rom_to_media[rom_name] = media_files

    results = []
    for filename in files:
        safe     = Path(filename).name
        rom_path = system_path / safe
        media    = rom_to_media.get(safe, [])

        if not rom_path.exists():
            results.append({"file": safe, "type": "rom", "status": "not_found"})
        else:
            try:
                rom_path.unlink()
                results.append({"file": safe, "type": "rom", "status": "deleted"})
            except OSError as e:
                results.append({"file": safe, "type": "rom", "status": "error", "detail": str(e)})

        for mp in media:
            try:
                mp.unlink()
                results.append({"file": str(mp.relative_to(system_path)), "type": "media", "status": "deleted"})
            except OSError as e:
                results.append({"file": str(mp.relative_to(system_path)), "type": "media", "status": "error", "detail": str(e)})

    gamelist_backup = ""
    if xml_path.exists() and files:
        filenames = {Path(f).name for f in files}
        gl_results, gamelist_backup = remove_gamelist_entries(xml_path, filenames)
        for r in gl_results:
            if r["status"] != "not_found":
                results.append({"file": r["file"], "type": "gamelist", "status": r["status"]})

    return JSONResponse({"results": results, "mode": mode, "gamelist_backup": gamelist_backup})


@app.post("/api/dat/rename-files")
async def rename_files(request: Request):
    """Rename ROM files to their DAT canonical names, and update the
    matching <path> in gamelist.xml so the entry doesn't go stale.
    Body: {rompath, folder, renames: [{file, expected},...]}
    Also renames the inner file inside .zip archives.
    """
    body    = await request.json()
    rompath = body.get("rompath", "")
    folder  = body.get("folder", "")
    renames = body.get("renames", [])

    system_path = Path(rompath) / folder
    if not system_path.is_dir():
        return JSONResponse({"error": f"Folder not found: {system_path}"}, status_code=400)

    results = []
    for item in renames:
        actual   = Path(item.get("file", "")).name
        expected = Path(item.get("expected", "")).name
        src      = system_path / actual
        dst      = system_path / expected

        if not src.exists():
            results.append({"file": actual, "expected": expected, "status": "not_found"})
            continue
        # Case-insensitive comparison for Windows
        src_resolved = src.resolve()
        dst_resolved = dst.resolve()
        same_file = src_resolved == dst_resolved or                     (src_resolved.name.lower() == dst_resolved.name.lower() and
                     src_resolved.parent == dst_resolved.parent)
        if dst.exists() and not same_file:
            results.append({"file": actual, "expected": expected, "status": "already_exists"})
            continue

        tmp = src.with_suffix(".tmp.zip")
        try:
            # For zip files, also rename the inner ROM file
            if src.suffix.lower() == ".zip":
                inner_stem = Path(expected).stem

                # Read all zip data BEFORE opening temp — keeps file handles clean
                zip_entries: list[tuple[str, bytes]] = []
                inner     = None
                inner_ext = ""
                with zipfile.ZipFile(src, "r") as zin:
                    names = zin.namelist()
                    inner = names[0] if names else None
                    inner_ext = Path(inner).suffix if inner else ""
                    for name in names:
                        zip_entries.append((name, zin.read(name)))
                # File is now fully closed

                new_inner = inner_stem + inner_ext
                if inner and inner != new_inner:
                    # Write rebuilt zip to temp file
                    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                        for name, data in zip_entries:
                            out_name = new_inner if name == inner else name
                            zout.writestr(out_name, data)
                    # Now safe to replace — original is closed
                    src.unlink()
                    tmp.rename(src)

            # Rename the container file to canonical name
            # On Windows, renaming to same name with different case requires two steps
            if src.name.lower() == dst.name.lower() and src.name != dst.name:
                tmp_name = src.with_name(src.stem + "._rename_tmp_" + src.suffix)
                src.rename(tmp_name)
                tmp_name.rename(dst)
            else:
                src.rename(dst)
            results.append({"file": actual, "expected": expected, "status": "renamed"})

        except (OSError, zipfile.BadZipFile) as e:
            results.append({"file": actual, "expected": expected, "status": "error", "detail": str(e)})
        finally:
            # Always clean up temp file if it still exists
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    gamelist_backup = ""
    gamelist_path   = system_path / "gamelist.xml"
    renamed_map = {r["file"]: r["expected"] for r in results if r["status"] == "renamed"}
    if gamelist_path.is_file() and renamed_map:
        gl_results, gamelist_backup = rename_gamelist_entries(gamelist_path, renamed_map)
        for r in gl_results:
            if r["status"] != "not_found":
                results.append({"file": r["file"], "expected": r["expected"],
                                 "status": r["status"], "type": "gamelist"})

    return JSONResponse({"results": results, "gamelist_backup": gamelist_backup})


# ---------------------------------------------------------------------------
# Routes - Collection Compare (two sources, folder-by-folder by filename)
# ---------------------------------------------------------------------------

# Media subfolder names to exclude when "Include Media" is OFF.
COMPARE_MEDIA_FOLDERS = ["media", "images", "manuals", "maps", "videos",
                         "ports", "bezels", "box", "boxart", "covers",
                         "marquees", "screenshots", "wheels", "thumbs",
                         "downloaded_images", "downloaded_media"]

# Compare treats .m3u/.cue playlist/index files as real, comparable,
# copyable files - unlike every other tool (MD5 Scan, DAT Scanner, Game
# Manager) which intentionally excludes them as non-hashable "ROM"
# payloads. A multi-disc game's .m3u is required for the discs it
# references to actually load in RetroBat/Recalbox; excluding it from
# Compare meant copying a missing multi-disc game left the .m3u behind,
# silently producing a non-functional set on the destination.
COMPARE_FILE_EXCL = [e for e in ROM_FILE_EXCL if e not in (".m3u", ".cue")]

# System folders that aren't real ROM collections - always excluded from
# Collection Compare regardless of the "include media" toggle.
COMPARE_FOLDER_EXCL = [".uncompressed", "singe"]


def _is_media_folder(folder_path: str) -> bool:
    """True if any path segment is a known media folder name."""
    segs = re.split(r"[/\\]", folder_path.lower())
    return any(s in COMPARE_MEDIA_FOLDERS for s in segs)


def _compare_system_dirs(rompath: str, folder_excl: list[str]) -> dict[str, Path]:
    """Return {system_name: system_path} for a collection root. If rompath
    itself has no qualifying subdirectories (i.e. it IS a single system
    folder with ROMs directly inside), returns just {rompath.name: rompath}.
    Mirrors the has-subfolders autodetection list_roms() does internally,
    but exposed separately so compare_scan() can enumerate systems up front
    and stream one SSE event per system instead of one opaque whole-tree
    call with no progress feedback."""
    base = Path(rompath)
    if not base.is_dir():
        return {}
    try:
        with os.scandir(base) as it:
            subdirs = [e for e in it if e.is_dir() and e.name not in folder_excl]
    except OSError:
        return {}
    if not subdirs:
        return {base.name: base}
    return {e.name: Path(e.path) for e in subdirs}


@app.post("/api/compare/scan")
async def compare_scan(
    path1:         str = Form(...),
    path2:         str = Form(...),
    include_media: str = Form(default="false"),
):
    """Compare two collections folder-by-folder, matching by filename,
    streamed as SSE (one event per system folder) so the UI can show
    progress and cancel mid-scan on a large collection - same pattern as
    every other scan tab. With include_media off, media subfolders are
    skipped."""
    inc_media = include_media.lower() in ("true", "1", "yes", "on")
    p1, p2 = Path(path1), Path(path2)

    async def generate() -> AsyncGenerator[str, None]:
        if not p1.is_dir():
            yield sse_event({"type": "error", "message": f"Collection 1 path not found: {path1}"})
            return
        if not p2.is_dir():
            yield sse_event({"type": "error", "message": f"Collection 2 path not found: {path2}"})
            return

        folder_excl = list(COMPARE_FOLDER_EXCL) + ([] if inc_media else list(COMPARE_MEDIA_FOLDERS))
        file_excl   = [] if inc_media else list(COMPARE_FILE_EXCL)

        loop = asyncio.get_event_loop()
        dirs1 = await loop.run_in_executor(_thread_pool, _compare_system_dirs, path1, folder_excl)
        dirs2 = await loop.run_in_executor(_thread_pool, _compare_system_dirs, path2, folder_excl)
        all_names = sorted(set(dirs1) | set(dirs2), key=str.lower)

        if not all_names:
            yield sse_event({"type": "error", "message": f"No system folders found in {path1} or {path2}"})
            return

        yield sse_event({"type": "start", "total": len(all_names),
                          "path1": path1, "path2": path2, "include_media": inc_media})

        for name in all_names:
            d1, d2 = dirs1.get(name), dirs2.get(name)
            map1 = (await loop.run_in_executor(_thread_pool, list_roms, str(d1), file_excl, folder_excl, name)) if d1 else {}
            map2 = (await loop.run_in_executor(_thread_pool, list_roms, str(d2), file_excl, folder_excl, name)) if d2 else {}

            for folder in sorted(set(map1.keys()) | set(map2.keys()), key=str.lower):
                f1 = set(map1.get(folder, []))
                f2 = set(map2.get(folder, []))
                matched = sorted(f1 & f2, key=str.lower)
                only1   = sorted(f1 - f2, key=str.lower)
                only2   = sorted(f2 - f1, key=str.lower)

                yield sse_event({
                    "type":         "folder",
                    "folder":       folder,
                    "is_media":     _is_media_folder(folder),
                    "matched":      len(matched),
                    "only1":        len(only1),
                    "only2":        len(only2),
                    "matched_list": matched,
                    "only1_list":   only1,
                    "only2_list":   only2,
                })

            yield sse_event({"type": "system_done", "system": name})
            await asyncio.sleep(0)

        yield sse_event({"type": "done"})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _resolve_within_root(root: str, rel: str) -> Path | None:
    """Resolve `rel` (a "/"-joined relative bucket path, may be empty) onto
    root and return the resolved directory only if it's actually inside
    root - guards compare_copy()/compare_delete() against a folder value
    that escapes the collection root (e.g. via ".."). Every other
    delete-capable endpoint in this file sanitizes filenames with
    Path(x).name; this is the folder-level equivalent for these two, whose
    "folder" can legitimately contain "/" (e.g. "nes/media/images") so it
    can't just be reduced to a bare name like a filename can."""
    root_p = Path(root).resolve()
    target = (root_p / rel).resolve() if rel else root_p
    if target != root_p and root_p not in target.parents:
        return None
    return target


@app.post("/api/compare/copy")
async def compare_copy(
    src_root:  str = Form(...),
    dst_root:  str = Form(...),
    folder:    str = Form(...),
    files:     str = Form(...),
):
    """Copy files for one folder from src to dst. Creates the dest folder if
    needed; skips files already present. Folder is the relative bucket path
    (e.g. 'nes' or 'nes/media/images')."""
    try:
        file_list = json.loads(files)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid files payload"})

    rel = folder.replace("\\", "/")
    src_dir = _resolve_within_root(src_root, rel)
    dst_dir = _resolve_within_root(dst_root, rel)
    if src_dir is None or dst_dir is None:
        return JSONResponse({"error": "invalid folder path"}, status_code=400)
    if not src_dir.is_dir():
        return JSONResponse({"error": f"source folder not found: {src_dir}"})

    results = []
    def _copy_all():
        dst_dir.mkdir(parents=True, exist_ok=True)
        for fn in file_list:
            fn = Path(fn).name
            if not fn:
                continue
            src = src_dir / fn
            dst = dst_dir / fn
            try:
                if not src.is_file():
                    results.append({"file": fn, "status": "error", "detail": "source missing"})
                elif dst.exists():
                    results.append({"file": fn, "status": "skipped", "detail": "already exists"})
                else:
                    shutil.copy2(src, dst)
                    results.append({"file": fn, "status": "copied"})
            except OSError as e:
                results.append({"file": fn, "status": "error", "detail": str(e)})

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_thread_pool, _copy_all)

    copied  = sum(1 for r in results if r["status"] == "copied")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errors  = sum(1 for r in results if r["status"] == "error")
    return JSONResponse({"results": results, "copied": copied, "skipped": skipped, "errors": errors})


@app.post("/api/compare/delete")
async def compare_delete(
    root:   str = Form(...),
    folder: str = Form(...),
    files:  str = Form(...),
):
    """Delete files for one folder from a single collection root - the
    reverse of compare_copy(), for pruning files that don't exist on the
    other side instead of copying them over. Also strips the matching
    <game> entries from that folder's gamelist.xml (if any) so no orphaned
    record is left behind. Folder is the relative bucket path (e.g. 'nes'
    or 'nes/media/images' - the latter has no gamelist.xml, so the strip
    step is naturally a no-op there)."""
    try:
        file_list = json.loads(files)
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid files payload"})
    file_list = [fn for fn in (Path(f).name for f in file_list) if fn]

    rel = folder.replace("\\", "/")
    target_dir = _resolve_within_root(root, rel)
    if target_dir is None:
        return JSONResponse({"error": "invalid folder path"}, status_code=400)
    if not target_dir.is_dir():
        return JSONResponse({"error": f"folder not found: {target_dir}"})

    results = []
    def _delete_all():
        for fn in file_list:
            filepath = target_dir / fn
            try:
                if not filepath.is_file():
                    results.append({"file": fn, "status": "not_found"})
                else:
                    filepath.unlink()
                    results.append({"file": fn, "status": "deleted"})
            except OSError as e:
                results.append({"file": fn, "status": "error", "detail": str(e)})

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_thread_pool, _delete_all)

    gamelist_path = target_dir / "gamelist.xml"
    gamelist_backup = ""
    if gamelist_path.is_file() and file_list:
        gl_results, gamelist_backup = await loop.run_in_executor(
            _thread_pool, remove_gamelist_entries, gamelist_path, set(file_list)
        )
        for r in gl_results:
            if r["status"] != "not_found":
                results.append({"file": r["file"], "status": r["status"]})

    deleted = sum(1 for r in results if r["status"] == "deleted")
    errors  = sum(1 for r in results if r["status"] == "error")
    return JSONResponse({"results": results, "deleted": deleted, "errors": errors, "gamelist_backup": gamelist_backup})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:8000")).start()
    uvicorn.run("romtools:app", host="0.0.0.0", port=8000, reload=True)