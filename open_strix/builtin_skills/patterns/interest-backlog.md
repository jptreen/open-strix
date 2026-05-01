# The Interest Backlog (S3)

S4 is *perceiving the world*. S2 is *peace between parallel things*. **S3 is operations
management — choosing what to work on, tracking what's been done vs. pending, closing
loops on commitments.** In VSM terms (see `mountaineering/philosophy.md`), S3 allocates
resources across S1 units in the present.

For an agent, the hard part of S3 isn't *deciding* — it's *capturing*. Operational work
is invisible until it's tracked. The instinct is to push through whatever's in front
of you. What's missing is **an irrefutable source of truth for what's been done vs.
not** — a place where commitments and observations land that survives every context
boundary, every turn, every agent restart.

`chainlink` is a great substrate for this. It's a Rust CLI issue tracker
(`cargo install --git https://github.com/dollspace-gay/chainlink.git chainlink-tracker`)
already used in open-strix for 5-whys RCA storage (`five-whys/CHAINLINK_SETUP.md`) and
the Codex backlog worker (`optional-skills/chainlink-worker/`). A third great use — and
the focus of this file — is the **interest backlog**.

## The pattern

Small and disarming:

1. **Whenever something feels odd, broken, annoying, surprising, or interesting** —
   create a chainlink issue tagged `interest`. One line is enough.
2. **A poller every ~30 minutes** scans for open `interest` issues and surfaces a batch
   to the agent.
3. **The agent picks one and acts** — investigate, fix, defer with a comment, or close
   as wontfix with a resolution.
4. **If the backlog drains, no problem.** Drain means you're caught up. The poller
   does nothing that tick.

That's the whole pattern. Its power is in two places: the *low bar for logging* and the
*fixed cadence for draining*.

## The low-bar principle (the hard part)

The single hardest thing about running an interest backlog is **getting the agent to
log more issues, not fewer**. The default instinct is to push through small frictions
without recording them. That's how operational debt accumulates invisibly.

Set the bar comically low. Things that should generate an `interest` issue:

* A tool returned an unexpected error (even if you worked around it)
* A skill's instructions seemed to contradict reality
* A poller fired about something you didn't understand
* A human said "huh, that's weird" or "that shouldn't happen"
* You noticed a pattern in your own behavior you didn't expect
* A file in `state/` that shouldn't exist, exists
* A configuration that *should* exist, doesn't
* You were about to write "let me just try one more variation" — the variation itself is the issue
* A peer agent did something surprising in a shared channel
* You were confused for more than a few seconds about your own setup
* You started to type a question to the human, then answered it yourself — log what made you almost ask

The bar is *felt friction*, not severity. A one-line note costs nothing; a missed
pattern that compounds for weeks costs a lot. **Filter on the way out (drain), not on
the way in.**

## The drain poller

A `pollers.json` entry that runs every ~30 minutes:

```json
{
  "pollers": [
    {
      "name": "interest-drain",
      "command": "python interest_poller.py",
      "cron": "*/30 * * * *",
      "description": "Surface a batch of open interest items for triage"
    }
  ]
}
```

The poller script lists open `interest` issues via `chainlink issue list --label
interest --status open --json` and emits a single agent prompt — something like: "You
have 7 open interest items. Top 3: #42 (poller fired about X), #51 (skill Y
contradicted Z), #58 (file W shouldn't exist). Pick one and either close it with a
resolution or take action."

The cadence matters:

* **Long enough that one task can complete** — 30 minutes lets you investigate or fix
  one item without the next tick interrupting (composes with `coordination.md` —
  consider `mkdir`-as-claim if drain runs concurrent with other work).
* **Short enough that the backlog can drain in a few cycles** if you stay focused.
* **Drainage is fine.** A quiet tick (no open issues) is success, not failure. Don't
  add work to fill the slot.

For busy periods the cadence can shorten; during focus elsewhere it can lengthen. The
default is a starting point, not a fixed law.

## What "interest" subsumes (and when to split)

The label is intentionally broad. As the backlog matures you may add narrower labels
alongside `interest`:

* `bug` — confirmed broken behavior
* `friction` — works but annoying
* `idea` — speculative improvement
* `notice` — observation worth keeping; don't act, just file
* `question` — investigate later

Add these *only when you find yourself wanting to filter the backlog by them*. Until
then, `interest` alone is enough, and one label means one query to drain.

## Composing with other skills

* **`five-whys`** — when an interest item recurs three times, that's a five-whys
  candidate. Use a *separate* `.chainlink/` database from RCA chains
  (`five-whys/CHAINLINK_SETUP.md` covers why mixing creates noise in both directions).
* **`circuit-breaker.md`** — every breaker trip should produce an interest item. You
  may not have time to investigate now; that's exactly what the backlog is for.
* **`try-harder.md`** — "I should remember to do X next time" is the canonical
  wrong-shape resolution. The right shape is a memory block edit, a checkpoint.md
  edit, *or* an interest item. The interest item lets you defer the decision until you
  have context to make it well.
* **`introspection`** — chainlink is *what's been done / what's noticed*;
  introspection is *what actually happened in the events log*. Both are sources of
  truth from different angles. Querying them together is powerful: "find interest
  items whose creation timestamp is within 30s of a `tool_call_error`."
* **`coordination.md`** — when two skills both want to log the same thing,
  single-writer the interest backlog. Usually one skill is the canonical noticer.
* **`multi-agent-handoff.md`** — sub-agents can drain interest items autonomously.
  `acpx run -- "drain top 3 interest items from chainlink"` is a perfectly reasonable
  Saturday-morning routine.

## Useful chainlink queries for the backlog

```bash
# All open interest items
chainlink issue list --label interest --status open

# Recently created (catches a spike of frustration)
chainlink issue list --label interest --status open --sort created --reverse | head

# Search for a theme across all items
chainlink issue search "scheduler"

# Items idle for too long (operational debt accumulating)
chainlink issue list --label interest --status open --idle-for 30d
```

The "idle for too long" query is its own diagnostic. Items open for a month are either
(a) not actually interesting, or (b) genuinely hard but quietly important. Periodically
triage the long-tail — close the (a)s with a wontfix resolution, escalate the (b)s.

## Anti-patterns

* **Filtering on the way in.** "This doesn't seem important enough to log." That's
  exactly how operational debt becomes invisible. Log it; filter at drain time.
* **Only logging bugs.** Restrict the bar and you only see broken things; you'll miss
  the "huh, that was weird" observations that often compound into the most useful fixes.
* **Letting the backlog become a graveyard.** Items idle for months without triage
  signal drainage isn't happening. Either drain or actively close — silence in
  chainlink is rot.
* **Mixing the interest DB with the RCA DB.** Use separate `.chainlink/` directories.
  RCA chains and operational backlogs cause noise when mixed.
* **Not journaling the drain.** When you close an interest item, leave a journal entry
  noting what you did and why. Future-you and `introspection` both want the *thinking*
  behind closures, not just the disposition.
* **Closing items without a resolution comment.** A closed issue with no resolution is
  a lie of omission — you can't tell later whether it was fixed, deferred, or
  ignored. Always leave a one-line `--kind resolution` comment.
