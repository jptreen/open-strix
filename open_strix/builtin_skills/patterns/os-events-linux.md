# Linux Event Sources

Linux has the richest event surface of the three desktop OSes — almost everything is
either a file you can `inotify`, a D-Bus signal you can subscribe to, a kernel event you
can `bpftrace`, or a journal entry you can tail. The ecosystem is fragmented, but the
primitives are powerful.

When in doubt, prefer push (the OS notifies you) over polling. D-Bus and `journalctl -f`
together cover most of what an agent wants to react to on a desktop Linux system.

## The five primitives

1. **`journalctl -f`** — the systemd journal as a tailable firehose. Filter by unit,
   priority, syslog identifier, or arbitrary `_FIELD=value`. Most desktop apps write here.
2. **D-Bus signals** — desktop and system events bus. `dbus-monitor`,
   `gdbus monitor --session`, or `busctl monitor`. Almost every interesting desktop event
   is a D-Bus signal.
3. **`udevadm monitor`** — kernel + userspace device events. Plug a USB cable, see it.
   Pair with udev rules to *run a script* declaratively on a specific device class.
4. **`inotifywait` / `fanotify`** — filesystem change notifications. fanotify can also
   intercept opens system-wide.
5. **systemd units with `OnFailure=`, `OnSuccess=`, path units, timer units** —
   declarative event hooks. `systemd.path(5)` watches a path; `systemd.timer(5)` is cron
   with monotonic timing.

Plus eBPF (`bpftrace`, `bcc`) when you need kernel-level events the userspace APIs don't
expose.

## Power, lid, sleep, wake

* Lid open/close — `acpi_listen` (from `acpid`), or watch `/proc/acpi/button/lid/LID0/state`
* AC plug/unplug — UPower D-Bus, signal `org.freedesktop.UPower.DeviceChanged`
* Battery low / threshold — UPower `Percentage`/`State` properties
* Suspend/resume — `systemd-suspend.service` hooks (`/lib/systemd/system-sleep/`), OR
  D-Bus signal `org.freedesktop.login1.Manager.PrepareForSleep` (true=going down,
  false=coming up)
* Inhibit suspend — `systemd-inhibit` (and *check* who's inhibiting: `loginctl list-inhibitors`)
* Brightness changed — `/sys/class/backlight/*/brightness` (inotify it)
* Thermal threshold — `/sys/class/thermal/thermal_zone*/temp` (poll)
* Fan speed crossing — `/sys/class/hwmon/`
* OOM killer fired — `journalctl -kf | grep -i oom`

## Session, lock, login

`loginctl monitor` streams session events. Or subscribe via D-Bus to `org.freedesktop.login1`:

* `SessionNew` / `SessionRemoved`
* `UserNew` / `UserRemoved`
* Idle hint changed
* Lock / unlock — `org.freedesktop.ScreenSaver.ActiveChanged` (varies by DE; KDE/GNOME
  also expose their own variants)
* sudo invoked — `/var/log/auth.log` or journal `_COMM=sudo`
* Failed SSH login — sshd in journal (or `pam_exec` for instant reaction)
* New SSH login succeeded — same; pair with a Slack/Bluesky notify

## Devices

`udevadm monitor` is the live view. For declarative reactions, write a udev rule:

```
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="abcd", RUN+="/path/to/script.sh"
```

Useful events:

* USB device plugged — by vendor/product, by class (mass-storage, HID, audio)
* Disk inserted — udisks2 D-Bus signal `BlockDeviceAdded`
* LUKS volume unlocked
* Mount/unmount — udisks2 or `systemd.mount` units
* Display connect/disconnect — DRM uevents
* Bluetooth device proximity — bluez D-Bus, `org.bluez.Device1.Connected`
* Webcam/mic in use — `fuser /dev/video0`, or PipeWire/PulseAudio active streams
* Headphones plugged in — ALSA `jack` events, or PipeWire route changes

## Network

* Wi-Fi connected to new SSID — NetworkManager D-Bus, `StateChanged` on `Device.Wireless`
* VPN up/down — NetworkManager `org.freedesktop.NetworkManager.VPN.Connection.StateChanged`
* Default route changed — netlink (`ip monitor route`)
* Interface up/down — netlink, or `ip monitor link`
* DNS resolution failure — `systemd-resolved` journal
* DHCP lease acquired — NetworkManager journal
* New device joined the LAN — `arp-scan` polled, or watch the router's lease table

## Processes & resources

* Process started/exited — eBPF (`execsnoop`, `exitsnoop` from bcc-tools)
* Specific process crashed — `coredumpctl list` polled, or systemd `OnFailure=`
* Container started/stopped — `docker events`, `podman events`, systemd
* Cgroup memory pressure (PSI) — `/proc/pressure/memory`
* I/O pressure — `/proc/pressure/io`
* CPU stall warning — kernel log
* Disk full — `df` poll, or filesystem-specific events

## Filesystem

`inotifywait -m -r path` for live tail. Useful targets:

* `~/Downloads` — new file → auto-classify
* `~/Pictures/Screenshots` — new screenshot → auto-rename/file
* A config file edited — `~/.config/foo/...`
* New mail in `~/Maildir/new/` (procmail/fdm/mbsync output)
* Repo files changed (run tests, etc.)

`fanotify` upgrades this to system-wide, including process attribution — overkill for most
agents but valuable for "anything that opens this file" detection.

## Desktop & app integration

D-Bus session bus carries:

* New notification posted — `org.freedesktop.Notifications.Notify` (intercept with
  `dbus-monitor "interface='org.freedesktop.Notifications'"`)
* Media player state — MPRIS2 (`org.mpris.MediaPlayer2.Player.PlaybackStatus`) — the song
  changed in Spotify/VLC/Chromium
* Active window changed — depends on compositor (GNOME extension, KWin script, or
  Wayland's experimental protocols)
* Idle time — `xprintidle` (X11) / `swayidle` (Sway) / `hyprctl` (Hyprland)
* Focus changed to a specific window/app
* Online accounts state changed — GNOME Online Accounts D-Bus

## Updates & packages

* Package update available — `apt list --upgradable`, `dnf check-update`,
  `pacman -Qu`, `flatpak remote-ls --updates` (poll on a timer)
* Kernel updated — compare `uname -r` to the latest installed
* Snap refresh available
* AUR helper has updates

## Creative / cursed

* Wayland idle inhibitor present — *something* is keeping the screen awake (a video?)
* A specific systemd timer fired — bind a poller via `OnCalendar=`
* `journalctl --grep` matched a regex — Task-Scheduler-style declarative trigger
* Boot count changed — `systemd-analyze` shows previous boots
* SELinux/AppArmor denial — security signal
* `audit.log` shows sudo-without-password
* Cron job emitted output (any output is a signal — silence is the default)
* USB power delivery wattage changed (e.g. you switched to a weaker charger)
* PipeWire detected a new audio sink (DAC plugged in)

## How to wire it up

The two ergonomic shapes:

* **Long-lived tail** — `journalctl -f`, `dbus-monitor`, `udevadm monitor`,
  `inotifywait -m`, all stream forever; pipe into a parser that emits agent events.
* **Declarative** — systemd `path` units, `timer` units, `OnFailure=` directives, udev
  `RUN+=`, pam_exec hooks. The OS launches your script when the condition fires; you
  don't run any listener.

Prefer declarative for anything that should survive reboot or run before login. Prefer
long-lived tails for things you want to filter and stream into the agent live.

## A note on Wayland vs X11

X11 gives you global keyboard/mouse hooks, screen contents, and per-window introspection
trivially — many "watch what the user is doing" patterns work out of the box. Wayland
deliberately blocks most of these for security. If a desktop event you want isn't
available, check whether your compositor exposes it via its own IPC (Sway, Hyprland, KWin,
Mutter all have one) before assuming it's impossible.
