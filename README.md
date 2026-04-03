# cloud-psp-macos

macOS **Menu Bar** client for the PPSSPP Cloud Sync project.  
It acts as both a sync agent and a launcher for PPSSPP, automatically keeping
your PSP save data in sync with the self-hosted backend.

---

## Features

| Feature | Details |
|---------|---------|
| **Sync Now** | Manually upload all locally changed saves to the cloud. |
| **Launch PPSSPP** | Download the latest cloud saves, then open PPSSPP. |
| **Auto-sync** | A background `watchdog` watcher detects when you save in-game and automatically uploads the changed save a few seconds later. |

---

## Requirements

* Python 3.10+
* macOS (the Menu Bar API is macOS-only)
* A running instance of the cloud-psp FastAPI backend server (configure its URL via `PPSSPP_CLOUD_BACKEND`)

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/systimd/cloud-psp-macos.git
cd cloud-psp-macos

# 2. (Recommended) Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt
```

---

## Configuration

By default the client connects to `http://localhost:8000`.  
Set the `PPSSPP_CLOUD_BACKEND` environment variable to point to a remote server:

```bash
export PPSSPP_CLOUD_BACKEND="https://my-server.example.com"
```

---

## Running the app

```bash
python app.py
```

A 🎮 icon will appear in the macOS Menu Bar.  
Click it to access **Sync Now** and **Launch PPSSPP**.

---

## Save data location

The client reads and writes saves from:

```
~/.config/ppsspp/PSP/SAVEDATA/
```

Each game occupies its own sub-folder (e.g. `ULUS10160GODOFWAR`).  
A `sync_state.json` file is maintained inside `SAVEDATA` to track the last
synced hash and cloud version for each game.

---

## Packaging as a standalone `.app` (py2app)

You can bundle the client into a native macOS application using
[py2app](https://py2app.readthedocs.io/):

```bash
# 1. Install py2app
pip install py2app

# 2. Create a minimal setup.py
cat > setup.py << 'EOF'
from setuptools import setup

APP = ['app.py']
OPTIONS = {
    'argv_emulation': False,
    'packages': ['rumps', 'watchdog', 'requests'],
    'plist': {
        'LSUIElement': True,          # hide from the Dock
        'CFBundleName': 'PPSSPP Sync',
        'CFBundleShortVersionString': '1.0.0',
    },
}

setup(
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
EOF

# 3. Build the .app bundle
python setup.py py2app

# The finished bundle will be inside the dist/ folder:
open dist/
```

> **Tip:** Use `python setup.py py2app -A` (alias mode) during development for
> a faster, non-standalone build that still lets you edit source files
> without rebuilding.

---

## Project structure

```
cloud-psp-macos/
├── app.py            # Menu Bar application (entry point)
├── sync_client.py    # Core sync logic (zip, hash, API calls, state)
├── requirements.txt  # Python dependencies
└── README.md         # This file
```