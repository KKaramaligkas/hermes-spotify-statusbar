/**
 * spotify-statusbar — a compact Spotify player for the desktop status bar.
 *
 * Placement mirrors the bundled Radio plugin, which is the reference for
 * "a player in the status bar": both contribute to `statusBar.right` with a
 * low `order`, so they occupy the same inline spot between the bar's flex gap
 * and the right-aligned system readouts. (The bar has only left/right
 * clusters — there is no center area; `order` is what puts a player there.)
 *
 * Data + transport come from this plugin's own backend namespace:
 *   GET  /api/plugins/spotify-statusbar/now      -> playback state
 *   POST /api/plugins/spotify-statusbar/command  -> toggle/next/previous/seek/volume
 * which reuses the bundled Spotify client, so token refresh stays in one place.
 *
 * Plain ESM, loaded uncompiled: UI is jsx() calls, not JSX syntax.
 */

import {
  Button,
  GlyphSpinner,
  haptic,
  host,
  icons,
  Popover,
  PopoverContent,
  PopoverTrigger,
  STATUSBAR_AREAS,
  Tip,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'spotify-statusbar'
const POLL_MS = 5000

// `ctx` is only in scope inside register(); components read through these.
let apiRequest = null
let postCommand = null
let openExternal = null

const CSS = `
/* The bar wraps two content-sized clusters (left / right) with justify-between.
   Giving the LEFT cluster the free space is what makes a middle position
   expressible at all; without it the player can only sit hard against the left
   items or the right cluster. Scoped to the statusbar slot, and removed on
   plugin dispose. */
[data-slot="statusbar"] > div:first-child{flex:1 1 auto}
/* Center the player in the space the cluster now spans. Both selectors are
   needed because the contribution may be mounted bare or behind a boundary
   wrapper, depending on how the host renders item.render. */
[data-slot="statusbar"] > div:first-child > *:has(.hermes-spotify-bar),
[data-slot="statusbar"] .hermes-spotify-bar{margin-inline:auto}
.hermes-spotify-bar{display:flex;align-items:center;gap:2px;height:100%;min-width:0;color:var(--ui-text-tertiary)}
.hermes-spotify-bar .hermes-spotify-action{width:16px;height:16px;padding:0;flex-shrink:0}
.hermes-spotify-action-icon{display:flex;align-items:center;justify-content:center;width:12px;height:12px;flex-shrink:0;overflow:hidden}
.hermes-spotify-next{display:flex;align-items:center;width:12px;height:12px}
.hermes-spotify-bar .spotify-name{max-width:150px;min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;text-align:left;white-space:nowrap}
.hermes-spotify-bar[data-playing=true] .spotify-name{color:var(--ui-text-secondary)}
.hermes-spotify-panel{width:292px;max-width:calc(100vw - 24px);padding:8px}
.hermes-spotify-head{display:flex;align-items:center;gap:8px;min-width:0}
.hermes-spotify-art{width:44px;height:44px;flex-shrink:0;border-radius:4px;object-fit:cover;background:var(--chrome-action-hover)}
/* No artwork from the local provider (see smtc.py): show a glyph so the slot
   reads as "art not available" rather than as a broken/blank image. */
.hermes-spotify-art-empty{display:flex;align-items:center;justify-content:center;color:var(--ui-text-quaternary)}
.hermes-spotify-head-copy{min-width:0;flex:1}
.hermes-spotify-title{display:block;font-size:12px;line-height:17px;color:var(--ui-text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hermes-spotify-artist{display:block;font-size:11px;line-height:16px;color:var(--ui-text-tertiary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hermes-spotify-progress{margin-top:8px;height:3px;border-radius:2px;background:var(--chrome-action-hover);overflow:hidden}
.hermes-spotify-progress-fill{height:100%;background:var(--ui-accent)}
.hermes-spotify-times{display:flex;justify-content:space-between;margin-top:3px;font-size:10px;line-height:14px;color:var(--ui-text-quaternary)}
.hermes-spotify-meta{display:flex;align-items:center;gap:6px;margin-top:6px;font-size:11px;line-height:16px;color:var(--ui-text-tertiary);min-width:0}
.hermes-spotify-meta-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hermes-spotify-volume{display:flex;align-items:center;gap:6px;height:26px;margin-top:4px}
.hermes-spotify-volume input{flex:1;min-width:0;height:3px;accent-color:var(--ui-accent);cursor:pointer}
.hermes-spotify-actions{display:flex;align-items:center;gap:2px;margin-top:6px}
.hermes-spotify-note{font-size:11px;line-height:17px;color:var(--ui-text-secondary);padding:2px 0}
/* Empty-state recovery: when there is no player (or no account), the chip turns
   into the action that fixes it rather than disappearing. */
.hermes-spotify-recovery{display:flex;align-items:center;gap:3px;height:100%;padding:0 4px;font-size:11px;color:var(--ui-text-quaternary);transition:color .15s}
.hermes-spotify-recovery:hover{color:var(--ui-text-secondary)}
.hermes-spotify-connect{margin-top:6px}
`

function fmtTime(ms) {
  const total = Math.max(0, Math.floor((ms || 0) / 1000))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

function NextArrow() {
  return jsxs('span', {
    className: 'hermes-spotify-next',
    'aria-hidden': 'true',
    children: [
      jsx(icons.Play, { style: { width: 7, height: 12, flexShrink: 0 }, fill: 'currentColor' }),
      jsx(icons.Play, { style: { width: 7, height: 12, flexShrink: 0, marginLeft: -2 }, fill: 'currentColor' })
    ]
  })
}

function SmallAction({ label, icon, onClick, busy = false, pressed, disabled = false }) {
  return jsx(Tip, {
    label,
    children: jsx(Button, {
      variant: 'ghost',
      size: 'micro',
      className: 'hermes-spotify-action',
      'aria-label': label,
      'aria-pressed': pressed,
      disabled,
      onClick,
      children: jsx('span', {
        className: 'hermes-spotify-action-icon',
        children: busy ? jsx(GlyphSpinner, { ariaLabel: label }) : jsx(icon, {})
      })
    })
  })
}

/**
 * Shown when there is nothing to control: offers to start Spotify, or to connect
 * an account. A player that vanishes silently leaves the user with no idea what
 * to do next, which is the whole reason this exists.
 */
function RecoveryChip({ action, icon, label, tip }) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)

  const go = () => {
    setBusy(true)
    haptic('tap')
    void (async () => {
      try {
        const result = await postCommand({ action })
        if (result && result.ok === false) {
          host.notify({ kind: 'warning', message: result.error || 'That did not work' })
        } else if (result && result.note) {
          host.notify({ kind: 'info', message: result.note })
        }
        // Launching the app takes seconds to publish a media session, so look
        // again a few times instead of waiting a full poll to show the player.
        for (const delay of [1500, 3000, 6000]) {
          setTimeout(() => void queryClient.invalidateQueries({ queryKey: [ID, 'now'] }), delay)
        }
      } catch (error) {
        host.notify({ kind: 'warning', message: 'Spotify is unreachable' })
      } finally {
        setBusy(false)
        void queryClient.invalidateQueries({ queryKey: [ID, 'now'] })
      }
    })()
  }

  return jsx(Tip, {
    label: tip,
    children: jsxs('button', {
      type: 'button',
      className: 'hermes-spotify-bar hermes-spotify-recovery',
      'aria-label': label,
      onClick: go,
      children: [
        jsx('span', {
          className: 'hermes-spotify-action-icon',
          children: busy ? jsx(GlyphSpinner, { ariaLabel: label }) : jsx(icon, {})
        }),
        jsx('span', { children: label })
      ]
    })
  })
}

function useNow() {
  return useQuery({
    queryKey: [ID, 'now'],
    queryFn: () => apiRequest('/now'),
    refetchInterval: POLL_MS,
    staleTime: 0,
    retry: false
  })
}

function SpotifyBar() {
  const { data } = useNow()
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(null)
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState(null)
  // Hold progress locally so the bar keeps moving between 5s polls.
  const [tick, setTick] = useState(0)
  // Volume is committed on release, so the pending value lives here until the
  // server confirms it. Without this the thumb snaps back to the polled value
  // mid-drag and the next poll would undo the user's edit visually.
  const [volumeDraft, setVolumeDraft] = useState(null)

  const playing = Boolean(data && data.playing)

  useEffect(() => {
    if (!playing) return undefined
    const id = setInterval(() => setTick(value => value + 1), 1000)
    return () => clearInterval(id)
  }, [playing])

  useEffect(() => {
    if (volumeDraft !== null && data && data.volume === volumeDraft) setVolumeDraft(null)
  }, [data, volumeDraft])

  // Every hook above; safe to bail now.
  //
  // `available` means the backend found a live player — with the local
  // (media-session) provider there is no login at all, so this deliberately is
  // not `logged_in`. The `logged_in` / `position_ms` / `has_volume` fallbacks
  // cover exactly one window: plugin.js hot-reloads from disk the moment it is
  // saved, while plugin_api.py only takes effect when the backend restarts.
  // Without them the player disappears during that gap instead of degrading.
  const send = action => extra => {
    setBusy(action)
    setNote(null)
    void (async () => {
      try {
        const result = await postCommand({ action, ...(extra || {}) })
        if (result && result.ok === false) setNote(result.error || 'Spotify rejected that command')
        haptic('tap')
      } catch (error) {
        setNote('Spotify is unreachable')
      } finally {
        setBusy(null)
        void queryClient.invalidateQueries({ queryKey: [ID, 'now'] })
        setTick(0)
      }
    })()
  }

  if (!data) return null

  const ready = data.available !== undefined ? data.available : data.logged_in
  if (!ready) {
    // Nothing to control — offer the way OUT instead of vanishing. "No player"
    // and "no account" need different recoveries, and the backend says which of
    // them this host can actually perform.
    if (data.can_launch) {
      return jsx(RecoveryChip, {
        action: 'launch',
        icon: icons.Play,
        label: 'Open Spotify',
        tip: 'Spotify is not running — click to open it'
      })
    }
    if (data.can_connect) {
      return jsx(RecoveryChip, {
        action: 'connect',
        icon: icons.ExternalLink,
        label: 'Connect Spotify',
        tip: 'Open the browser to connect your Spotify account'
      })
    }
    return null
  }

  const volume = volumeDraft === null ? data.volume : volumeDraft
  const hasTrack = Boolean(data.title)
  const label = hasTrack ? `${data.title} — ${data.artists}` : 'Spotify'
  const canNext = data.supports_next !== false
  const canPrev = data.supports_prev !== false
  const hasVolume = data.has_volume !== undefined ? data.has_volume : data.device_supports_volume
  const elapsedMs = data.position_ms || data.progress_ms || 0

  const progress = data.duration_ms
    ? Math.min(data.duration_ms, elapsedMs + (playing ? tick * 1000 : 0))
    : 0

  const commitVolume = () => {
    if (volumeDraft === null) return
    send('volume')({ volume_percent: volumeDraft })
  }

  return jsxs('div', {
    className: 'hermes-spotify-bar',
    'data-playing': String(playing),
    children: [
      jsx(SmallAction, {
        label: playing ? 'Pause' : 'Play',
        icon: playing ? icons.Pause : icons.Play,
        busy: busy === 'toggle',
        onClick: () => send('toggle')()
      }),

      jsxs(Popover, {
        open,
        onOpenChange: setOpen,
        children: [
          jsx(PopoverTrigger, {
            asChild: true,
            children: jsx(Button, {
              variant: 'ghost',
              size: 'micro',
              'aria-label': `Spotify: ${label}`,
              children: jsx('span', { className: 'spotify-name', children: label })
            })
          }),
          jsx(PopoverContent, {
            side: 'top',
            align: 'end',
            className: 'hermes-spotify-panel',
            'aria-label': 'Spotify',
            children: jsxs('div', {
              children: [
                jsxs('div', {
                  className: 'hermes-spotify-head',
                  children: [
                    data.image
                      ? jsx('img', {
                          className: 'hermes-spotify-art',
                          src: data.image,
                          alt: '',
                          onError: event => {
                            event.currentTarget.style.visibility = 'hidden'
                          }
                        })
                      : jsx('span', {
                          className: 'hermes-spotify-art hermes-spotify-art-empty',
                          children: jsx(icons.AudioLines, {})
                        }),
                    jsxs('span', {
                      className: 'hermes-spotify-head-copy',
                      children: [
                        jsx('span', { className: 'hermes-spotify-title', children: data.title || 'Nothing playing' }),
                        jsx('span', {
                          className: 'hermes-spotify-artist',
                          children: hasTrack ? data.artists || data.album : 'Open Spotify and start a track'
                        })
                      ]
                    })
                  ]
                }),

                hasTrack
                  ? jsxs('div', {
                      children: [
                        jsx('div', {
                          className: 'hermes-spotify-progress',
                          children: jsx('div', {
                            className: 'hermes-spotify-progress-fill',
                            style: { width: `${data.duration_ms ? (progress / data.duration_ms) * 100 : 0}%` }
                          })
                        }),
                        jsxs('div', {
                          className: 'hermes-spotify-times',
                          children: [
                            jsx('span', { children: fmtTime(progress) }),
                            jsx('span', { children: fmtTime(data.duration_ms) })
                          ]
                        })
                      ]
                    })
                  : null,

                data.device || data.provider === 'local'
                  ? jsxs('div', {
                      className: 'hermes-spotify-meta',
                      children: [
                        jsx('span', {
                          className: 'hermes-spotify-meta-text',
                          // Naming the source matters here: the local provider has
                          // no volume and no device picker, and silently missing
                          // controls read as broken rather than unavailable.
                          children: data.device
                            ? `Playing on ${data.device}`
                            : 'Via the Spotify desktop app'
                        })
                      ]
                    })
                  : null,

                hasVolume && data.volume !== null && data.volume !== undefined
                  ? jsx('div', {
                      className: 'hermes-spotify-volume',
                      children: jsx('input', {
                        type: 'range',
                        min: 0,
                        max: 100,
                        // Controlled, and committed only on RELEASE. A range
                        // input fires `input` per intermediate value, so
                        // posting from onChange would fire dozens of volume
                        // writes per drag — Spotify gets stepped in tiny
                        // increments instead of one set.
                        value: volume,
                        'aria-label': 'Volume',
                        onChange: event => setVolumeDraft(Number(event.target.value)),
                        onPointerUp: commitVolume,
                        onKeyUp: commitVolume,
                        onBlur: commitVolume
                      })
                    })
                  : null,

                jsxs('div', {
                  className: 'hermes-spotify-actions',
                  children: [
                    jsx(SmallAction, {
                      label: 'Previous',
                      icon: () => jsx(icons.Play, { style: { transform: 'scaleX(-1)' }, fill: 'currentColor' }),
                      busy: busy === 'previous',
                      disabled: !canPrev,
                      onClick: () => send('previous')()
                    }),
                    jsx(SmallAction, {
                      label: playing ? 'Pause' : 'Play',
                      icon: playing ? icons.Pause : icons.Play,
                      busy: busy === 'toggle',
                      onClick: () => send('toggle')()
                    }),
                    jsx(SmallAction, {
                      label: 'Next',
                      icon: NextArrow,
                      busy: busy === 'next',
                      disabled: !canNext,
                      onClick: () => send('next')()
                    }),
                    jsx('span', { style: { flex: 1, minWidth: 6 } }),
                    data.url
                      ? jsx(Button, {
                          variant: 'ghost',
                          size: 'micro',
                          'aria-label': 'Open in Spotify',
                          onClick: () => {
                            if (openExternal) void openExternal(data.url)
                          },
                          children: jsx(icons.ExternalLink, {})
                        })
                      : null
                  ]
                }),

                // The upgrade path, offered where the user is actually looking:
                // the local provider works without any account, and this is how
                // they discover what connecting adds.
                data.connected === false
                  ? jsx('div', {
                      className: 'hermes-spotify-connect',
                      children: jsx(Button, {
                        variant: 'text',
                        size: 'micro',
                        onClick: () => send('connect')(),
                        children: 'Connect Spotify for artwork and volume'
                      })
                    })
                  : null,

                note ? jsx('div', { className: 'hermes-spotify-note', children: note }) : null
              ]
            })
          })
        ]
      }),

      jsx(SmallAction, {
        label: 'Next',
        icon: NextArrow,
        busy: busy === 'next',
        disabled: !canNext,
        onClick: () => send('next')()
      })
    ]
  })
}

export default {
  id: ID,
  name: 'Spotify Player',
  description: 'Compact Spotify player in the status bar: play/pause, skip, progress and volume.',
  defaultEnabled: false,
  register(ctx) {
    // Wrapped, not stored directly: ctx.rest may rely on its receiver.
    apiRequest = path => ctx.rest(path)
    // ctx.rest defaults to GET; transport control is always a POST.
    postCommand = body => ctx.rest('/command', { method: 'POST', body })
    openExternal = ctx.os && ctx.os.openExternal ? ctx.os.openExternal.bind(ctx.os) : null

    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())

    ctx.register({
      // LEFT cluster, and positioned from CSS rather than by `order`.
      //
      // The bar is `justify-between` with two content-sized clusters (see
      // StatusbarControls): left-aligned and right-aligned, with a flexible gap
      // between. There is no center slot in the SDK (`STATUSBAR_AREAS` is only
      // left|right), so "in the middle of the bar" is expressed by living in the
      // left cluster, letting that cluster absorb the free space (see the CSS
      // rule below), and centering in what it now spans.
      //
      // A high `order` sorts this item last among the left items so the auto
      // margins split the leftover space rather than the cluster's own items.
      id: 'spotify-player',
      area: STATUSBAR_AREAS.left,
      order: 900,
      render: () => jsx(SpotifyBar, {})
    })
  }
}
