# Windows Event Sources

Windows has the richest *declarative* event-trigger story of any OS — Task Scheduler can
bind a script to almost anything (idle, login, lock, plugged-in, an Event Log entry
matching an XPath). Combined with WMI event subscriptions and ETW, you can react to
anything the kernel notices.

When in doubt, prefer push (the OS notifies you) over polling. The big four push
mechanisms below cover almost everything worth listening for.

## The five primitives

1. **Task Scheduler** — declarative triggers: time, idle, login/logout, lock/unlock, AC
   plugged in, network connected, OR an arbitrary Event Log XPath query. Bind a
   PowerShell script and you're done. No listener process required.
2. **`Get-WinEvent` / `wevtutil`** — Windows Event Log, the journal of everything. Has
   thousands of channels (Security, System, Application, plus per-app Operational
   channels). Pollable in tail mode or queryable historically.
3. **WMI / CIM event subscriptions** — `Register-WmiEvent` or `Register-CimIndicationEvent`
   to subscribe to things like `Win32_ProcessStartTrace`, `Win32_VolumeChangeEvent`,
   `__InstanceModificationEvent` on any class. Push-based, in-process.
4. **`SystemEvents` class** (`Microsoft.Win32.SystemEvents`) — .NET events for session
   switch, power mode, display change, time change, theme change. Hookable from PowerShell
   via `Register-ObjectEvent`.
5. **ETW (Event Tracing for Windows)** — the real-time firehose. `logman` / `pktmon` /
   PerfView / KrabsETW. Overkill for most agent uses, but unmatched for kernel-level events.

Plus `FileSystemWatcher` (.NET) and `Register-ObjectEvent` against any .NET object that
exposes events, plus registry change notifications via `RegNotifyChangeKeyValue`.

## Lid, sleep, wake, power

The `System` log + `kernel-power` provider:

* Lid opened/closed — Event ID 1, plus `Microsoft-Windows-Kernel-Power/Thermal-Operational`
* System sleep / wake — Event ID 1, 42, 107
* Wake source (lid, USB, network, RTC) — included in the wake event payload
* AC adapter plugged/unplugged — `Microsoft-Windows-Kernel-Power` + `Win32_Battery` polling
* Battery crossed a threshold — `Win32_Battery.EstimatedChargeRemaining`
* Thermal event — `Kernel-Power/Thermal-Operational`
* Modern Standby entry/exit
* Hibernate file written

`SystemEvents.PowerModeChanged` gives you Suspend/Resume in real time from a long-lived
PowerShell session.

## Session, lock, login

Security log has the canonical events:

* 4624 — successful login (with logon type: console, network, RDP, unlock, etc.)
* 4634 — logoff
* 4625 — failed login (brute-force detector)
* 4800 / 4801 — workstation locked / unlocked
* 4778 / 4779 — RDP session reconnect / disconnect
* `SystemEvents.SessionSwitch` for in-process notification of lock/unlock

## Devices

* USB plug/unplug — `Win32_VolumeChangeEvent` via WMI, OR
  `Microsoft-Windows-Kernel-PnP/Configuration` events
* Disk arrival/removal — same as above; ideal for "back up that drive on insert"
* New monitor connected — `SystemEvents.DisplaySettingsChanged`
* Audio default device changed — `IMMNotificationClient` (COM)
* Bluetooth pair/unpair — Event Log + `Get-PnpDevice`
* Camera/mic in use — `consentStore` registry keys under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore`

## Network

* Network changed — `NetworkChange.NetworkAvailabilityChanged` (.NET)
* Wi-Fi connected to a new SSID — `Get-NetConnectionProfile`, or `WlanRegisterNotification`
* VPN up/down — Routing and Remote Access events, or `Get-VpnConnection`
* DHCP lease acquired — DHCP-Client/Operational log
* DNS resolution failures — DNS Client/Operational log
* Firewall block — Windows Firewall log

## Processes & services

WMI is your friend here:

* New process started — `Win32_ProcessStartTrace`, with command line
* Process exited — `Win32_ProcessStopTrace`
* Specific process started/stopped — filter by `ProcessName`
* Service crashed — `Service Control Manager` events (7031, 7034)
* Service installed — 7045 (also a classic malware indicator)
* Driver loaded — `Microsoft-Windows-Kernel-PnP`
* BSOD — watch `C:\Windows\Minidump` for new `.dmp` files
* WER report generated — `Microsoft-Windows-Windows Error Reporting`

## Filesystem & registry

* `FileSystemWatcher` for any directory — fires on Created/Changed/Deleted/Renamed
* USN journal for whole-volume change tracking (no missed events)
* `Register-ObjectEvent` on the watcher fires from PowerShell
* Registry change — `RegNotifyChangeKeyValue` (P/Invoke from PowerShell), useful for
  watching, e.g., `Run` keys, `Uninstall` keys, app config

## Updates, install, software

* Windows Update available — `Microsoft.Update.Session` COM API
* Update installed — `Setup` log
* MSI install / uninstall — Application log, `MsiInstaller` source
* WinGet upgrade available — `winget upgrade` polled
* Defender detection — `Microsoft-Windows-Windows Defender/Operational`, Event ID 1116
* Defender quarantine entry added
* New scheduled task created (often a malware signal)

## Apps with COM automation

* Outlook — `Outlook.Application` COM, `NewMailEx` event for new mail
* Excel — workbook open/save events
* Edge / Chrome history — SQLite databases under `%LOCALAPPDATA%`
* Teams presence/status — Graph API or registry keys

## UI Automation (the surprising one)

`UIAutomationClient` from .NET can subscribe to:

* Active window changed — `AddAutomationFocusChangedEventHandler`
* A specific control's value changed (e.g. a particular textbox)
* A toast notification appeared
* Window opened / closed by title pattern

This is how accessibility tools work; it's an excellent generic UI event bus.

## Creative / cursed

* Boot time changed — `(gcim Win32_OperatingSystem).LastBootUpTime`, machine rebooted
* User idle time — `GetLastInputInfo` via P/Invoke
* Specific URL visited (Edge history SQLite + mtime watch)
* Cortana / Copilot invocation in unified app logs
* Snipping Tool ran (process trace + window title)
* Focus Assist mode change — `WNF_SHEL_QUIETHOURS_ACTIVE_PROFILE_CHANGED`
* Night Light on/off — registry under `Software\Microsoft\Windows\CurrentVersion\CloudStore`
* Storage threshold crossed — Storage Sense / `Win32_LogicalDisk` poll
* GPU process for a known game launched (gaming-session detector)
* Hardware ID change (motherboard swap, GPU swap) — `Win32_ComputerSystemProduct`

## How to wire it up

For most agent uses, the simplest path is **Task Scheduler with an Event Log trigger**:

1. Find the Event Log channel + ID you care about (`Get-WinEvent -ListLog *`)
2. Create a task that triggers `On an event` with an XPath query
3. Action: run `pwsh.exe -File yourscript.ps1`

The OS hosts the long-running listener for you. No separate daemon, survives reboots,
shows up in the GUI for the user to inspect. Reach for `Register-WmiEvent` only when you
need a long-lived in-process subscription with stateful handlers.
