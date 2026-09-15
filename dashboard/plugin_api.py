"""Backend for the spotify-statusbar desktop player.

Mounted at /api/plugins/spotify-statusbar/. Runs inside the Hermes backend
process, so it reuses the bundled Spotify plugin's client (token refresh
included) instead of re-implementing auth.

Routes never raise for a missing login or an unplayable state — they return a
shaped result the player can render. A 500 here would surface as a broken
widget, so failures become data.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter

router = APIRouter()


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

# Actions that mutate playback need Premium; Spotify answers 403 otherwise and
# the client's friendly-error mapping turns that into a readable message.
_TRANSPORT_PATHS = {
    "next": ("POST", "/me/player/next"),
    "previous": ("POST", "/me/player/previous"),
}


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


@router.get("/now")
async def now() -> dict:
    """Current playback, shaped for the player.

    Always 200. `logged_in: false` means Spotify auth is missing entirely;
    `playing: false` means auth is fine but nothing is playing (or no device).
    """
    try:
        client = _client()
    except Exception as exc:  # auth missing / not configured
        return {"logged_in": False, "playing": False, "error": f"{type(exc).__name__}"}

    try:
        state = client.get_playback_state()
    except Exception as exc:
        return {"logged_in": True, "playing": False, "error": f"{type(exc).__name__}: {exc}"}

    # get_playback_state returns a sentinel dict on 204 (nothing playing).
    if not state or state.get("empty") or state.get("status_code") == 204:
        return {"logged_in": True, "playing": False}

    item = state.get("item") or {}
    if not item:
        return {"logged_in": True, "playing": False}

    album = item.get("album") or {}
    images = album.get("images") or []
    device = state.get("device") or {}

    return {
        "logged_in": True,
        "playing": bool(state.get("is_playing")),
        "title": item.get("name") or "",
        "artists": _artist_line(item),
        "album": album.get("name") or "",
        "image": images[-1].get("url") if images else None,
        "uri": item.get("uri") or "",
        "url": (item.get("external_urls") or {}).get("spotify") or "",
        "progress_ms": state.get("progress_ms") or 0,
        "duration_ms": item.get("duration_ms") or 0,
        "shuffle": bool(state.get("shuffle_state")),
        "repeat": state.get("repeat_state") or "off",
        "device": device.get("name") or "",
        "device_supports_volume": bool(device.get("supports_volume")),
        "volume": device.get("volume_percent"),
    }


@router.post("/command")
async def command(body: dict) -> dict:
    """Transport control: toggle / play / pause / next / previous / seek / volume.

    Returns `{ok, action, ...}` — a failure is a shaped result, never an
    exception, so the player can show a message instead of disappearing.
    """
    result = await _dispatch(body)
    _log(
        {
            "caller": "desktop-player",
            "requested": (body or {}).get("action"),
            "action": result.get("action"),
            "ok": result.get("ok"),
            "error": result.get("error"),
            "volume_percent": (body or {}).get("volume_percent"),
        }
    )
    return result


async def _dispatch(body: dict) -> dict:
    action = str((body or {}).get("action") or "").strip().lower()
    if not action:
        return {"ok": False, "error": "missing action"}

    try:
        client = _client()
    except Exception as exc:
        return {"ok": False, "error": f"Spotify is not connected ({type(exc).__name__})"}

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

        return {"ok": False, "error": f"unknown action: {action}"}
    except Exception as exc:
        return {"ok": False, "action": action, "error": str(exc)}
