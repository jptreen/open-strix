# Crossing Context Boundaries

A *context boundary* is any moment when your in-flight conversation state is dropped and
a fresh turn begins. The agent that comes out the other side has the same identity but
no memory of the variables you were holding mid-thought.

`long-running-jobs/SKILL.md` documents this for `async_mode=True` specifically — its
"pre-spawn discipline" is the canonical example. But the principle generalizes to *every*
context boundary in open-strix, and the unified frame is more useful than the per-skill
version.

## Where the boundaries are

Every one of these is a fresh turn with rebuilt context:

* **Async shell completion** — `shell(async_mode=True)` exits, completion event wakes
  you (`async-tasks.md`)
* **Sub-agent return** — `acpx`/`codex exec`/`task()` finishes, you resume with the result
* **Schedule fires** — `add_schedule` / `/schedule` job runs at its cron time
* **Poller wake-up** — a `pollers.json` poller emitted an event
* **Channel switch** — a message arrives in a different channel; you may resume there
* **Conversation resumption** — user sends a new message after a quiet period
* **Compaction** — the harness summarized old messages to fit in the window
* **Restart** — the agent process died and came back

In every case: in-memory variables are gone; what's on disk survives.

## What actually survives — the hierarchy

Sorted from most-reliably-present to most-fragile:

1. **Memory blocks** — rendered into the prompt every turn. If it's in a block, you
   *will* see it.
2. **`~/checkpoint.md`** — returned as the result of every `journal` call (and journal
   is mandated once per turn). Effectively injected near the end of every turn.
3. **State files** under `state/` — survive forever, but only if you remember to read
   them. Cross-reference them from blocks so future-you knows to look.
4. **Journal entries** in `logs/journal.jsonl` — survive forever, queryable via the
   `introspection` skill. Each is a letter to your future self.
5. **Recent channel history** — last N messages in the channel are re-rendered. Older
   messages exist on disk but aren't in the prompt by default.
6. **Events log** `logs/events.jsonl` — ground truth, queryable via `introspection`,
   but not in your prompt unless you read it.
7. **Sub-agent / job completion payload** — for async wake-ups, the wake-up prompt
   includes a tail of stdout/stderr (~4KB). That's the *only* in-prompt context you get
   about the job that just finished.

Anything *not* on this list does not survive. In-flight calculations, the contents of
variables, the half-built plan you were working on — gone.

## The discipline

Anything you'll need on the other side has to be written *before* you cross the
boundary. The shape is always the same:

1. **Decide what future-you needs to know.** A handle (job ID, file path, channel ID),
   the intent (why), the success path, the failure path.
2. **Write it somewhere durable** — a journal entry is usually the right home; a state
   file for structured data; a memory block for things you'll need across many turns.
3. **Then cross the boundary** — spawn the job, send the message, end the turn.

The wrong order — cross first, scramble for context after — is the most common bug.

## Idempotency: design for the boundary firing twice

Boundaries can produce duplicate wake-ups. A poller might emit the same event twice
(cursor wasn't saved before crash). A schedule might fire while a previous instance is
still running. An async job might be retried after a network blip. *Design actions so
they're safe to run twice.*

Concrete tactics:

* **Tag durable artifacts with a unique key.** "Posted draft #abc123 to channel" — if
  you wake up and see #abc123 already exists, skip rather than duplicate.
* **Update cursors atomically.** The poller pattern (see `pollers/design-patterns.md`)
  saves the cursor *after* successfully emitting events.
* **Use mkdir / O_CREAT-style claims.** If the directory exists, another instance is
  working; back off.
* **Treat send_message and similar with care.** A duplicate message annoys the human;
  worse, the harness may circuit-break you (`send_message_loop_detected`).

The agent's instinct is "I just got here, I should do the thing." The right instinct
is: "I just got here, has the thing already been done?"

Idempotency is the cross-cutting fix for context-boundary collisions, and it's the
single most leverage-rich technique in `coordination.md`. If you find yourself
designing the same protect-against-duplicate-firing logic for the third time, lift
it into a reusable shape per that file.

## Composing with the other patterns

* **`async-tasks.md`** — async wake-ups are the most common boundary. Pre-spawn
  discipline lives here.
* **`journal-as-breadcrumbs.md`** — *how* to write the boundary-crossing notes well.
  Journal is the highest-leverage durable surface that's in your prompt next turn.
* **`try-harder.md`** — when you keep losing context across boundaries, the structural
  fix is editing checkpoint.md or a memory block, not "remembering harder."
* **`introspection`** — the source-of-truth skill for *querying what survived*. Read
  `logs/events.jsonl` and `logs/journal.jsonl` to reconstruct what happened on the
  other side of the boundary. Trust events > journal > memory blocks.

## Common failure modes

* **Variable in the wind.** "I was about to call X but the turn ended" — and the next
  turn has no idea X was the next step. Fix: write it down before the turn ends.
* **Re-doing what's already done.** Boundary fired twice; you didn't tag the artifact;
  now there are two of them.
* **The breadcrumb without the trail.** A journal entry says "see state/foo.md" but
  state/foo.md doesn't exist or is empty. The breadcrumb has to land before the boundary,
  not after.
* **Silent drift.** Memory blocks say one thing, state files say another, behavior
  reflects neither. Use the `introspection` skill to detect this; the `try-harder.md`
  pattern of identifying file conflicts is exactly the fix.
