# Coordination (S2)

S4 is about *perceiving* the world. **S2 is about peace between the parallel things you
do in it.** In VSM terms (see `mountaineering/philosophy.md`), S2 is the anti-oscillation
function — what stops operational subsystems (S1) from fighting each other when each
pursues its local goal correctly.

For an agent, S1 is everything happening in parallel: channels you're present in,
scheduled jobs, pollers, async shell jobs, sub-agents, peer agents in shared spaces.
Each is doing the right thing locally. Without S2 they collide — duplicate work, race
on shared state, double-message the human, oscillate state files back and forth.

S2 failures are subtle. Things work fine for weeks; then a quiet collision breaks
something at an inconvenient moment. Build coordination *before* the collision, not
after.

## Where parallel S1s live

Any of these can collide with itself, with another instance, or with another S1:

* **Channels** — multiple agents, or multiple skills within one agent, both feel responsible for a message
* **Schedules** — two `add_schedule` jobs both want 9am UTC; both fire and race
* **Pollers** — five pollers all on `*/5 * * * *` all wake at minute 0 (cron storm)
* **Async jobs** — two `shell(async_mode=True)` jobs writing to the same file
* **Sub-agents** — concurrent `acpx`/`codex exec` calls modifying the same repo or profile
* **Reactive vs. scheduled** — agent did the daily summary at 8:55 because the human asked; scheduled job fires blind at 9:00
* **Memory ↔ state files** — a block says one thing, a state file says another, behavior reflects neither
* **Peer agents** — two open-strix instances in the same Discord channel both responding to the human
* **Self-collision across turns** — a poller fires while you're mid-task; your two halves don't agree on what's happening

## The collision menu — concrete failure modes

| Symptom | Likely collision |
|---|---|
| Same artifact created twice (issue, PR, message, file) | Idempotency missing — boundary fired twice or two paths produced it |
| Pollers fail at the same minute every hour | Cron storm — all on `*/5` aligned to minute 0 |
| Daily morning post arrives twice on some days | Reactive vs. scheduled — agent did it manually, then schedule fired blind |
| Two agents replied to the same human within seconds | Channel ownership not defined |
| State file flips back and forth on consecutive turns | Two writers with conflicting models — pure oscillation |
| Playwright profile corrupted | Concurrent access without a claim |
| `send_message_loop_detected` event firing | Reactive-vs-scheduled collision, or skill-vs-skill collision |
| Memory block contradicts current behavior | A second writer (skill, schedule, manual edit) overwrote silently |

## The coordination toolkit

Most of these are five-line interventions, not architectures.

### `mkdir`-as-claim

The simplest mutex on POSIX. `mkdir` is atomic — exactly one process succeeds:

```sh
if mkdir /tmp/scrape.lock 2>/dev/null; then
  trap 'rmdir /tmp/scrape.lock' EXIT
  # do the work
fi
# else: another instance got there first, exit silently
```

For long-running claims (hours/days), prefer a state file with timestamp + owner so
stale locks can be detected programmatically.

### Schedule jitter

The default `*/5 * * * *` aligns every poller to minutes 0/5/10/.... Three pollers all
hit their APIs in the same second. Stagger them:

```
*/5 * * * *           # poller A — minutes 0,5,10,...
1-59/5 * * * *        # poller B — minutes 1,6,11,...
2-59/5 * * * *        # poller C — minutes 2,7,12,...
```

For `add_schedule`, pick odd-minute starts (`time_of_day: "09:07"` instead of `"09:00"`).
The human doesn't notice; the API does.

### Idempotency keys

The single highest-leverage coordination technique. Every artifact gets a unique key
derived from its inputs; before creating, check if it already exists:

```python
key = f"morning-summary-{date.today()}"
if state.get(key):
    return  # already done; quietly skip
do_the_work()
state[key] = True
```

This makes duplicate firings (poller, schedule, reactive call) safe by construction.
See `context-boundaries.md` "Idempotency" for the deeper treatment.

### Reactive-vs-scheduled debounce

When the same task can fire reactively *or* on a schedule, the schedule must look
before it leaps:

```python
last_run = read_state("morning-summary.last_run")
if last_run and (now - last_run) < timedelta(hours=4):
    journal(f"morning summary skipped — already ran reactively at {last_run}")
    return
```

Equivalently: write a "done" marker after every execution (regardless of trigger); the
schedule reads it before acting.

### Channel ownership conventions

When multiple agents share a channel, define who responds to what *in writing*. Common
shapes:

* **Mention-routed** — only respond when explicitly `@mentioned`; otherwise stay silent
* **Time-routed** — agent A handles 9am-5pm, agent B handles overnight
* **Topic-routed** — agent A handles questions about X, agent B about Y
* **Leader-elected** — first to claim wins (mkdir-as-claim on a per-message lock file)

Encode the convention in a `state/channels.jsonl` entry or memory block. Without it,
both agents will respond reasonably and step on each other.

### Single-writer state files

For any state file: name *one* writer; everyone else reads. The phone-book pattern is
the model — `phone-book.md` is auto-generated by the harness; the agent reads it but
doesn't edit it. Manual notes go in the sibling `phone-book.extra.md`. **Two writers on
one file is the most common source of oscillation.**

### The drain pattern

Instead of every poller waking the agent on its own clock, have pollers *enqueue* events
to a shared file; one scheduled tick *drains* the queue and processes them in batch.
Benefits:

* Smooths the wake-up rate (no thundering herd)
* Coordination decisions become cheap — you see all pending events together
* The agent makes one batched decision per tick, not N independent ones

A natural fit when you're already at five-plus pollers competing for attention.

### Pacing your own outbound

Self-imposed rate limits prevent self-collision with the harness's `send_message`
circuit breaker:

* No more than one message to the same channel within N seconds, unless directly responding
* Batch related findings into a single message rather than three sequential ones
* If you find yourself drafting a third message, ask whether one consolidated message would serve better

The circuit breaker is your S2 enforcement; pacing is the S2 *practice* that keeps you
out of its way.

## Recognizing oscillation

True oscillation — state flipping back and forth indefinitely — is the diagnostic
signature S2 was invented for. Tells:

* A state file's content over the last 5 turns: A, B, A, B, A
* Two memory blocks each instructing the other's contents to change
* A decision that gets reversed on alternating turns
* `git log` on `state/` shows constant flip-flop edits

When you see this, **stop the loop and find the two writers**. Usually it's two skills
or two scheduled jobs with overlapping responsibility for the same file. The fix is to
merge the responsibility into one writer, not to "try harder to be consistent."

## When NOT to coordinate

Coordination has a cost. Every lock, every check, every debounce is overhead and
potential failure surface. Skip it when:

* The action is naturally idempotent (read-only, or write-same-thing-every-time)
* Collisions are extremely rare AND non-destructive
* Adding the lock would serialize work that genuinely needs to be parallel

The shape: coordinate where collisions would be **destructive** (duplicate messages,
corrupted state, oscillation); don't coordinate where collisions would be **wasteful but
recoverable** (extra polling, redundant computation that produces the same answer).

## Composing with other patterns

* **`scheduling.md`** — most coordination problems start here. Use the decision tree,
  then add jitter and idempotency keys.
* **`context-boundaries.md`** — boundaries that fire twice are the most common collision
  source. Idempotency is the cross-cutting fix.
* **`multi-agent-handoff.md`** — sub-agents need explicit claims on shared resources
  (Playwright profile, repo, file regions). Without them, two `acpx` calls fight.
* **`fallback-chains.md`** — chained fallbacks can themselves collide if not
  coordinated (fallback B fires while A is still retrying).
* **`try-harder.md`** — file-conflict detection is an S2 diagnosis. Use the
  find-the-second-writer move.
* **`circuit-breaker.md`** — the harness's loop detection is a coarse S2 backstop;
  build your own finer-grained S2 above it.
* **`introspection`** — query `events.jsonl` for tool calls happening within the same
  second from different sessions or schedules. That's the collision footprint on disk.
