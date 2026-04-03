"""
sync_client.py
Core synchronization logic for the PPSSPP cloud sync macOS client.

Responsibilities:
  - Zip / unzip individual game save folders.
  - Calculate the MD5 hash of a zip file.
  - Communicate with the FastAPI backend (status, download, upload).
  - Maintain a local sync_state.json file to track sync state per game.
"""

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_SAVE_PATH: Path = Path("~/.config/ppsspp/PSP/SAVEDATA").expanduser()
SYNC_STATE_FILE: Path = DEFAULT_SAVE_PATH / "sync_state.json"

# ---------------------------------------------------------------------------
# Backend configuration  (override via environment variables if needed)
# ---------------------------------------------------------------------------

BACKEND_URL: str = os.environ.get("PPSSPP_CLOUD_BACKEND", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Zip / Unzip helpers
# ---------------------------------------------------------------------------


def zip_game_save(game_id: str, save_path: Path = DEFAULT_SAVE_PATH) -> io.BytesIO:
    """Compress the save folder for *game_id* into an in-memory ZIP archive.

    Args:
        game_id: The name of the game's save folder inside SAVEDATA.
        save_path: The root SAVEDATA directory.

    Returns:
        A BytesIO buffer containing the zip data, seeked back to position 0.

    Raises:
        FileNotFoundError: If the save folder for the game does not exist.
    """
    game_folder = save_path / game_id
    if not game_folder.is_dir():
        raise FileNotFoundError(f"Save folder not found: {game_folder}")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in game_folder.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(save_path)
                zf.write(file_path, arcname=str(arcname))

    buffer.seek(0)
    return buffer


def unzip_game_save(zip_data: bytes, save_path: Path = DEFAULT_SAVE_PATH) -> None:
    """Extract a ZIP archive of save data into the SAVEDATA directory.

    Existing files will be overwritten so the local save matches the cloud
    version exactly.

    Args:
        zip_data: Raw bytes of the zip archive received from the backend.
        save_path: The root SAVEDATA directory.
    """
    save_path.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        zf.extractall(save_path)


# ---------------------------------------------------------------------------
# MD5 helpers
# ---------------------------------------------------------------------------


def md5_of_buffer(buffer: io.BytesIO) -> str:
    """Return the lowercase hex MD5 digest of a BytesIO buffer.

    The buffer position is restored to its original location after reading.

    Args:
        buffer: An in-memory buffer (e.g. produced by :func:`zip_game_save`).

    Returns:
        Lowercase hexadecimal MD5 string.
    """
    pos = buffer.tell()
    hasher = hashlib.md5()
    for chunk in iter(lambda: buffer.read(65536), b""):
        hasher.update(chunk)
    buffer.seek(pos)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Backend API calls
# ---------------------------------------------------------------------------


def get_sync_status(game_id: str) -> dict:
    """Query the backend for the sync status of a game.

    Calls ``GET /sync/{game_id}/status``.

    Returns:
        Parsed JSON response dict.  Expected keys: ``cloud_version`` (int),
        ``cloud_hash`` (str), ``updated_at`` (str ISO-8601).

    Raises:
        requests.HTTPError: On a non-2xx response.
    """
    url = f"{BACKEND_URL}/sync/{game_id}/status"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.json()


def download_save(game_id: str) -> bytes:
    """Download the latest save zip for a game from the backend.

    Calls ``GET /sync/{game_id}/download``.

    Returns:
        Raw bytes of the ZIP archive.

    Raises:
        requests.HTTPError: On a non-2xx response.
    """
    url = f"{BACKEND_URL}/sync/{game_id}/download"
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def upload_save(game_id: str, zip_buffer: io.BytesIO, md5: str) -> dict:
    """Upload a zipped save to the backend.

    Calls ``POST /sync/{game_id}/upload`` with the archive as a multipart
    file upload and the MD5 checksum as a form field.

    Args:
        game_id:    Identifier for the game (folder name).
        zip_buffer: In-memory buffer containing the ZIP data, seeked to 0.
        md5:        Hex MD5 digest of *zip_buffer* for integrity verification.

    Returns:
        Parsed JSON response dict from the backend.

    Raises:
        requests.HTTPError: On a non-2xx response.
    """
    url = f"{BACKEND_URL}/sync/{game_id}/upload"
    files = {"file": (f"{game_id}.zip", zip_buffer, "application/zip")}
    data = {"md5": md5}
    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Sync state management
# ---------------------------------------------------------------------------


def load_sync_state() -> dict:
    """Load the local sync state from ``sync_state.json``.

    The state file lives inside the SAVEDATA folder and records the last known
    hash and version for every synced game so the client can detect drift
    without trusting filesystem timestamps.

    Returns:
        Dict mapping game_id to ``{"last_sync_hash": str, "cloud_version": int}``.
        Returns an empty dict if the file does not exist or is corrupt.
    """
    if not SYNC_STATE_FILE.exists():
        return {}
    try:
        with SYNC_STATE_FILE.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def save_sync_state(state: dict) -> None:
    """Persist *state* to ``sync_state.json`` inside the SAVEDATA folder.

    Args:
        state: The full state dict (as returned by :func:`load_sync_state`).
    """
    SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SYNC_STATE_FILE.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def update_game_state(game_id: str, last_sync_hash: str, cloud_version: int) -> None:
    """Update the state entry for a single game and persist the state file.

    Args:
        game_id:         Identifier for the game.
        last_sync_hash:  MD5 hex digest of the last successfully synced zip.
        cloud_version:   Version number returned by the backend after the sync.
    """
    state = load_sync_state()
    state[game_id] = {
        "last_sync_hash": last_sync_hash,
        "cloud_version": cloud_version,
    }
    save_sync_state(state)


# ---------------------------------------------------------------------------
# High-level sync operations
# ---------------------------------------------------------------------------


def sync_down(game_id: str, save_path: Path = DEFAULT_SAVE_PATH) -> bool:
    """Download and apply the latest cloud save for *game_id* if it is newer.

    The function compares the cloud version with the locally tracked version.
    If the cloud has a newer version the save is downloaded and extracted.

    Args:
        game_id:   Identifier for the game (folder name inside SAVEDATA).
        save_path: The root SAVEDATA directory.

    Returns:
        ``True`` if a new save was downloaded and applied, ``False`` otherwise.
    """
    try:
        status = get_sync_status(game_id)
    except requests.RequestException:
        return False

    cloud_version: int = status.get("cloud_version", 0)
    cloud_hash: Optional[str] = status.get("cloud_hash")

    state = load_sync_state()
    local_entry = state.get(game_id, {})
    local_version: int = local_entry.get("cloud_version", -1)

    if cloud_version <= local_version:
        return False

    try:
        zip_data = download_save(game_id)
    except requests.RequestException:
        return False

    unzip_game_save(zip_data, save_path)

    # Use the hash reported by the server (or recompute from downloaded data).
    applied_hash = cloud_hash or hashlib.md5(zip_data).hexdigest()
    update_game_state(game_id, applied_hash, cloud_version)
    return True


def sync_up(game_id: str, save_path: Path = DEFAULT_SAVE_PATH) -> bool:
    """Zip and upload the local save for *game_id* if it has changed.

    The function hashes the local zip and compares it with the last known
    sync hash.  If the hashes differ (i.e. the user saved the game), the
    new archive is uploaded.

    Args:
        game_id:   Identifier for the game (folder name inside SAVEDATA).
        save_path: The root SAVEDATA directory.

    Returns:
        ``True`` if the save was uploaded successfully, ``False`` otherwise.
    """
    try:
        zip_buffer = zip_game_save(game_id, save_path)
    except FileNotFoundError:
        return False

    current_hash = md5_of_buffer(zip_buffer)

    state = load_sync_state()
    local_entry = state.get(game_id, {})
    last_hash: Optional[str] = local_entry.get("last_sync_hash")

    if current_hash == last_hash:
        return False

    try:
        result = upload_save(game_id, zip_buffer, current_hash)
    except requests.RequestException:
        return False

    new_version: int = result.get("cloud_version", local_entry.get("cloud_version", 0) + 1)
    update_game_state(game_id, current_hash, new_version)
    return True


def list_local_games(save_path: Path = DEFAULT_SAVE_PATH) -> list[str]:
    """Return a list of game IDs (sub-folder names) found in *save_path*.

    Args:
        save_path: The root SAVEDATA directory.

    Returns:
        Sorted list of directory names.  Returns an empty list if
        *save_path* does not exist.
    """
    if not save_path.is_dir():
        return []
    return sorted(
        entry.name
        for entry in save_path.iterdir()
        if entry.is_dir() and entry.name != "__pycache__"
    )


def sync_all_up(save_path: Path = DEFAULT_SAVE_PATH) -> dict[str, bool]:
    """Upload updated saves for every game found in *save_path*.

    Args:
        save_path: The root SAVEDATA directory.

    Returns:
        Dict mapping each game_id to ``True`` (uploaded) or ``False`` (skipped /
        failed).
    """
    results: dict[str, bool] = {}
    for game_id in list_local_games(save_path):
        results[game_id] = sync_up(game_id, save_path)
    return results


def sync_all_down(save_path: Path = DEFAULT_SAVE_PATH) -> dict[str, bool]:
    """Download new cloud saves for every game found in *save_path*.

    Args:
        save_path: The root SAVEDATA directory.

    Returns:
        Dict mapping each game_id to ``True`` (downloaded) or ``False``
        (already up-to-date / failed).
    """
    results: dict[str, bool] = {}
    for game_id in list_local_games(save_path):
        results[game_id] = sync_down(game_id, save_path)
    return results
