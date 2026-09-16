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

### Recovering from an empty state

The player never just disappears. When there is nothing to control, the chip becomes the
action that fixes it, chosen by what the host can actually do:

| State | Chip shows | Clicking it |
|---|---|---|
| Spotify not running (Windows) | **Open Spotify** | Starts the app — Store AppUserModelId, then the `spotify:` URI, then the `WindowsApps` alias. A no-op if it is already open |
| No player and no account | **Connect Spotify** | Opens the browser to authorize, via a detached `hermes auth spotify login` so nothing blocks |
| Nothing possible | *(nothing)* | — |

After a launch it re-polls at 1.5 s / 3 s / 6 s, because a freshly started app takes a few
seconds to publish a media session.

While you are on the local provider, the popover also shows **"Connect Spotify for artwork
and volume"** — the two things connecting actually adds over the zero-setup path.

`connect` refuses with a clear message rather than hanging when no developer app exists yet:
that path is an interactive wizard, so it tells you to run `hermes auth spotify` in a
terminal instead of spawning something that would wait invisibly.

## If you change Spotify account

The two providers behave very differently here, and it's worth knowing why:

- **Local provider: nothing to do.** It reads the OS media session, which knows nothing
  about accounts — switch account in the Spotify app and the player simply follows. No
  credentials exist to go stale.
- **Web API provider: it speaks for the account that authorised Hermes**, not for this
  machine. So after switching account in the Spotify app, the saved tokens still point at
  the *old* account, and that account has no active device here.

The plugin handles that instead of getting confused:

- If the Web API reports nothing playing while this machine *does* have a session, it falls
  back to the local session and marks the response `account_mismatch`. The popover then
  explains: *"Showing this machine — your connected Spotify account is a different one."*
  You get your real track back, not a bogus "Open Spotify" prompt.
- The popover offers **"Reconnect with a different Spotify account"**, which forces the
  authorization flow. Plain `connect` would no-op in this state, because tokens *do* exist —
  they're just the wrong account's.
- A **revoked** grant (removed at spotify.com/account/apps, or an aged-out refresh token) is
  detected from the failure itself, and the chip offers **"Reconnect Spotify"** rather than
  telling you to open an app that is already open and playing.

The underlying reason all this is needed: `hermes auth status spotify` reports `logged_in`
whenever a refresh token is *stored*, which is a presence check, not a validity check. Dead
tokens keep looking connected, so this plugin treats the API failure as the signal.

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
| No album artwork | Expected with the local provider. Windows exposes the artwork only as a WinRT stream, which Windows PowerShell cannot read without a compiled C# helper — not worth a dependency for a 44px image. Artwork returns with the Spotify connection |
| Nothing at all on macOS/Linux | The local provider is Windows-only; connect Spotify (`hermes auth spotify`) to use the Web API path |
| A browser tab shows as now-playing | Should not happen — the worker filters to the Spotify app. Please report it |

Every command is logged to `$HERMES_HOME/logs/spotify-statusbar.jsonl` — useful when
something moved your volume and you want to know what called it.

## License

MIT — see [LICENSE](LICENSE).
