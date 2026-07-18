# Updating PYRom Manager

PYRom Manager checks GitHub for a newer release automatically and shows a green banner in the app when one's available — but **it does not update itself**. This is a deliberate choice: an app that already reads, renames, moves, and deletes files on your disk shouldn't also be quietly overwriting its own code. Updating is a manual, few-minute step, and your data is never touched by it either way.

Pick the section below matching how you originally installed it.

---

## If you installed via `git clone`

From inside your `pyrom-manager` folder:

```bash
git pull
```

Then reinstall dependencies (in case `requirements.txt` changed) and restart the app:

```bash
# Windows
venv\Scripts\activate
pip install -r requirements.txt

# macOS/Linux
source venv/bin/activate
pip install -r requirements.txt
```

Restart the app (`run.bat`, or `python romtools.py`) as usual.

---

## If you installed via "Download ZIP"

1. Go to **https://github.com/WaxTools/pyrom-manager/releases/latest** and download the new `Source code (zip)`.
2. Extract it to a **new, separate folder** first (don't extract directly on top of your existing install).
3. Copy these files/folders from the new extracted folder into your existing `pyrom-manager` folder, overwriting when prompted:
   - `romtools.py`
   - `templates/`
   - `requirements.txt`
   - `run.bat`
   - any other tracked project files (README, docs, etc., if you want them too)
4. **Do not copy over** — and don't worry about losing — anything you have locally that isn't in the new ZIP. These are yours, are already excluded from every release archive, and are untouched by this process:
   - `config.json` (your local settings)
   - `cache/` (the hash/verify cache database)
   - `DatRoot/` (your DAT files and folder mappings)
   - `chdman/` (your `chdman.exe`)
   - `venv/` (your Python virtual environment)
5. Reinstall dependencies, in case they changed:
   ```
   # Windows
   venv\Scripts\activate
   pip install -r requirements.txt

   # macOS/Linux
   source venv/bin/activate
   pip install -r requirements.txt
   ```
6. Restart the app.

---

## Checking what changed

See [CHANGELOG.md](../CHANGELOG.md) for a summary of each release, or the full **[Releases page](https://github.com/WaxTools/pyrom-manager/releases)** for release notes on a specific version.

## Checking your current version

Your installed version is in the `VERSION` file at the project root, and is also shown in the update banner inside the app whenever a newer release is available.
