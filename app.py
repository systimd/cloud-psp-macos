"""
app.py
macOS Menu Bar application and PPSSPP launcher for cloud save synchronization.

Features:
  - "Sync Now"    – manually trigger an upload of all locally changed saves.
  - "Launch PPSSPP" – sync down the latest cloud saves, then launch PPSSPP.
  - Background watchdog thread that auto-uploads a save a few seconds after
    any file change is detected inside the SAVEDATA folder.

Dependencies:
  pip install rumps watchdog requests
"""

import subprocess
import threading
import time

import rumps

from sync_client import (
    DEFAULT_SAVE_PATH,
    list_local_games,
    sync_all_down,
    sync_all_up,
    sync_down,
    sync_up,
)

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WATCHDOG_AVAILABLE = False


# ---------------------------------------------------------------------------
# Watchdog event handler
# ---------------------------------------------------------------------------

_DEBOUNCE_SECONDS = 3  # wait this long after the last change before syncing


class SaveDataEventHandler(FileSystemEventHandler):
    """Debounced file-system event handler for the SAVEDATA directory.

    When any file inside a game's save folder is created or modified, a
    background timer is (re)started.  Once the timer fires the affected game's
    save is zipped and uploaded.
    """

    def __init__(self) -> None:
        super().__init__()
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def _schedule_upload(self, game_id: str) -> None:
        """Cancel any pending timer for *game_id* and start a fresh one."""
        with self._lock:
            existing = self._timers.get(game_id)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(_DEBOUNCE_SECONDS, self._do_upload, args=(game_id,))
            timer.daemon = True
            timer.start()
            self._timers[game_id] = timer

    def _do_upload(self, game_id: str) -> None:
        """Perform the actual upload (called from the timer thread)."""
        with self._lock:
            self._timers.pop(game_id, None)
        uploaded = sync_up(game_id)
        if uploaded:
            rumps.notification(
                title="PPSSPP Cloud Sync",
                subtitle=game_id,
                message="Save uploaded to cloud ☁️",
            )

    def on_modified(self, event: "FileSystemEvent") -> None:
        if event.is_directory:
            return
        self._handle(event.src_path)

    def on_created(self, event: "FileSystemEvent") -> None:
        if event.is_directory:
            return
        self._handle(event.src_path)

    def _handle(self, path: str) -> None:
        """Derive the game_id from the changed file path and schedule upload."""
        from pathlib import Path

        changed = Path(path)
        try:
            relative = changed.relative_to(DEFAULT_SAVE_PATH)
        except ValueError:
            return

        parts = relative.parts
        if not parts:
            return

        game_id = parts[0]
        if game_id == "sync_state.json":
            return

        self._schedule_upload(game_id)


# ---------------------------------------------------------------------------
# Menu Bar application
# ---------------------------------------------------------------------------


class PPSSPPSyncApp(rumps.App):
    """macOS Menu Bar application for PPSSPP cloud save synchronization."""

    def __init__(self) -> None:
        super().__init__(
            name="PPSSPP Sync",
            title="🎮",
            quit_button="Quit",
        )
        self.menu = [
            rumps.MenuItem("Sync Now", callback=self.sync_now),
            rumps.MenuItem("Launch PPSSPP", callback=self.launch_ppsspp),
            None,  # separator
        ]
        self._observer: "Observer | None" = None
        self._start_watchdog()

    # ------------------------------------------------------------------
    # Menu callbacks
    # ------------------------------------------------------------------

    @rumps.clicked("Sync Now")
    def sync_now(self, _sender: rumps.MenuItem) -> None:
        """Upload all locally changed saves to the cloud."""
        self.title = "⏳"
        results = sync_all_up()
        uploaded = [gid for gid, ok in results.items() if ok]
        self.title = "🎮"
        if uploaded:
            rumps.notification(
                title="PPSSPP Cloud Sync",
                subtitle="Sync complete",
                message=f"Uploaded: {', '.join(uploaded)}",
            )
        else:
            rumps.notification(
                title="PPSSPP Cloud Sync",
                subtitle="Sync complete",
                message="All saves are already up to date.",
            )

    @rumps.clicked("Launch PPSSPP")
    def launch_ppsspp(self, _sender: rumps.MenuItem) -> None:
        """Sync down the latest cloud saves, then launch PPSSPP."""
        self.title = "⬇️"
        results = sync_all_down()
        downloaded = [gid for gid, ok in results.items() if ok]
        self.title = "🎮"
        if downloaded:
            rumps.notification(
                title="PPSSPP Cloud Sync",
                subtitle="Sync complete",
                message=f"Downloaded: {', '.join(downloaded)}",
            )
        subprocess.run(["open", "-a", "PPSSPP"], check=False)

    # ------------------------------------------------------------------
    # Watchdog helpers
    # ------------------------------------------------------------------

    def _start_watchdog(self) -> None:
        """Start the background file-system watcher for SAVEDATA."""
        if not _WATCHDOG_AVAILABLE:
            rumps.notification(
                title="PPSSPP Cloud Sync",
                subtitle="Warning",
                message="watchdog is not installed – auto-sync disabled.",
            )
            return

        DEFAULT_SAVE_PATH.mkdir(parents=True, exist_ok=True)

        event_handler = SaveDataEventHandler()
        self._observer = Observer()
        self._observer.schedule(
            event_handler,
            str(DEFAULT_SAVE_PATH),
            recursive=True,
        )
        self._observer.daemon = True
        self._observer.start()

    def _stop_watchdog(self) -> None:
        """Gracefully stop the file-system watcher."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    app = PPSSPPSyncApp()
    app.run()


if __name__ == "__main__":
    main()
