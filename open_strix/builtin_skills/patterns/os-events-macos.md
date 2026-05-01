# macOS Event Sources

The OS already knows when interesting things happen — lid opens, screen unlocks, USB plugged
in, network joined, app launched. You don't need a poller for any of these; macOS will call
*you*. This file is a tour of the mechanisms and the events you can hang off them.

When in doubt, prefer push (the OS notifies you) over polling. Reserve polling for things
that don't have a notification API — most SQLite-backed apps fall in that bucket.

## The five primitives

Most macOS event-listening reduces to one of these:

1. **`log stream` / `log show`** — the unified logging firehose, filterable by NSPredicate.
   Everything Apple writes goes here.
2. **`launchd` agents** — declarative event triggers. `WatchPaths`, `QueueDirectories`,
   `StartOnMount`, `KeepAlive`, `StartCalendarInterval`. The OS runs your script when the
   condition fires; you write zero listening code.
3. **`pmset -g log`** — power management log, parseable, covers sleep/wake/lid/battery.
4. **`osascript` / JXA** — talk to any scriptable app (Messages, Mail, Safari, Music,
   Calendar, Notes, Reminders). Works for *getting* state and for installing handlers.
5. **`fswatch` / FSEvents** — filesystem changes. Cheaper than polling `ls`.

Add `pyobjc` if you want to subscribe to `NSDistributedNotificationCenter`,
`NSWorkspace`, IOKit, or CoreLocation in real time.

## Power, lid, sleep, wake

`pmset -g log` is the canonical source. Tail it and grep:

* Lid opened / closed
* Display went to sleep / woke
* System slept (and *why* — the wake reason is logged: lid, USB, network, scheduled)
* AC adapter connected / disconnected
* Battery crossed a threshold
* Thermal pressure rising (`pmset -g therm`)
* Scheduled wake fired (you can also schedule them with `pmset schedule`)

`pmset -g log | grep "Wake from"` is shockingly informative.

## Session, lock, login

`NSDistributedNotificationCenter` carries these system-wide:

* `com.apple.screenIsLocked` / `com.apple.screenIsUnlocked`
* `com.apple.screensaver.didstart` / `.didstop`
* `AppleInterfaceThemeChangedNotification` (dark mode flipped)
* `AppleColorPreferencesChangedNotification` (accent color)
* `com.apple.LaunchServices.applicationRegistered` (a new app showed up)

`NSWorkspace` notifications cover login/logout, app launch/quit, volume mount/unmount,
sleep/wake from the user-session perspective.

## Filesystem & launchd

A `LaunchAgent` plist with `WatchPaths` is the laziest event hook on the system — drop a
file in a folder, your script runs. Useful triggers:

* New screenshot saved (watch `defaults read com.apple.screencapture location`)
* New file in `~/Downloads` (auto-classify, auto-file)
* New `.ics` in `~/Library/Calendars/...` (event added)
* New `.eml` in a Mail download folder
* `~/Library/Application Support/CloudDocs` changes (iCloud sync activity)
* A specific dotfile changing (`~/.zshrc` edited)

`StartOnMount=true` runs on every disk arrival — perfect for "back up that USB stick the
moment it shows up."

## Devices & connectivity

* USB plug/unplug — IOKit notifications, or watch `system_profiler SPUSBDataType`
* Bluetooth device connect/disconnect — IOBluetooth notifications
* AirPods switching to *which* device (Continuity)
* Wi-Fi joined a new SSID — `wdutil info` polled, or `airport` event log
* VPN up/down — `scutil --nc list` polled
* Display attached/detached — `NSApplicationDidChangeScreenParametersNotification`
* Camera or mic became active — `lsof` for `AppleCamera`/`coreaudiod` clients, or watch
  the green/orange indicator state in the unified log

## Continuity, Handoff, Universal Clipboard

* AirDrop received a file (watch `~/Downloads`)
* Universal Clipboard delivered something from iPhone (`pbpaste` diff, plus
  `NSPasteboard.changeCount` if you go native)
* Sidecar / Continuity Camera connected
* Handoff offered an activity

## App-data SQLite databases (poll, don't subscribe)

These have no real notification API but the SQLite files are readable. Watch the file's
mtime, then read deltas.

* `~/Library/Messages/chat.db` — new iMessage / SMS
* `~/Library/Application Support/Notification Center/db2/db` — every notification you
  received, system-wide
* `~/Library/Safari/History.db` (Safari) / `~/Library/Application Support/Google/Chrome/...`
* Notes, Reminders, Photos databases (each has its own quirks)

## Spotlight as a live event source (underused)

`mdfind -live "query"` keeps streaming new matches forever. Treat it as a subscription:

* `mdfind -live "kMDItemContentTypeTree == 'public.image' && kMDItemFSCreationDate > $time.today"`
* "Anything mentioning project X across all my files" as a live trigger

## Creative / cursed

* Hot Corners triggered (look in unified log)
* Focus / DND mode changed
* TouchID / FaceID auth event (in unified log)
* Screen Time threshold crossed
* "Picture in Picture" started
* Software Update available — `softwareupdate -l`
* SIP status flipped — `csrutil status`
* New Login Item added (security signal)
* Screen Sharing session started (`ARDAgent` events)
* TCC privacy permission granted to an app (database in `~/Library/Application Support/com.apple.TCC/`)
* Idle time crossed N minutes — `ioreg -c IOHIDSystem | awk '/HIDIdleTime/'`
* Boot time changed (machine rebooted while you weren't looking) — `sysctl kern.boottime`
* A specific Shortcut was run — Shortcuts log in unified logging
* Siri/Dictation invoked

## How to wire it up

For most of these you'll either:

* Run `log stream --predicate '...' --style ndjson` as a long-lived process and parse lines, OR
* Drop a `LaunchAgent` `.plist` in `~/Library/LaunchAgents` whose `WatchPaths` or
  `StartCalendarInterval` triggers your script, OR
* Write a tiny pyobjc daemon that adds an observer to `NSDistributedNotificationCenter`
  or `NSWorkspace.shared.notificationCenter`.

For the unified-log path, the predicate language is the killer feature — you can filter by
subsystem, category, process, message regex. `log stream --predicate 'subsystem ==
"com.apple.powerd"'` is a one-liner sleep/wake monitor.
