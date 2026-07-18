# Installing PYRom Manager on Windows

This guide assumes **zero prior experience** — you've never used a terminal, never installed Python, and don't know what a "virtual environment" is. Every step is spelled out. It will take about 15–20 minutes the first time.

If you get stuck, see the [Troubleshooting](#troubleshooting) section at the bottom before giving up — the most common problems have simple fixes.

---

## Table of Contents

1. [What you're about to do](#1-what-youre-about-to-do)
2. [Install Python](#2-install-python)
3. [Get the PYRom Manager files onto your computer](#3-get-the-pyrom-manager-files-onto-your-computer)
4. [Open a terminal in the right folder](#4-open-a-terminal-in-the-right-folder)
5. [Create a virtual environment](#5-create-a-virtual-environment)
6. [Install the app's dependencies](#6-install-the-apps-dependencies)
7. [Run the app](#7-run-the-app)
8. [Stopping the app, and running it again later](#8-stopping-the-app-and-running-it-again-later)
9. [Troubleshooting](#troubleshooting)

---

## 1. What you're about to do

PYRom Manager is a **Python program** that runs a small local web server on your own PC, which you then open in your web browser (like Chrome or Edge) at an address like `http://localhost:8000`. Nothing gets uploaded to the internet — it all stays on your machine.

To get there, you'll:
- Install **Python** (the language the app is written in), if you don't already have it.
- Download the PYRom Manager files.
- Use a "terminal" (a text window where you type commands instead of clicking) to set up and start the app. This sounds scary if you've never done it, but you'll only ever need to type the exact commands shown below — copy/paste is fine.

A **terminal** (also called a "command line" or "console") is just a window where you type a line of text and press Enter, instead of clicking icons. On Windows, the terminal program we'll use is called **PowerShell** — it comes built into Windows already, nothing to install.

---

## 2. Install Python

1. Open your web browser and go to **https://www.python.org/downloads/**.
2. Click the big yellow/blue **"Download Python 3.x.x"** button (any version 3.10 or newer works — the site will suggest the latest one, that's fine).
3. Once downloaded, open the installer file (usually in your **Downloads** folder, named something like `python-3.12.x-amd64.exe`).
4. **This is the single most important step:** on the very first installer screen, check the box at the bottom that says:

   ☑ **"Add python.exe to PATH"**

   If you skip this, Windows won't be able to find Python later and you'll get errors. If you forget, you can re-run the installer and choose "Modify" to fix it.

5. Click **"Install Now"** and wait for it to finish.
6. Click **"Close"** when done.

### Verify Python installed correctly

1. Press the **Windows key** on your keyboard, type `PowerShell`, and press Enter. A blue-ish window will open — this is your terminal.
2. Type the following and press Enter:

   ```
   python --version
   ```

3. You should see something like `Python 3.12.4`. If instead you see an error like *"python is not recognized..."*, see [Troubleshooting](#troubleshooting) below.

Keep this PowerShell window open, or just remember how to reopen it — you'll use it again in the next steps.

---

## 3. Get the PYRom Manager files onto your computer

You don't need to know Git for this. The easiest way:

1. Go to **https://github.com/WaxTools/pyrom-manager** in your browser.
2. Click the green **"Code"** button.
3. Click **"Download ZIP"**.
4. Once it's downloaded (check your **Downloads** folder for `pyrom-manager-master.zip` or similar), **right-click** the ZIP file and choose **"Extract All..."**.
5. Choose a location you'll remember — for example, extract it directly to your `Documents` folder, or to `C:\`. Avoid extracting into folders with unusual characters or very long paths.
6. After extracting, you'll have a folder named something like `pyrom-manager-master`. You can rename it to just `pyrom-manager` if you like (right-click → Rename). **Remember the full path to this folder** — you'll need it in the next step. For example: `C:\Users\YourName\Documents\pyrom-manager`.

> **Prefer using Git instead?** If you're comfortable installing [Git for Windows](https://git-scm.com/download/win), you can instead run `git clone https://github.com/WaxTools/pyrom-manager.git` in PowerShell — this makes it easier to pull future updates. Either method works equally well for running the app.

---

## 4. Open a terminal in the right folder

Everything from here happens inside a PowerShell window that is "standing in" the `pyrom-manager` folder you just extracted.

The easiest way to do this:

1. Open **File Explorer** and navigate into your `pyrom-manager` folder (the one containing `romtools.py`, `requirements.txt`, etc.).
2. Click once on the empty white space inside the address bar at the top of the window (or right-click inside the folder), and type `powershell`, then press Enter.
   - Alternative: hold **Shift** and **right-click** inside the folder (on empty space), and choose **"Open PowerShell window here"** (or "Open in Terminal" on newer Windows 11).
3. A PowerShell window opens, already pointed at that folder. You can confirm this by typing:

   ```
   dir
   ```

   and pressing Enter — you should see `romtools.py`, `requirements.txt`, `templates`, etc. listed. If you see those files, you're in the right place.

---

## 5. Create a virtual environment

A **virtual environment** ("venv") is just a private, isolated folder where Python installs this app's specific dependencies, so they don't clash with anything else on your system. Think of it as a clean toolbox just for this app. You only need to create it **once**.

In the same PowerShell window (still inside the `pyrom-manager` folder), type:

```
python -m venv venv
```

Press Enter and wait a few seconds. Nothing dramatic will print — when you get your cursor back, it's done. You'll now see a new folder called `venv` inside your project folder.

Next, **activate** the virtual environment (you'll need to do this every time you open a new terminal to work with this app, but only once per session):

```
venv\Scripts\activate
```

If it worked, your prompt will now show `(venv)` at the start of the line, like:

```
(venv) PS C:\Users\YourName\Documents\pyrom-manager>
```

> **Getting a red "running scripts is disabled" error?** See [Troubleshooting](#troubleshooting) — this is a common one-time Windows setting you need to change.

---

## 6. Install the app's dependencies

With `(venv)` showing at the start of your prompt, type:

```
pip install -r requirements.txt
```

Press Enter. You'll see a bunch of text scroll by as it downloads and installs several packages (FastAPI, Uvicorn, etc.) — this can take 1-2 minutes depending on your internet connection. When it's done, you'll get your prompt back with no red error text.

---

## 7. Run the app

Still in the same PowerShell window, with `(venv)` showing, type:

```
python romtools.py
```

You should see some startup text, ending in something like:

```
Uvicorn running on http://0.0.0.0:8000
```

This means it's working! **Leave this PowerShell window open** — closing it stops the app.

Now open your web browser (Chrome, Edge, Firefox, whatever you normally use) and go to:

```
http://localhost:8000
```

You should see the PYRom Manager interface.

> **Tip:** Once it's set up, you can also just double-click **`run.bat`** inside the project folder to start the app — it does steps 5-7 for you automatically (activating the environment and launching). You'll still need to have done steps 5-6 at least once first.

---

## 8. Stopping the app, and running it again later

- **To stop the app:** click into the PowerShell window running it, and press **Ctrl + C**. Then you can close the window.
- **To run it again later:** you do **not** need to repeat the venv creation or `pip install` steps — those only happen once. Just:
  1. Open the `pyrom-manager` folder and double-click **`run.bat`**, **or**
  2. Open PowerShell in that folder (step 4) and run:
     ```
     venv\Scripts\activate
     python romtools.py
     ```
  3. Open `http://localhost:8000` in your browser again.

---

## Troubleshooting

### "python is not recognized as an internal or external command..."
Python either isn't installed, or wasn't added to PATH during install.
- Close and reopen PowerShell (sometimes it just needs a fresh window after installing).
- If that doesn't help, re-run the Python installer from [step 2](#2-install-python), choose **"Modify"**, and make sure **"Add python.exe to PATH"** is checked. Restart your computer if it still doesn't work afterward.

### Red error: "running scripts is disabled on this system" when activating the venv
This is a Windows security setting (PowerShell's "execution policy"), not a problem with the app. Fix it once, for your user only, by running this in PowerShell:

```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Press Enter, type `Y` if asked to confirm, then try `venv\Scripts\activate` again.

### "pip is not recognized..."
Make sure you see `(venv)` at the start of your prompt first — if you skipped [step 5](#5-create-a-virtual-environment) or the activation didn't work, `pip` won't be found. Re-run `venv\Scripts\activate` and try again.

### Browser shows "can't reach this page" / "connection refused" at localhost:8000
- Make sure the PowerShell window running `python romtools.py` is still open and doesn't show an error.
- Check the terminal output for a line saying which port it's actually running on — it should say `8000`.
- Make sure you typed `localhost:8000`, not `localhost.8000` or similar typos.

### "Address already in use" / "port 8000 already in use"
Something else on your PC is already using port 8000 (maybe another copy of this app is already running). Close any other PowerShell windows that might be running `romtools.py`, or restart your computer, then try again.

### The window flashes and closes immediately when double-clicking `run.bat`
This usually means an error happened too fast to read. Instead, open PowerShell manually (step 4) and run the commands from steps 5-7 by hand — the error message will stay visible so you can read what went wrong (often it means step 5/6 haven't been done yet in that folder).

### I extracted the ZIP but there's a folder inside a folder
Some ZIP extractors create `pyrom-manager-master\pyrom-manager-master\...`. Make sure you `cd`/navigate into the folder that directly contains `romtools.py`, not one level above it.

### Still stuck?
Open an issue at **https://github.com/WaxTools/pyrom-manager/issues** with:
- The exact command you ran
- The exact error text (copy/paste it, don't paraphrase)
- Your Windows version and Python version (`python --version`)
