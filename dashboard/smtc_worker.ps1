# SMTC worker — a long-lived JSON-lines bridge to Windows' media session API.
#
# Spawned ONCE by the plugin backend and kept alive: powershell.exe startup costs
# hundreds of milliseconds, which would dominate a status-bar poll. One process,
# one line of JSON in, one line of JSON out.
#
#   -> {"op":"now"}
#   -> {"op":"cmd","action":"toggle"|"play"|"pause"|"next"|"previous"}
#
# Requires no Spotify account, no developer app, no client id: the OS owns this
# session data because the Spotify desktop app publishes it.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($op, $type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}

$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])

function Get-SpotifySession {
    # Browsers publish media sessions too — match only the Spotify desktop app
    # so a YouTube tab never shows up as "Spotify".
    $mgr.GetSessions() | Where-Object { $_.SourceAppUserModelId -like '*Spotify*' } | Select-Object -First 1
}

function Write-Line($obj) {
    [Console]::Out.WriteLine(($obj | ConvertTo-Json -Compress -Depth 6))
    [Console]::Out.Flush()
}

function Get-Now {
    $s = Get-SpotifySession
    if (-not $s) { return @{ ok = $true; present = $false } }
    try {
        $props = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
        $info = $s.GetPlaybackInfo()
        $tl = $s.GetTimelineProperties()
        $endMs = [int]$tl.EndTime.TotalMilliseconds
        return @{
            ok          = $true
            present     = $true
            title       = [string]$props.Title
            artists     = [string]$props.Artist
            album       = [string]$props.AlbumTitle
            playing     = ([string]$info.PlaybackStatus -eq 'Playing')
            status      = [string]$info.PlaybackStatus
            position_ms = [int]$tl.Position.TotalMilliseconds
            duration_ms = $endMs
            shuffle     = $false
            repeat      = 'off'
            device      = ''
            volume      = $null
            has_volume  = $false
            supports_next = [bool]$info.Controls.IsNextEnabled
            supports_prev = [bool]$info.Controls.IsPreviousEnabled
        }
    } catch {
        return @{ ok = $false; present = $false; error = $_.Exception.Message }
    }
}

while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    $line = $line.Trim()
    if ($line -eq '') { continue }

    try {
        $req = $line | ConvertFrom-Json
    } catch {
        Write-Line @{ ok = $false; error = 'bad json' }
        continue
    }

    if ($req.op -eq 'now') {
        Write-Line (Get-Now)
        continue
    }

    if ($req.op -eq 'launch') {
        # Start the Spotify desktop app. Three rungs, because a Store install, a
        # classic install, and a URI-handler registration each fail differently:
        # the AppUserModelId, then the app-execution alias, then the spotify: URI.
        $ok = $false
        $how = ''
        $aumid = 'SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify'
        try { Start-Process "shell:AppsFolder\$aumid" -ErrorAction Stop; $ok = $true; $how = 'aumid' }
        catch {
            try { Start-Process 'spotify:' -ErrorAction Stop; $ok = $true; $how = 'uri' }
            catch {
                try {
                    $alias = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\spotify.exe'
                    if (Test-Path $alias) { Start-Process $alias -ErrorAction Stop; $ok = $true; $how = 'alias' }
                } catch { }
            }
        }
        # A no-op when the app is already open (it just focuses), so this is safe
        # to call without knowing the current state.
        Write-Line @{ op = 'launch'; ok = $ok; how = $how; action = 'launch' }
        continue
    }

    if ($req.op -eq 'cmd') {
        $s = Get-SpotifySession
        if (-not $s) { Write-Line @{ ok = $false; error = 'no Spotify session'; action = $req.action }; continue }
        $result = $false
        try {
            switch ($req.action) {
                'toggle'   { $result = Await ($s.TryTogglePlayPauseAsync()) ([bool]) }
                'play'     { $result = Await ($s.TryPlayAsync()) ([bool]) }
                'pause'    { $result = Await ($s.TryPauseAsync()) ([bool]) }
                'next'     { $result = Await ($s.TrySkipNextAsync()) ([bool]) }
                'previous' { $result = Await ($s.TrySkipPreviousAsync()) ([bool]) }
                'seek'     { $result = Await ($s.TryChangePlaybackPositionAsync([int64]$req.position_ms)) ([bool]) }
                default    { Write-Line @{ ok = $false; error = "unsupported action: $($req.action)" }; continue }
            }
        } catch {
            Write-Line @{ ok = $false; error = $_.Exception.Message; action = $req.action }
            continue
        }
        # SMTC returns a bare bool; false means the app refused (or the control is
        # unavailable), so surface it rather than claiming success.
        Write-Line @{ ok = [bool]$result; action = $req.action; error = $(if ($result) { $null } else { 'the media session refused the request' }) }
        continue
    }

    Write-Line @{ ok = $false; error = "unknown op: $($req.op)" }
}
