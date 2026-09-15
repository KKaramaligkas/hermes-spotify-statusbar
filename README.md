# Spotify Player for the Hermes desktop status bar

A compact Spotify player that lives in the Hermes desktop status bar: play/pause,
next, previous, a live progress bar, and a volume slider.

![status bar](https://img.shields.io/badge/Hermes-desktop%20plugin-8A2BE2)

## What you get

- **In the bar:** play/pause, the current track and artist, and skip-next — sized to
  sit alongside the built-in readouts.
- **A popover** with album art, title/artist, elapsed / total time, the target device,
  a volume slider, prev / play / next, and *Open in Spotify*.
- Progress interpolates locally between polls, so it moves smoothly.

It reads and controls playback through Hermes' own Spotify integration, so OAuth
token refresh is handled by Hermes — this plugin never touches your credentials.

## Requirements

- Hermes Agent with the desktop app, and the Spotify toolset enabled
  (`hermes tools` → 🎵 Spotify).
- **Logged in to Spotify:** `hermes auth spotify`. This is the one prerequisite —
  the plugin is dead weight without it (it renders nothing at all when logged out).
- A Spotify **Premium** account for anything that changes playback (play, pause,
  skip, volume) and an **active Spotify Connect device** — open the Spotify app on
  some device first.
- Read-only works on Free accounts, but there is not much to read here.

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

## How it works

A unified Hermes plugin package — the agent half provides a small backend, the desktop
half draws the status bar UI:

```
plugin.yaml
dashboard/
  manifest.json     # { "name": "spotify-statusbar", "api": "plugin_api.py" }
  plugin_api.py     # GET /now  ·  POST /command
desktop/
  plugin.js         # the status bar contribution
```

`plugin_api.py` reuses the Spotify client bundled with Hermes
(`plugins.spotify.client.SpotifyClient`) for authenticated calls, so there is one
OAuth token store and one refresh path. Routes never raise: a missing login, a device
that cannot play, and a bad action all come back as `{ok: false, error}` data, because
a 500 in a widget renders as a vanishing widget.

| Command | Spotify call |
|---|---|
| `toggle` | reads state, then play or pause |
| `pause` | `PUT /me/player/pause` — a no-op success when nothing is playing |
| `play` | `PUT /me/player/play`, falling back to transfer-and-play |
| `next` / `previous` | `POST /me/player/next` \| `/previous` |
| `seek` | `PUT /me/player/seek?position_ms=` |
| `volume` | `PUT /me/player/volume?volume_percent=` |

### Positioning

The status bar is a `justify-between` row with two content-sized clusters (left and
right) and unreachable space between them, so `order` cannot place anything in the
middle. This plugin registers in the left cluster and uses a small scoped CSS rule
(`[data-slot="statusbar"] > div:first-child { flex: 1 1 auto }` plus
`margin-inline: auto`) to centre itself in that space. The rule is injected with the
plugin's own `<style>` element and removed on dispose, so disabling the plugin
restores the stock bar.

### Troubleshooting

| Symptom | Cause |
|---|---|
| Nothing appears | Desktop half not toggled on in Settings → Plugins |
| Appears then vanishes | `plugin.js` threw at render — a toast names the failure |
| Nothing renders, no error | Backend started before the plugin was enabled → restart the app |
| Buttons do nothing | Backend route missing, or a GET sent where a POST was needed |
| "No Spotify device available" | Open the Spotify app so a Connect device exists |

Every command is logged to `$HERMES_HOME/logs/spotify-statusbar.jsonl` — useful when
something moved your volume and you want to know what called it.

## License

MIT — see [LICENSE](LICENSE).
