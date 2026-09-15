# Spotify Player for the Hermes desktop status bar

A compact Spotify player that lives in the Hermes desktop status bar: play/pause,
next, previous, and a live progress bar.

**No developer app, no client ID, no OAuth consent.** On Windows the plugin drives
the Spotify desktop app through the OS media-session API (SMTC), which the Spotify
app publishes for free. Having Spotify open and logged in *is* the setup.

![status bar](https://img.shields.io/badge/Hermes-desktop%20plugin-8A2BE2)

## What you get

- **In the bar:** play/pause, the current track and artist, and skip-next.
- **A popover** with title/artist, elapsed / total time, prev / play / next, and
  *Open in Spotify*.
- Progress interpolates locally between polls, so it moves smoothly.

With the optional Spotify connection (below) you additionally get album artwork,
the device name, and a working volume slider.

## Requirements

- Hermes Agent with the desktop app.
- **Windows** for the zero-setup path (SMTC is a Windows API).
- The **Spotify desktop app open and logged in**. That's it.
- A Spotify **Premium** account for anything that changes playback (play, pause,
  skip). Free accounts can only be watched, and this plugin is mostly controls.

## Install

**One click** (opens the app's install confirmation):

```
hermes://plugin/install?repo=KKaramaligkas/hermes-spotify-statusbar&enable=1
```

**Or from the CLI:**

```bash
hermes plugins install KKaramaligkas/hermes-spotify-statusbar
hermes plugins enable spotify-statusbar
```

**Or by hand:** copy this repository into `$HERMES_HOME/plugins/spotify-statusbar/`.

### Two steps people miss

1. **Restart the desktop app.** The plugin's backend routes (`/api/plugins/spotify-statusbar/…`)
   are mounted once, at backend start. Enabling the plugin while a backend is already
   running does *not* mount them, so the player silently renders nothing. Opening a new
   chat reuses the same backend — you need a real app restart.
2. **Toggle it on.** A plugin's desktop half is deliberately opt-in: it appears in
   **Settings → Plugins** but stays off until you flip it.

## Two providers

The backend picks the best available source per request:

| Provider | Needs | Gives |
|---|---|---|
| **local** (default) | Spotify desktop app open — nothing else | title, artist, album, position/duration, play/pause, next, previous, seek |
| **webapi** | `hermes auth spotify` (Spotify developer app) | everything above **plus** album artwork, the target device name, and volume |

The local provider is what makes the plugin installable by anyone. The Web API one is
strictly richer, so it wins whenever Spotify is connected — force the local path with
`HERMES_SPOTIFY_STATUSBAR_PROVIDER=local`.

**The local provider does not fake what it cannot do:** SMTC exposes no volume and no
device picker, so the volume slider hides itself and playback reports the source as
*"Via the Spotify desktop app"* rather than showing dead controls.

### How the local provider works

`powershell.exe` startup costs ~250 ms, which would dominate a 5 s poll, so the
PowerShell side runs as **one long-lived worker** (`dashboard/smtc_worker.ps1`) that
reads a JSON request per line and writes a JSON reply per line. Warm round trips are
1–2 ms. The worker is spawned lazily, restarted if it dies, and filtered to the
Spotify app so a YouTube tab never shows up as Spotify.

## How it works

A unified Hermes plugin package — the agent half provides a backend, the desktop half
draws the status bar UI:

```
plugin.yaml
dashboard/
  manifest.json       # { "name": "spotify-statusbar", "api": "plugin_api.py" }
  plugin_api.py       # GET /now  ·  POST /command   (provider selection)
  smtc.py             # the local media-session provider
  smtc_worker.ps1     # its long-lived PowerShell worker
desktop/
  plugin.js           # the status bar contribution
```

`plugin_api.py` never raises for a missing login, a closed Spotify, or a bad action:
everything comes back as `{ok: false, error}` or `{available: false}` data, because a
500 in a widget renders as a vanishing widget.

| Command | local | webapi |
|---|---|---|
| `toggle` | `TryTogglePlayPauseAsync` | reads state, then play or pause |
| `play` / `pause` | `TryPlayAsync` / `TryPauseAsync` | `PUT /me/player/play` (transfer fallback) / `pause` |
| `next` / `previous` | `TrySkipNextAsync` / `TrySkipPreviousAsync` | `POST /me/player/next` \| `/previous` |
| `seek` | `TryChangePlaybackPositionAsync` | `PUT /me/player/seek` |
| `volume` | refused, with a reason | `PUT /me/player/volume` |

### Positioning

The status bar is a `justify-between` row with two content-sized clusters and
unreachable space between them, so `order` cannot place anything in the middle. This
plugin registers in the left cluster and uses a small scoped CSS rule
(`[data-slot="statusbar"] > div:first-child { flex: 1 1 auto }` plus
`margin-inline: auto`) to centre itself in that space. The rule is injected with the
plugin's own `<style>` element and removed on dispose, so disabling the plugin
restores the stock bar.

### Troubleshooting

| Symptom | Cause |
|---|---|
| Nothing appears | Desktop half not toggled on in Settings → Plugins |
| Nothing appears, and Spotify is open | Backend started before the plugin was enabled → restart the app |
| Appears then vanishes | `plugin.js` threw at render — a toast names the failure |
| "Via the Spotify desktop app" and no volume | Expected: the local provider has no volume control |
| Nothing at all on macOS/Linux | The local provider is Windows-only; connect Spotify (`hermes auth spotify`) to use the Web API path |
| A browser tab shows as now-playing | Should not happen — the worker filters to the Spotify app. Please report it |

Every command is logged to `$HERMES_HOME/logs/spotify-statusbar.jsonl` — useful when
something moved your volume and you want to know what called it.

## License

MIT — see [LICENSE](LICENSE).
