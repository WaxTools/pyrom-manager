# Installing PYRom Manager on macOS / Linux

This guide assumes **zero prior experience** — you've never used a terminal, never installed Python yourself, and don't know what a "virtual environment" is. Every step is spelled out. It will take about 15–20 minutes the first time.

If you get stuck, see the [Troubleshooting](#troubleshooting) section at the bottom before giving up — the most common problems have simple fixes.

Where a command differs between macOS and Linux, both are shown.

---

## Table of Contents

1. [What you're about to do](#1-what-youre-about-to-do)
2. [Open a terminal](#2-open-a-terminal)
3. [Install Python](#3-install-python)
4. [Get the PYRom Manager files onto your computer](#4-get-the-pyrom-manager-files-onto-your-computer)
5. [Move into the project folder in the terminal](#5-move-into-the-project-folder-in-the-terminal)
6. [Create a virtual environment](#6-create-a-virtual-environment)
7. [Install the app's dependencies](#7-install-the-apps-dependencies)
8. [Run the app](#8-run-the-app)
9. [Stopping the app, and running it again later](#9-stopping-the-app-and-running-it-again-later)
10. [Troubleshooting](#troubleshooting)

---

## 1. What you're about to do

PYRom Manager is a **Python program** that runs a small local web server on your own computer. When it starts, it automatically opens your default web browser to `http://localhost:8000`. Nothing gets uploaded to the internet — it all stays on your machine.

To get there, you'll:
- Install **Python**, if you don't already have a recent enough version.
- Download the PYRom Manager files.
- Use a **terminal** (a text window where you type commands instead of clicking) to set up and start the app. You'll only ever need to type the exact commands shown below — copy/paste is fine.

A **terminal** is just a window where you type a line of text and press Return/Enter, instead of clicking icons.

---

## 2. Open a terminal

**macOS:**
1. Click the magnifying glass icon in the top-right of your screen (**Spotlight Search**), or press **Cmd + Space**.
2. Type `Terminal` and press Enter.
3. A black or white window opens with a blinking cursor — this is your terminal.

**Linux:**
Most distributions let you open a terminal with the keyboard shortcut **Ctrl + Alt + T**, or by searching for "Terminal" in your applications menu (the icon usually looks like a black screen with `>_`).

Keep this window open — you'll use it for the rest of the setup.

---

## 3. Install Python

### Check if you already have a suitable version

In the terminal, type:

```bash
python3 --version
```

and press Enter. If you see `Python 3.10.x` or higher (e.g. `3.11`, `3.12`), you're already set — skip to [step 4](#4-get-the-pyrom-manager-files-onto-your-computer).

If you see an error, or a version lower than 3.10, install/upgrade Python:

### macOS

1. Go to **https://www.python.org/downloads/** in your browser.
2. Click the yellow **"Download Python 3.x.x"** button for macOS.
3. Open the downloaded `.pkg` file and follow the installer prompts (Continue → Continue → Agree → Install), entering your Mac's password if asked.
4. Once done, close and reopen Terminal, then check again:
   ```bash
   python3 --version
   ```

> If you have [Homebrew](https://brew.sh/) installed already, `brew install python3` also works and is a common alternative — but the installer above is simpler if this is your first time.

### Linux

Most Linux distributions already include Python 3. If your `python3 --version` above showed something below 3.10, update it using your distribution's package manager:

**Debian/Ubuntu-based:**
```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

**Fedora:**
```bash
sudo dnf install python3 python3-pip
```

**Arch:**
```bash
sudo pacman -S python python-pip
```

You'll be asked for your account password when using `sudo` — that's normal, it just means "run this with administrator permission."

---

## 4. Get the PYRom Manager files onto your computer

You don't need to know Git for this. The easiest way:

1. Go to **https://github.com/WaxTools/pyrom-manager** in your browser.
2. Click the green **"Code"** button.
3. Click **"Download ZIP"**.
4. Find the downloaded file in your **Downloads** folder (something like `pyrom-manager-master.zip`) and extract it:
   - **macOS:** double-click the ZIP file — it extracts automatically into the same folder.
   - **Linux:** right-click the ZIP file and choose "Extract Here", or in the terminal: `cd ~/Downloads && unzip pyrom-manager-master.zip`.
5. You'll end up with a folder named something like `pyrom-manager-master`. Move it somewhere you'll remember, e.g. your Home folder or Documents. You can rename it to `pyrom-manager` if you like.

> **Prefer using Git instead?** If Git is installed (`git --version` to check), you can instead run `git clone https://github.com/WaxTools/pyrom-manager.git` — this makes it easier to pull future updates. Either method works equally well for running the app.

---

## 5. Move into the project folder in the terminal

In your terminal, use the `cd` (change directory) command to move into the folder. For example, if you extracted it to your Downloads folder:

```bash
cd ~/Downloads/pyrom-manager-master
```

(Replace the path with wherever you actually put the folder. Tip: you can type `cd ` (with a trailing space) in the terminal, then drag the folder from Finder/Files straight into the terminal window — it will fill in the correct path for you automatically.)

Confirm you're in the right place by typing:

```bash
ls
```

You should see `romtools.py`, `requirements.txt`, `templates`, etc. listed. If you see those, you're good to continue.

---

## 6. Create a virtual environment

A **virtual environment** ("venv") is just a private, isolated folder where Python installs this app's specific dependencies, so they don't clash with anything else on your system. Think of it as a clean toolbox just for this app. You only need to create it **once**.

```bash
python3 -m venv venv
```

Press Enter and wait a few seconds. When you get your prompt back, it's done — you'll see a new `venv` folder inside your project folder.

Now **activate** it (you'll do this every time you open a new terminal to work with this app):

```bash
source venv/bin/activate
```

If it worked, your prompt will now start with `(venv)`, like:

```
(venv) yourname@computer pyrom-manager-master %
```

---

## 7. Install the app's dependencies

With `(venv)` showing at the start of your prompt, type:

```bash
pip install -r requirements.txt
```

Press Enter. You'll see text scroll by as it downloads and installs several packages (FastAPI, Uvicorn, etc.) — this can take 1-2 minutes. When it finishes with no red error text, you're done.

---

## 8. Run the app

Still with `(venv)` showing, type:

```bash
python romtools.py
```

You should see startup text ending in something like:

```
Uvicorn running on http://0.0.0.0:8000
```

This means it's working! **Leave this terminal window open** — closing it stops the app.

A browser tab should open **automatically** a second or two later, pointed at `http://localhost:8000`, showing the PYRom Manager interface. If it doesn't open on its own, just open your browser and type that address in yourself.

---

## 9. Stopping the app, and running it again later

- **To stop the app:** click into the terminal window running it, and press **Ctrl + C**. Then you can close the window.
- **To run it again later**, you do **not** need to repeat the venv creation or `pip install` steps — those only happen once. Just:
  1. Open a terminal ([step 2](#2-open-a-terminal)).
  2. Move into the project folder (`cd` as in [step 5](#5-move-into-the-project-folder-in-the-terminal)).
  3. Run:
     ```bash
     source venv/bin/activate
     python romtools.py
     ```
  4. Your browser should open automatically to `http://localhost:8000` — if not, open it yourself.

---

## Troubleshooting

### "command not found: python3"
Python isn't installed, or your terminal can't find it. Revisit [step 3](#3-install-python). On macOS, make sure you actually ran the downloaded installer (not just downloaded it). On Linux, use your distribution's package manager command from step 3.

### "command not found: pip" (after activating the venv)
Make sure your prompt actually shows `(venv)` at the start — if activation ([step 6](#6-create-a-virtual-environment)) didn't succeed, re-run:
```bash
source venv/bin/activate
```

### Permission denied errors when installing packages
If you see permission errors during `pip install -r requirements.txt`, it almost always means the virtual environment isn't activated (you're installing into the system Python instead). Confirm `(venv)` is showing, then try again. Never use `sudo pip install` for this project — you shouldn't need administrator rights once the venv is active.

### "zsh: command not found: python" but `python3` works
On modern macOS, the command is `python3`, not `python`, unless you've set up an alias. Just use `python3` for the initial venv creation step; once the venv is activated, `python` will correctly point to the venv's Python inside that terminal session.

### Browser shows "can't reach this page" / "connection refused" at localhost:8000
- Make sure the terminal window running `python romtools.py` is still open and shows no error.
- Confirm the terminal output mentions port `8000`.
- Double check you typed `localhost:8000` correctly in the browser.

### "Address already in use" / "port 8000 already in use"
Something else is already using port 8000 (maybe another copy of this app is already running in another terminal window). Find and close it, or restart your computer, then try again.

### Still stuck?
Open an issue at **https://github.com/WaxTools/pyrom-manager/issues** with:
- The exact command you ran
- The exact error text (copy/paste it, don't paraphrase)
- Your OS/distribution and Python version (`python3 --version`)
