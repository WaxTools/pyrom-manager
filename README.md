# PYRom Manager

A self-hosted, browser-based toolkit for organizing, verifying, and cleaning up large retro-gaming ROM collections (Recalbox, RetroBat, EmulationStation-style layouts). Built with FastAPI + vanilla JS, running entirely on your own machine against your own files.

> **⚠️ This app modifies, renames, and deletes files on disk.** Read the [Disclaimer](#-disclaimer) before you point it at anything you can't afford to lose.

> **🧪 Tested environments:** this app has only been validated on **RetroBat (Windows)** and **Recalbox (Raspberry Pi)**. It may well work on other EmulationStation-based setups (Batocera, standalone ES, etc.) since they share similar `gamelist.xml`/folder conventions, but that's untested — proceed with extra caution (and backups!) on anything else, and please [let us know](#-contributing--feedback) how it goes either way.

> **🤝 Contributions and feedback wanted!** This is an actively-used personal project shared in the hope it's useful to others too. Bug reports, feature requests, pull requests, and "hey, this worked/didn't work on my setup" reports are all genuinely welcome — see [Contributing & Feedback](#-contributing--feedback) below. Don't be shy about opening an issue.

---

## Table of Contents

- [Summary](#pyrom-manager)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running the App](#running-the-app)
- [Configuration](#configuration)
- [User Guide](#user-guide)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Contributing & Feedback](#-contributing--feedback)
- [Disclaimer](#-disclaimer)
- [License](#license)

---

## Features

PYRom Manager is organized into tabs, each targeting a different housekeeping job on a ROM collection:

| Tab | What it does |
|---|---|
| 🔑 **ROM File List** | Scans a ROM folder tree and computes CRC32/MD5/SHA1 hashes for every file (including inside archives), with results cached so repeat scans are fast. |
| 🖼 **Media Cleaner** | Finds orphaned media (box art, screenshots, videos, manuals) that no longer has a matching ROM, and lets you delete it folder-by-folder. |
| 📋 **DAT Manager** | A DAT Catalogue (browse/inspect No-Intro / Redump / MAME style `.dat` files and map them to your ROM folders), a DAT Coverage view (which folders have a DAT mapped), and a ROM Scanner that deep-verifies your ROMs against DAT checksums (including on-the-fly `.chd` extraction via `chdman`), reports unverified/misnamed files, and can delete or rename them to match the DAT. |
| 👥 **Duplicates** | Cleans up duplicate/leftover `readme` files dropped by Recalbox scraping. |
| 🗃 **Game Manager** | Merges `gamelist.xml` metadata with DAT verification results into one filterable, sortable, deletable game catalogue (with cover art preview). |
| 🔄 **Compare** | Compares two ROM sources (e.g. two systems' folders) file-by-file and lets you copy or delete the differences. |
| 🔧 **Utilities** | App settings (e.g. the path to `chdman.exe`) and cache management. |

All scans stream live progress to the browser over Server-Sent Events, so long-running jobs (hashing tens of thousands of files) show real-time status instead of a spinner.

## Requirements

- **Python 3.10+**
- **Windows, macOS, or Linux** (paths in the UI accept both `\\` and `/`/UNC style network paths)
- **[chdman](https://www.mamedev.org/)** (from the MAME tools distribution) — optional, only needed if you want the DAT Scanner to verify compressed `.chd` disc images. Point the app at your `chdman` executable from the Utilities tab; everything else works without it.

Python dependencies (installed via `requirements.txt`):

- `fastapi`, `uvicorn` — web server
- `jinja2` — HTML templating
- `aiofiles`, `python-multipart` — file/form handling
- `py7zr` — reading `.7z` archives

## Installation

> **New to the command line, Python, or virtual environments?** Use the detailed step-by-step guide for your OS instead — it explains every step in plain English, with troubleshooting for common errors:
> - 🪟 **[Detailed Windows install guide](docs/INSTALL_WINDOWS.md)**
> - 🍎🐧 **[Detailed macOS/Linux install guide](docs/INSTALL_MACOS_LINUX.md)**

The short version, if you're already comfortable with a terminal:

```bash
# 1. Clone the repo
git clone https://github.com/wtaulu/pyrom-manager.git
cd pyrom-manager

# 2. Create and activate a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

## Running the App

**Windows:** double-click `run.bat`, or:

```bash
venv\Scripts\activate
python romtools.py
```

**macOS/Linux:**

```bash
source venv/bin/activate
python romtools.py
```

Then open **http://localhost:8000** in your browser.

You can also run it directly with uvicorn (useful for auto-reload during development):

```bash
uvicorn romtools:app --reload --port 8000
```

## Configuration

App-level settings (currently just the path to `chdman`) are stored in a local `config.json` in the project root. This file is **not** part of the repo (it's machine-specific and gitignored) — the app creates it automatically the first time you save a setting from the **Utilities** tab, or you can create it by hand:

```json
{
  "chdman_path": "C:\\path\\to\\chdman.exe"
}
```

If it's missing, the app simply treats settings as unset — nothing breaks.

A local SQLite cache (`cache/cache.db`) is also created automatically on first use to speed up repeat hash/verify scans. It's safe to delete at any time via **Utilities → Clear Cache**, or by removing the `cache/` folder — it will be rebuilt on next use.

## User Guide

### ROM File List (MD5/hash scan)
Point it at a ROM folder. It walks every subfolder, computes hashes for each ROM file (opening archives to hash their contents where relevant), and lists the results. Results are cached by file path + modification time + size, so unchanged files aren't re-hashed on the next run.

### Media Cleaner
Point it at a ROMs root that also contains a `media/` (or similar) subfolder per system. It cross-references media files against existing ROMs and flags media with no matching ROM as an orphan. You review the list per folder and delete what you don't want.

### DAT Manager
- **DAT Catalogue** — browse the `.dat` files in your `DatRoot` folder, inspect their contents, and map each one to the ROM folder it corresponds to.
- **DAT Coverage** — see at a glance which of your ROM folders have (or are missing) a DAT mapping.
- **ROM Scanner** — deep-verifies ROMs in a mapped folder against the DAT's checksums. For CHD-based systems it extracts and hashes the disc image via `chdman` first. Produces a report of verified/unverified/misnamed files, which you can then act on (delete unverified, rename to match the DAT).

### Duplicates
Scans for and removes duplicate scraper-generated `readme`/description files left behind in ROM folders (a common Recalbox artifact).

### Game Manager
Combines a folder's `gamelist.xml` (EmulationStation/Recalbox/RetroBat metadata) with DAT verification results into a single browsable, filterable, sortable table — with cover art — so you can review your whole collection and delete entries (and their files) in bulk.

### Compare
Point it at two folders (e.g. comparing a backup against your live ROMs folder, or two versions of the same system's set). It diffs file lists by name and lets you copy missing files across, or delete extras.

### Utilities
Set the `chdman` path, and view/clear the SQLite result cache.

## Project Structure

```
pyrom-manager/
├── romtools.py          # FastAPI app: all routes, scanning/hashing/DAT logic
├── templates/
│   └── base.html        # Single-page UI (tabs, JS, styles)
├── docs/
│   ├── INSTALL_WINDOWS.md      # Detailed step-by-step Windows install guide
│   └── INSTALL_MACOS_LINUX.md  # Detailed step-by-step macOS/Linux install guide
├── requirements.txt      # Python dependencies
├── run.bat               # Windows launcher
├── CHANGELOG.md          # Release history
├── config.json           # Local settings (gitignored, created automatically)
├── cache/                # SQLite hash/verify cache (gitignored, created automatically)
├── DatRoot/               # Your DAT files + folder mappings (gitignored, user-provided)
└── chdman/                # Your chdman.exe (gitignored, user-provided)
```

## Troubleshooting

- **"chdman not found" / CHD verification fails** — set the correct path under Utilities, or skip CHD-based systems.
- **Scan seems stuck** — very large collections (tens of thousands of files) can take a while on first scan; subsequent scans of the same folder are much faster thanks to the cache. Check the browser console / terminal running the app for errors.
- **Cache acting up / stale results** — clear it from Utilities → Clear Cache, or delete the `cache/` folder while the app is stopped.
- **Network share paths** — UNC paths (`\\server\share\...` or `//server/share/...`) are supported in the path fields.

## 🤝 Contributing & Feedback

This app has only been tested against **RetroBat (Windows)** and **Recalbox (Raspberry Pi)** setups so far. If you run it against a different frontend/OS combo (Batocera, standalone EmulationStation, ES-DE, etc.), a different system's ROM set, or just hit something weird — **please open an issue and let us know**, whether it worked great or broke horribly. Both are useful.

Ways to help make this a better app:

- 🐛 **Found a bug?** [Open an issue](../../issues/new?template=bug_report.md) with what you did and what happened.
- 💡 **Have an idea?** [Open a feature request](../../issues/new?template=feature_request.md).
- ✅ **It worked for you?** Even a quick issue or discussion post saying "tested on X, works fine" genuinely helps others trust the tool on their own setup.
- 🔧 **Want to fix/build something yourself?** See [CONTRIBUTING.md](CONTRIBUTING.md) for how to submit a pull request.

No feedback is too small — this project gets better the more real-world setups it's tried against.

## ⚠️ Disclaimer

**This application reads, hashes, renames, moves, copies, and permanently deletes files on your filesystem, based on the actions you take in the UI (and in some cases automatically as part of a scan/cleanup operation).** There is no recycle-bin/undo built in — deleted files are gone.

By using this software, you acknowledge and agree that:

- You are solely responsible for backing up your data before use.
- You use this software entirely at your own risk.
- The author(s) and contributors provide no warranty of any kind and **take no responsibility for any data loss, corruption, or other damage** resulting from the use, misuse, or malfunction of this software.
- This software is provided "AS IS", without warranty of any kind, express or implied, as further detailed in the [LICENSE](LICENSE).

If you are not comfortable with a tool that can delete files, do not run the delete/rename/cleanup operations against ROM folders you have not backed up.

## License

MIT — see [LICENSE](LICENSE).
