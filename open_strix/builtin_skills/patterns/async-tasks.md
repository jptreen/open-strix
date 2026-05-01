# Async Task Patterns

`shell(..., async_mode=True)` (see the `long-running-jobs` skill for the mechanics) is
usually pitched as "run a long build without blocking the turn." That sells it short. The
deeper move is:

> **Anything that blocks until a condition is met can be turned into an agent wake-up.**

The shell command exits → the harness fires a completion event → this conversation
resumes with full context. So if you can write a one-liner that blocks until *X*, you've
just built "wake me up when X" into the agent's flow.

## The killer pattern: block on the human

The shell command runs an OS-native modal dialog. The dialog blocks until the user
clicks. The agent's turn ends, the conversation suspends, and the moment the human gets
back to their machine and clicks OK, the agent resumes — with the original task still on
its mind.

**macOS** (the recently-discovered favorite):

```sh
shell(
  command='osascript -e \'display dialog "LinkedIn session expired. Log back in, then click OK." with title "Scraper paused" buttons {"OK"} default button "OK"\'',
  async_mode=True,
)
```

The agent kicks this off, the turn completes, the operator's screen shows a dialog. When
they click OK the shell exits, the completion event wakes the agent, and it picks up
exactly where it left off — no context lost, no need to rebuild what it was doing.

`display dialog` can also collect input — `default answer ""` returns the typed text in
the result — turning it into a synchronous "ask the human a question and wait" primitive.

**Linux**: `zenity --question`, `zenity --entry`, `kdialog --inputbox` — same shape, all
block until clicked.

**Windows**: a one-liner PowerShell `[System.Windows.MessageBox]::Show(...)` blocks the
same way.

For the *notify* side (non-blocking ping that the human should look at the dialog), see
`messaging.md` "Pinging the human at the keyboard." The full pattern is often **notify +
block**: send a notification so they know the dialog appeared, then the dialog itself is
the blocking gate.

## What else blocks?

Once you have "spawn a blocking command, wake me when it finishes," all of these become
agent wake-up triggers:

**Wait for a file to appear** — useful for "the human will drop a CSV in this folder":

```sh
shell(command='inotifywait -e create -q --format %f /watch/dir', async_mode=True)  # Linux
shell(command='fswatch -1 /watch/dir',                            async_mode=True)  # macOS
```

**Wait for a webhook** — block on a TCP listener for a one-shot HTTP POST:

```sh
shell(command='nc -l 9999 | head -c 4096', async_mode=True)
```

Pair with an ngrok/devtunnels URL (see `messaging.md`) and you've got "wake me when this
URL is hit." Useful for OAuth callbacks, GitHub webhook deliveries, "click here when
done" links you put in an email.

**Wait for CI to finish**:

```sh
shell(command='gh pr checks 123 --watch', async_mode=True)
```

GitHub's CLI blocks until the checks resolve. The wake-up gives you the result.

**Wait for a deploy / cloud build** — most cloud CLIs have `--wait` or foreground modes:

```sh
shell(command='gcloud builds submit --config=cloudbuild.yaml',  async_mode=True)
shell(command='kubectl rollout status deploy/api --timeout=10m', async_mode=True)
```

**Wait for a condition to become true** — the universal `until` loop:

```sh
shell(
  command='until curl -sf https://api.example.com/health; do sleep 10; done',
  async_mode=True,
)
```

This is where async overlaps `pollers` — and the distinction matters. Use a poller when
the condition will be polled *forever* and many wake-ups are expected. Use async-until
when this is a *one-shot* "wait for this specific thing, then resume *this* task." The
async pattern preserves your in-flight reasoning; pollers create fresh wake-ups.

**Wait for a long-running job you didn't start** — `wait $PID`, or:

```sh
shell(command='tail --pid=12345 -f /dev/null', async_mode=True)
```

Blocks until process 12345 exits.

**Wait for the laptop to wake up** — block on the next wake event from `pmset -g log`,
or on a `loginctl` session-change. Useful for "do this when the operator gets back."

**Wait for a Slack reaction / GitHub PR approval / email reply** — usually easier as a
poller, but if you need *this* conversation to resume (with all its context) when the
event fires, write an `until` loop that polls and exits on match, run it async, and let
the wake-up be your callback.

## The pattern: notify + block

The compound move that the LinkedIn dialog uses:

1. Agent realizes it can't proceed (session expired, decision needed, input required)
2. Agent fires a notification via `messaging.md` so the operator's phone/desktop pings
3. Agent spawns a blocking command (dialog, file-watch, webhook listener) `async_mode=True`
4. Agent's turn completes — token cost stops, the harness shows "waiting"
5. Operator handles the thing, clicks OK / drops the file / hits the webhook
6. Blocking command exits, completion event fires
7. Agent resumes with full context, finishes the task

This is dramatically better than two alternatives:

* **Synchronous block** — burns tokens for hours, hits timeouts, the conversation is
  pinned waiting for one human action.
* **"Just stop and the human can re-prompt"** — context is gone. The human has to
  re-explain the task. Often the agent has done substantial setup work that's wasted.

The async-block pattern preserves the work-in-progress across the human-time gap.

## Composing with browser automation

The browser-automation file's "headful bootstrap" pattern is a perfect fit:

1. Headless scrape detects logged-out state
2. Notify the human (`messaging.md`)
3. `async_mode=True` block on a dialog: "Click OK after you've logged in"
4. Operator opens the headful browser, logs in, clicks OK in the dialog
5. Agent resumes, retries the scrape, now succeeds

The full loop happens without the agent ever holding a synchronous turn during the
wait — so it costs nothing while the human is away, and the human's click is the only
event that brings everything back online.

## Composing with pollers

Pollers and async-tasks are duals:

* **Pollers** run forever on a schedule, emit events, wake *any* agent / fresh
  conversation.
* **Async tasks** are one-shot, block on one specific event, wake *this* agent /
  preserve this conversation.

When to convert one to the other:

* If a poller keeps firing for the same in-flight task, it's wasting your prelude work —
  consider an async-block-until instead.
* If an async-block has been waiting for hours and the operator might send several
  similar requests over time, you probably want a poller.

## When to reach for what

| Need | Tool |
|---|---|
| Quick command, need result now | sync `shell` |
| Long command, don't care when it finishes | async `shell`, ignore completion |
| Long command, resume *this conversation* when done | **async `shell` (this file)** |
| Wait for repeating events forever | `pollers` |
| Run on a fixed cadence (cron) | `/schedule` |
| Run a prompt repeatedly until cancelled | `/loop` |
| Sleep until a specific time | `ScheduleWakeup` |
| Parallel work that doesn't need this conversation | subagent |
| Wait for *one* event, preserving conversation | **async `shell` with a blocking command** |

The middle two columns (last row of each) are what this file is about. The async-block
pattern is the only primitive that preserves the current reasoning across an
indeterminate wait.

## Anti-patterns

* **Don't use async-block for things that take seconds.** Just await synchronously. The
  wake-up cycle has overhead.
* **Don't use it for recurring conditions.** That's what pollers are for. Async-block is
  one-shot.
* **Don't block on something that might never happen** without a timeout. Wrap the
  blocking command in `timeout 1h ...` if you can't trust the human / event to arrive.
* **Don't forget the pre-spawn discipline** from `long-running-jobs`. The wake-up turn
  is fresh context — leave a journal entry or state file before spawning so future-you
  knows what was happening.
* **Don't chain async-blocks naively.** Each wake-up is a separate turn with its own
  pre-spawn cost. If you need multiple gates, consider whether they can be one shell
  pipeline (`gate1 && gate2 && gate3`).
