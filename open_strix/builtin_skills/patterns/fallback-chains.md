# Fallback Chains

The instinct when interacting with the world is to pick *one* channel, *one* scraper,
*one* data source — and pray it works. Real systems have outages, expired tokens,
rate limits, and quiet failures. The pattern is to **layer alternatives with explicit
fall-through**, where each layer covers a different failure mode.

This is not the same as retrying the same thing harder. Retrying handles *transient*
failures; fallback chains handle *modal* failures (the channel is down, the API is
gone, the cookie expired). Each rung of the chain assumes the previous rung is broken.

## Anatomy of a fallback chain

Three things make a chain useful rather than ceremonial:

1. **Each layer covers a *different* failure mode.** Slack → Slack-via-different-bot is
   not a chain; it's superstition. Slack → email → osascript dialog → ntfy.sh is a
   chain — each rung survives the previous rung's typical failures.
2. **Detection has to be cheap.** If checking whether a layer succeeded costs as much as
   the layer itself, the chain is more expensive than the failures it prevents.
3. **The terminal rung is loud.** When the whole chain falls through, *somebody* needs
   to know. Often: write a state file marker AND page the human via the most reliable
   channel you have left.

## Concrete chains worth stealing

**Reaching the operator** (composes with `messaging.md`):

```
1. Slack DM (fastest if they're at desk)
2. Email (survives Slack outage, archived)
3. osascript display dialog (only if they're at the Mac)
4. ntfy.sh push to phone (works anywhere)
5. write state/operator-needs-attention.md (terminal — picked up next session)
```

The agent should not iterate this in real-time on every send. Pick the right *initial*
rung based on urgency and time-of-day; fall through only on detected failure.

**Scraping data behind a login** (composes with `browser-automation.md`):

```
1. Official API if one exists
2. Undocumented internal API the page calls (snarf via network capture)
3. HTML parse from rendered page
4. Screenshot + vision read of the rendered page
```

Each rung is more brittle but works on more sites. The trick is *not* to start at #4 —
start at #1, fall through only when the upstream fails.

**Knowing about a state change** (composes with `world-scanning.md`):

```
1. Webhook (push from the source — if they support it)
2. Long-poll subscription
3. Cron-poll the API every N minutes
4. Cron-poll the public HTML page
```

Push is cheaper and faster but needs the source's cooperation. Pull always works.

**OS event detection** (composes with `os-events-*.md`):

```
1. Native OS hook (NSDistributedNotificationCenter, D-Bus signal, ETW)
2. Poll the underlying state (lock file, /sys file, registry key)
3. Poll a derived signal (process list, network connection, log line)
```

**Authentication** (composes with `browser-automation.md`):

```
1. Cached token in keychain / session
2. Refresh token flow
3. Re-login flow (probably needs the human; chain into "reach the operator")
```

## When NOT to add fallbacks

Fallback chains protect *transport*, not *truth*. If the failure mode is "the data is
wrong," more channels won't help — you'll just have wrong data faster.

* Don't fall back across data sources that disagree. Pick one source of truth and live
  with its outages, or surface the disagreement to the human.
* Don't fall back to a worse version of the same thing because the canonical version
  is "slow." Slow is not a failure mode.
* Don't add fallbacks "just in case" without a concrete failure mode in mind. Each
  rung costs maintenance — when one breaks silently, the chain is now lying.

## The verification problem

A fallback chain is only as useful as your ability to detect that a rung *actually*
delivered. Many channels fail silently — a Slack message to a bot that's been removed
returns 200 OK and goes to /dev/null.

Mitigations:

* **Confirm-on-receive when possible.** Webhooks acknowledged by the receiver. Email
  with read receipts (rarely). Discord/Slack message you re-fetch after sending to
  confirm presence.
* **Heartbeats.** If the channel has been quiet for too long, treat it as down and
  fall through. (See `world-scanning.md` "Inversions.")
* **Periodic end-to-end tests.** A scheduled job that exercises the whole chain and
  alerts if any rung silently broke.

## Don't overengineer

A two-rung chain is a *huge* upgrade from a one-rung chain. The marginal value drops
fast: fourth and fifth rungs are usually dead weight that nobody maintains.

The right shape for most agent code:

```
primary  →  one well-chosen backup  →  loud failure
```

Three layers tops, unless the cost of total failure is genuinely catastrophic.

## Composing with other patterns

* **`messaging.md`** — the most common use of fallback chains; the messaging file's
  many channels are a *menu*, this file is *how to layer them*.
* **`browser-automation.md`** — extraction-method fallback (API → HTML → screenshot).
* **`world-scanning.md`** — pull/push fallback for change detection.
* **`async-tasks.md`** — when the terminal "loud failure" rung is `display dialog
  "Everything failed, please intervene"`, that's an async-block waiting for the human.
* **`circuit-breaker.md`** — falling through every rung repeatedly is itself a
  pattern to break on. If the chain has fired three times in an hour, stop and
  investigate the structural problem rather than draining the chain again.
* **`introspection`** — `logs/events.jsonl` is where you debug *which* rung failed and
  why. A fallback chain that fires often is a signal worth investigating.
