# Messaging Patterns

How to send messages between two computers or agents, or between an agent and the human
operator, especially when a formal channel is difficult to setup. We generally prefer to
avoid creating new security attack surface, but that's not always necessary.

The recurring trick: pick a service both parties already trust, find a writable field in it, and
treat that field as a one-character-wide socket. Pair with the `pollers` skill on the receiving end.

## Pinging the human at the keyboard

A different direction than most of this file: agent → operator, when the operator is at
their machine. Useful when the agent is stuck and needs hands — a session expired, a
captcha appeared, a decision is needed, a long job finished. The OS has built-in,
free, no-setup notification systems for exactly this.

Always include in the message *what you need* — a URL to click, a decision to make, a
command to run. A notification that just says "scrape failed" wastes a context switch.

For the *blocking* counterpart — "notify the human, then suspend this conversation
until they act" — see `async-tasks.md`. The compound move is: fire a notification (this
section), then `shell(async_mode=True)` a blocking dialog / file-watch / webhook listener
so the human's action becomes the agent's wake-up. Free during the wait, full context
preserved on resume.

**macOS** — built into `osascript`, no install needed:

```sh
osascript -e 'display notification "Session expired — log back into LinkedIn" with title "LinkedIn scraper" sound name "Basso"'
```

Sound names live in `/System/Library/Sounds/` (`Basso`, `Glass`, `Hero`, `Submarine`, ...).
For richer notifications (click handlers, action buttons, grouping), `brew install
terminal-notifier`. For hands-free / across-the-room, `say "the build is broken"`.

**Linux** — `notify-send` from libnotify, present on every desktop:

```sh
notify-send -u critical -i dialog-warning "LinkedIn scraper" "Session expired — log back in"
paplay /usr/share/sounds/freedesktop/stereo/dialog-warning.oga  # if you want audio
```

`zenity --notification` and `kdialog --passivepopup` are alternatives if you want toolkit-native.

**Windows** — PowerShell with `BurntToast` for modern toast notifications:

```powershell
New-BurntToastNotification -Text "LinkedIn scraper", "Session expired — log back in"
```

Or for one-off scripts without installing a module, the legacy
`[System.Windows.Forms.NotifyIcon]` balloon tip still works.

**Off-machine — push to your phone or chat** when the operator may not be at the
keyboard:

* `ntfy.sh` — free, one `curl` per notification, your phone subscribes to a topic
* Pushover — paid but cheap, official iOS/Android apps, action buttons
* Telegram bot — `curl` to the bot API, message lands in your DMs
* Slack DM to yourself via webhook
* Email to yourself with a high-priority filter rule
* macOS: send yourself an iMessage via `osascript` driving Messages.app
* iOS Shortcuts personal automation triggered by a shared file/note

## Default-safe — piggyback on a service you already use

Both sides are already authenticated, audit logs already exist, and there's no new endpoint exposed.

* Party A commits & pushes to a special Git repo / Party B has a poller setup to listen
* Party A creates a github issue / Party B has a poller setup to listen
* Comments on a long-lived GitHub PR — threaded, durable, addressable, free
* `git notes add` on a commit — the commit is the address, the note is the payload
* GitHub Releases — ordered, durable, supports asset blobs
* Issue *labels* as a state machine — the receiver polls label changes, no comment-parsing
* Jira / Linear comments on a shared ticket
* A shared Google Doc / Notion page — append a line, other side diffs
* Google Doc *comments* — already threaded, already notify
* A single cell in a shared Google Sheet (the cheapest message bus you'll ever build)
* Calendar events — title or description as payload, start time as deadline
* Email to a shared inbox + IMAP poller (decades old, still works, fully audited)

## Smuggle the message in a field meant for something else

When you don't want to create a new artifact, just edit an existing one. The "diff" is the message.

* The repo's `description` field on GitHub
* A PR title — flip it to signal a state change
* A draft PR's body — keep editing; receiver polls `updated_at`
* A user's profile bio (your bot account's bio is a 160-char broadcast)
* Slack/Discord status message
* A pinned message edited in place
* A GitHub Gist (secret URL is a private message; public is broadcast)
* A short-link service — change the destination URL to send a signal
* The README badge URL on a repo
* Profile/avatar image hash — flip the picture, the hash change is a 1-bit channel
* A static page on GitHub Pages — push HTML, the other side fetches

## Public boards (sometimes the right answer)

When privacy doesn't matter, a public append-only log is dramatically simpler than a private one.

* A Bluesky / Mastodon account both sides watch
* A subreddit you both read; a specific user's submission history
* Hacker News — submissions and comments are an RSS feed
* A wiki page either side can edit (with rollback for free)
* Nostr relays — public, append-only, free, multiple-relay redundancy
* A Bluesky custom-lexicon record type — same firehose, your own schema

## File drops

The mailbox model: one side writes a file, the other lists the directory.

* Shared Dropbox / iCloud / Google Drive folder
* An S3 prefix — `aws s3 ls` is the inbox
* WebDAV / Nextcloud share
* Syncthing folder — peer-to-peer, no cloud
* IPFS pin — publish a CID in any of the channels above; receiver fetches
* `magic-wormhole` / `croc` for one-shot transfers
* A printer's network share (yes, really — it's a shared filesystem with a chassis)

## Real queues & pub/sub (formal — list for completeness)

Use these when traffic is high enough that ad-hoc patterns hurt. Otherwise they're overkill.

* SQS / SNS, Pub/Sub, NATS, RabbitMQ, Kafka
* Redis pub/sub or a list
* Postgres `LISTEN` / `NOTIFY` (free if you already have Postgres)
* MQTT broker — HiveMQ has a public one, or self-host
* Matrix homeserver — federated, end-to-end encrypted, surprisingly easy
* IRC, XMPP — still excellent, still free

## Decentralized / weird

Mostly here for inspiration, occasionally the right tool.

* DNS TXT records you control — slow, global, cached, charmingly unkillable
* A subdomain's CNAME flip as a signal
* Bitcoin `OP_RETURN` — 80 bytes, durable, public, expensive per message
* Ethereum tx `data` field — same idea, cheaper, still public
* Certificate Transparency logs — issue a cert with a signaling SAN
* IPFS PubSub topics
* BitTorrent DHT entries

## More dangerous (but not bad) — direct connectivity

You're opening a port. Worth it for low-latency or high-volume, but think about who can reach it.

* MS devtunnels / ngrok / Cloudflare Tunnel
* Tailscale (Funnel for public, Taildrop for files between your nodes)
* A small VPS running `nc -l` or a tiny webhook receiver
* A self-hosted Matrix or XMPP server
* SSH into a shared bastion — `~/inbox` is now the queue

## Physical / IoT side channels

When the two parties share a room, or share a Home Assistant.

* Smart bulb on/off via the Hue API
* Smart plug state
* A scene activation in Home Assistant
* An LED on a Raspberry Pi visible to a webcam the other side polls
* AirDrop / Quick Share for one-shot transfers between humans-with-agents

## Steganographic / cursed-but-valid

When the medium itself is the message and you don't want to leave an obvious artifact.

* A filename pattern in a shared folder — the *name* is the payload, file is empty
* File `mtime` as 32 bits of channel
* `mkdir foo` = "ready", `rmdir foo` = "done"
* A symlink's target as the message
* A specific emoji reaction count on a fixed message
* Commit author email or commit message regex
* The number of stars on a throwaway repo (read-only side channel — receiver polls count)
* Spotify playlist title (the playlist is shared, the title is editable)

## Pattern: presence as the message

Most channels above can degrade to one bit: *the artifact exists* or *it doesn't*. That's
often enough — the receiver doesn't need a payload, just a trigger to act on already-known
context. A zero-byte file in a shared folder is the cheapest, most reliable inter-process
message ever invented. Don't reach for JSON when an `mkdir` will do.

## Pattern: deferred sends

Sometimes you want to message *the future*, not another party.

* Schedule an email to yourself (Gmail "Send later")
* `at` / `launchd` / cron job that drops a file the other side is polling
* A scheduled GitHub Action that reads a state file and acts
* A calendar reminder whose description is the message body

---

A useful exercise: pick any service you and the other party both have accounts on, and find
the smallest editable field in it. That field is a message channel. The richer the field,
the richer the protocol — but a single bit, polled often, is usually enough.
