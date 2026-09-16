"""Zero-setup local Spotify control via Windows' media session API (SMTC).

Why this exists: Spotify's Web API requires every user to register their own
developer app for a client id. SMTC needs nothing — the Spotify desktop app
publishes its media session to Windows, and the OS exposes it to any process.
So "just have Spotify open and be logged in" is the whole setup.

Cost model: `powershell.exe` startup is ~250 ms, which would dominate a 5 s
status-bar poll, so the PowerShell side runs as ONE long-lived worker that reads
a JSON request per line and writes a JSON reply per line. Warm calls are 1-2 ms.

Limits, stated plainly: SMTC exposes no volume and no device list, and this is
Windows-only. `plugin_api` falls back to the Web API provider when the user has
connected Spotify, and hides what the local provider cannot do.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

WORKER = Path(__file__).with_name("smtc_worker.ps1")

# Keep the console window from flashing on every spawn.
_CREATE_NO_WINDOW = 0x08000000

_CALL_TIMEOUT_S = 8.0


def available() -> bool:
    """True when this provider can run at all on this host."""
    return sys.platform == "win32" and WORKER.is_file()


class SmtcProvider:
    """Owns the worker process. One instance per backend process."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._broken: Optional[str] = None

    # -- worker lifecycle ---------------------------------------------------

    def _spawn(self) -> None:
        self._proc = subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(WORKER),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_CREATE_NO_WINDOW,
        )

    def _ensure(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()
        assert self._proc is not None
        return self._proc

    def _exchange(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """One request/response round trip. Serialised: the worker is stateful."""
        with self._lock:
            proc = self._ensure()
            try:
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
            except Exception as exc:
                # A dead or wedged worker must not wedge the plugin: kill it and
                # let the next call respawn.
                self._kill()
                return {"ok": False, "error": f"media session bridge failed: {type(exc).__name__}"}

        if not line:
            self._kill()
            return {"ok": False, "error": "media session bridge closed unexpectedly"}

        try:
            return json.loads(line)
        except Exception:
            return {"ok": False, "error": "media session bridge returned malformed output"}

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
        except Exception:
            pass

    # -- public surface -----------------------------------------------------

    def now(self) -> Dict[str, Any]:
        if self._broken:
            return {"ok": False, "available": False, "error": self._broken}
        try:
            raw = self._exchange({"op": "now"})
        except Exception as exc:
            return {"ok": False, "available": False, "error": f"{type(exc).__name__}: {exc}"}

        if not raw.get("ok"):
            self._broken = str(raw.get("error") or "media session unavailable")
            return {"ok": False, "available": False, "error": self._broken}

        if not raw.get("present"):
            return {
                "ok": True,
                "provider": "local",
                "available": False,
                "playing": False,
                "error": None,
            }

        return {
            "ok": True,
            "provider": "local",
            "available": True,
            "playing": bool(raw.get("playing")),
            "title": raw.get("title") or "",
            "artists": raw.get("artists") or "",
            "album": raw.get("album") or "",
            # SMTC DOES expose artwork (MediaProperties.Thumbnail) but hands it over as
            # a WinRT stream, and Windows PowerShell cannot read one: OpenReadAsync
            # returns a bare __ComObject with no late-bound methods, and casting it to
            # IInputStream raises "Cannot convert System.__ComObject ... to type
            # IInputStream". Reading it needs a compiled C# helper, which is not worth
            # a dependency for a decorative 44px image. The renderer draws a glyph
            # instead; the Web API provider still supplies real artwork.
            "image": None,
            "uri": "",
            "url": "",
            "position_ms": int(raw.get("position_ms") or 0),
            "duration_ms": int(raw.get("duration_ms") or 0),
            "shuffle": bool(raw.get("shuffle")),
            "repeat": raw.get("repeat") or "off",
            "device": "",
            "volume": None,
            # SMTC has no volume control, so the slider hides itself rather than
            # pretending to work.
            "has_volume": False,
            "supports_next": bool(raw.get("supports_next", True)),
            "supports_prev": bool(raw.get("supports_prev", True)),
            "can_seek": True,
            "error": None,
        }

    def launch(self) -> Dict[str, Any]:
        """Start the Spotify desktop app. Safe when it is already running."""
        if not available():
            return {"ok": False, "action": "launch", "error": "starting Spotify from here is Windows-only"}
        raw = self._exchange({"op": "launch"})
        ok = bool(raw.get("ok"))
        return {
            "ok": ok,
            "action": "launch",
            "how": raw.get("how") or None,
            "error": None if ok else "could not start the Spotify app",
        }

    def command(self, action: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if action == "seek":
            raw = self._exchange({"op": "cmd", "action": "seek", "position_ms": int(body.get("position_ms") or 0)})
            return {"ok": bool(raw.get("ok")), "action": "seek", "error": raw.get("error")}

        if action == "toggle":
            # SMTC's toggle is atomic, so there is no read-then-write race.
            raw = self._exchange({"op": "cmd", "action": "toggle"})
            return {"ok": bool(raw.get("ok")), "action": "toggle", "error": raw.get("error")}

        if action in {"play", "pause", "next", "previous"}:
            raw = self._exchange({"op": "cmd", "action": action})
            return {"ok": bool(raw.get("ok")), "action": action, "error": raw.get("error")}

        if action == "volume":
            # Honest refusal instead of silently doing nothing.
            return {
                "ok": False,
                "action": "volume",
                "error": "Volume needs the Spotify connection (Windows media sessions expose no volume).",
            }

        return {"ok": False, "action": action, "error": f"unsupported action for the local provider: {action}"}


_INSTANCE: Optional[SmtcProvider] = None
_INSTANCE_LOCK = threading.Lock()


def provider() -> SmtcProvider:
    """Process-wide singleton — the worker must not be spawned per request."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = SmtcProvider()
        return _INSTANCE
