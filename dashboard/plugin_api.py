"""Backend for the spotify-statusbar desktop player.

Mounted at /api/plugins/spotify-statusbar/.

Two providers, chosen at request time:

- **webapi** — used when Spotify is connected via `hermes auth spotify`. Full
  features (multi-device, volume, artwork, seek) through the Spotify Web API.
- **local** — used otherwise. Windows' media session API (SMTC) reads the
  Spotify desktop app's own session, so it needs NO developer app, NO client id
  and no consent flow: just Spotify open and logged in on this machine.
  Everything else is hidden rather than faked.

Routes never raise for a missing login or an unplayable state — they return a
shaped result the player can render. A 500 here would surface as a broken
widget, so failures become data.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter

router = APIRouter()


def _load_sibling(name: str):
    """Import a sibling .py from this directory, or None if that fails.

    The host mounts THIS file with importlib.spec_from_file_location under a
    synthetic module name, so this module is not part of a package and
    `from . import x` raises "attempted relative import with no known parent
    package". Mirror the host's own loader, and register in sys.modules before
    exec so annotations resolve — but under a plugin-specific prefix, so a
    generic name like `smtc` cannot collide with another plugin's module.
    """
    try:
        import importlib.util
        import sys
        from pathlib import Path

        module_name = f"hermes_dashboard_plugin_spotify_statusbar_{name}"
        cached = sys.modules.get(module_name)
        if cached is not None:
            return cached

        path = Path(__file__).with_name(f"{name}.py")
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        return module
    except Exception:
        # A missing/broken sibling must only disable that provider, never break
        # the module import (a failed import means NO routes get mounted).
        return None


smtc = _load_sibling("smtc")

# Actions that mutate playback need Premium; Spotify answers 403 otherwise and
# the client's friendly-error mapping turns that into a readable message.
_TRANSPORT_PATHS = {
    "next": ("POST", "/me/player/next"),
    "previous": ("POST", "/me/player/previous"),
}


def _log(entry: Dict[str, Any]) -> None:
    """Append one JSON line per command to <hermes home>/logs/spotify-statusbar.jsonl.

    Transport control is a side effect on someone else's audio, so an
    unattributed volume change is indistinguishable from a bug in this plugin
    without a record of who called what. Never raises.
    """
    try:
        from hermes_constants import get_hermes_home

        path = get_hermes_home() / "logs" / "spotify-statusbar.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {**entry, "ts": datetime.now(timezone.utc).isoformat()}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


# -- provider selection ------------------------------------------------------


def _connected() -> bool:
    """True when `hermes auth spotify` has stored tokens."""
    try:
        from hermes_cli.auth import get_auth_status

        return bool(get_auth_status("spotify").get("logged_in"))
    except Exception:
        return False


def _preferred_provider() -> Optional[str]:
    """Which provider to use, honouring an explicit override.

    Default precedence is webapi-when-connected: it is strictly richer, and the
    local provider exists so the plugin works with zero setup, not to replace the
    full integration. Force the local path with
    HERMES_SPOTIFY_STATUSBAR_PROVIDER=local.
    """
    override = (os.environ.get("HERMES_SPOTIFY_STATUSBAR_PROVIDER") or "").strip().lower()
    if override in {"local", "webapi"}:
        return override
    if _connected():
        return "webapi"
    return "local" if (smtc is not None and smtc.available()) else None


def _unavailable(reason: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "provider": None,
        "available": False,
        "playing": False,
        "error": reason,
    }


def _empty(provider: str) -> Dict[str, Any]:
    """Provider works, but there is nothing to control right now."""
    return {
        "ok": True,
        "provider": provider,
        "available": False,
        "playing": False,
        "error": None,
    }


# -- webapi provider helpers -------------------------------------------------


def _client():
    from plugins.spotify.client import SpotifyClient

    return SpotifyClient()


def _artist_line(track: Dict[str, Any]) -> str:
    return ", ".join(a.get("name", "") for a in (track.get("artists") or []) if a.get("name"))


def _devices(client) -> list:
    try:
        return (client.request("GET", "/me/player/devices") or {}).get("devices") or []
    except Exception:
        return []


def _active_or_first_device(client) -> Optional[Dict[str, Any]]:
    """The device playback should target: the active one, else the first visible."""
    devices = _devices(client)
    return next((d for d in devices if d.get("is_active")), None) or (devices[0] if devices else None)


def _webapi_now() -> Dict[str, Any]:
    try:
        client = _client()
    except Exception as exc:
        return _unavailable(f"Spotify is not reachable ({type(exc).__name__})")

    try:
        state = client.get_playback_state()
    except Exception as exc:
        return _unavailable(f"{type(exc).__name__}: {exc}")

    # get_playback_state returns a sentinel dict on 204 (nothing playing).
    if not state or state.get("empty") or state.get("status_code") == 204:
        return _empty("webapi")

    item = state.get("item") or {}
    if not item:
        return _empty("webapi")

    album = item.get("album") or {}
    images = album.get("images") or []
    device = state.get("device") or {}
    volume = device.get("volume_percent")

    return {
        "ok": True,
        "provider": "webapi",
        "available": True,
        "playing": bool(state.get("is_playing")),
        "title": item.get("name") or "",
        "artists": _artist_line(item),
        "album": album.get("name") or "",
        "image": images[-1].get("url") if images else None,
        "uri": item.get("uri") or "",
        "url": (item.get("external_urls") or {}).get("spotify") or "",
        "position_ms": state.get("progress_ms") or 0,
        "duration_ms": item.get("duration_ms") or 0,
        "shuffle": bool(state.get("shuffle_state")),
        "repeat": state.get("repeat_state") or "off",
        "device": device.get("name") or "",
        "volume": volume,
        "has_volume": bool(device.get("supports_volume")) and volume is not None,
        "supports_next": True,
        "supports_prev": True,
        "can_seek": True,
        "error": None,
    }


def _webapi_command(action: str, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        client = _client()
    except Exception as exc:
        return {"ok": False, "action": action, "error": f"Spotify is not connected ({type(exc).__name__})"}

    try:
        if action == "toggle":
            state = client.get_playback_state()
            playing = bool(state and not state.get("empty") and state.get("is_playing"))
            action = "pause" if playing else "play"

        if action == "pause":
            # Pausing something that is not playing is a no-op, not a failure.
            # Spotify 403s this ("no active device"/Premium copy) and reporting
            # that to a Premium user is actively misleading.
            state = client.get_playback_state()
            if not state or state.get("empty") or not state.get("is_playing"):
                return {"ok": True, "action": "pause", "noop": True}
            client.request("PUT", "/me/player/pause")
            return {"ok": True, "action": "pause"}

        if action == "play":
            device = _active_or_first_device(client)
            if not device:
                return {
                    "ok": False,
                    "action": "play",
                    "error": "No Spotify device available — open Spotify first.",
                }
            if device.get("is_active"):
                try:
                    client.request("PUT", "/me/player/play")
                    return {"ok": True, "action": "play", "device": device.get("name")}
                except Exception:
                    # An "active" device with no loaded session 403s here; the
                    # transfer is the path that actually starts audio.
                    pass
            client.request("PUT", "/me/player", json_body={"device_ids": [device["id"]], "play": True})
            return {"ok": True, "action": "play", "device": device.get("name")}

        if action in _TRANSPORT_PATHS:
            method, path = _TRANSPORT_PATHS[action]
            client.request(method, path)
            return {"ok": True, "action": action}

        if action == "seek":
            position = max(0, int(body.get("position_ms") or 0))
            client.request("PUT", "/me/player/seek", params={"position_ms": position})
            return {"ok": True, "action": "seek"}

        if action == "volume":
            percent = max(0, min(100, int(body.get("volume_percent") or 0)))
            client.request("PUT", "/me/player/volume", params={"volume_percent": percent})
            return {"ok": True, "action": "volume", "volume_percent": percent}

        return {"ok": False, "action": action, "error": f"unknown action: {action}"}
    except Exception as exc:
        return {"ok": False, "action": action, "error": str(exc)}


# -- routes ------------------------------------------------------------------


@router.get("/now")
async def now() -> dict:
    """Current playback, shaped for the player.

    Always 200. `available: false` means there is nothing to control right now
    (Spotify not running, or not connected) and `provider` names which backend
    answered — the renderer uses both to decide what to draw.
    """
    provider = _preferred_provider()
    if provider is None:
        return _unavailable(
            "Local control needs the Spotify desktop app on Windows; "
            "run `hermes auth spotify` for the full Web API integration."
        )
    if provider == "webapi":
        return _webapi_now()
    if smtc is None:
        return _unavailable("The local media-session provider failed to load on this host.")
    return smtc.provider().now()


@router.post("/command")
async def command(body: dict) -> dict:
    """Transport control: toggle / play / pause / next / previous / seek / volume.

    Returns `{ok, action, ...}` — a failure is a shaped result, never an
    exception, so the player can show a message instead of disappearing.
    """
    action = str((body or {}).get("action") or "").strip().lower()
    if not action:
        return {"ok": False, "error": "missing action"}

    provider = _preferred_provider()
    if provider == "webapi":
        result = _webapi_command(action, body or {})
    elif provider == "local":
        result = (
            smtc.provider().command(action, body or {})
            if smtc is not None
            else {"ok": False, "action": action, "error": "the local media-session provider failed to load"}
        )
    else:
        result = {"ok": False, "action": action, "error": "no Spotify provider is available"}

    _log(
        {
            "caller": "desktop-player",
            "provider": provider,
            "requested": (body or {}).get("action"),
            "action": result.get("action"),
            "ok": result.get("ok"),
            "error": result.get("error"),
            "volume_percent": (body or {}).get("volume_percent"),
        }
    )
    return result