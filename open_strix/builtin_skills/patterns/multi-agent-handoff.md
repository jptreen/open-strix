# Multi-Agent Handoff Patterns

`messaging.md` covers *channels*. This file covers *handoffs* — the protocol of giving
work to another agent (sub-agent, peer agent, fresh self) and integrating what comes
back. The channel is the road; the handoff is the way you pack the suitcase.

The recurring shapes are: **what context to package**, **what to await vs not**, **how
to route the result back**, and **what to verify before trusting it**.

## The three roles

Pick the role first; the protocol follows.

* **Sub-agent** — short-lived, dies when done. You spawn it for one task, get one
  result, integrate, move on. `acpx run -- "..."`, `codex exec`, `claude -p "..."`,
  open-strix `task(subagent_type="...", ...)`. Use when the work is bounded and you want
  the result back.
* **Peer agent** — long-lived, has its own life, you talk to it via a channel. Often
  another open-strix instance, or a Claude session running for a teammate. Use when
  collaboration is ongoing, not transactional.
* **Fresh self** — same identity (memory blocks, checkpoint, state) but a clean
  conversation. Triggered by `add_schedule`, `/schedule`, a poller wake-up, or the
  next user message. Use when *this* conversation has burned context and the task is
  better restarted than continued.

## Packaging the context bundle

The sub-agent / fresh-self / peer cannot read your in-flight conversation. Whatever
they need has to be in one of three places:

1. **The spawn command itself** — the prompt argument. Best for short, self-contained
   tasks. Token-efficient because nothing has to be read.
2. **A state file** the agent will read. Best for structured handoffs — a JSON spec, a
   list of URLs to process, a config. Reference the path in the spawn command.
3. **A channel or shared artifact** (git repo, issue, doc) that the agent already knows
   to check. Best when multiple parties contribute to the input.

The anti-pattern: assuming the sub-agent can "figure it out from context." It cannot.
Its context is what you give it.

## Fire-and-forget vs await-and-resume

The two shapes for getting work done by someone else:

**Fire-and-forget.** Spawn, never look back. The sub-agent's work product is its own
artifact — a file written, a message sent, a PR opened. Your turn continues without
waiting. Use when the result has its own home and doesn't need to flow back into your
reasoning.

```sh
shell(command='acpx run -- "summarize new bluesky mentions and post to #digest"', async_mode=True)
# Don't wait. The summary lands in #digest; you keep working.
```

**Await-and-resume.** Spawn async, the completion event wakes you, you integrate the
result. This is the pattern from `async-tasks.md` applied to sub-agent calls. Use when
the result feeds your next reasoning step.

```sh
# Pre-spawn journal entry: "Spawned acpx to investigate the rate-limit regression. On
# success, decide whether to escalate to oncall. On failure, fall back to manual log read."
shell(command='acpx run -- "investigate rate-limit regression, write report to /tmp/rl.md" > /tmp/acpx.out 2>&1', async_mode=True)
```

The hybrid: fire-and-forget for *most* sub-agents, await-and-resume only when you need
the result to make a decision *now*.

## Result routing

Where does the sub-agent's output go? Pick deliberately:

* **A file** — most reliable, survives everything, easy for you to read on resume
* **A channel** (`send_message`) — when the result should be visible to humans, not just
  used internally
* **A journal entry** — when it's about *you* (e.g., "investigated X, found Y")
* **A memory block update** — when it's a fact about the world that you'll need
  repeatedly
* **An issue / PR / commit** — when it's a durable artifact for the team

The sub-agent should know its routing target. Tell it explicitly in the prompt:
"…write the result to `state/research/rl-regression.md`." Don't hope it picks well.

## Trust gradient on results

A sub-agent's report describes what it *intended* to do, not necessarily what it *did*.
Before acting on the result:

* If it claims to have edited a file — read the file
* If it claims to have sent a message — check `list_messages`
* If it claims to have run tests — check the actual exit code / log
* If it claims something is "fixed" or "working" — verify with one sanity probe

This isn't paranoia; it's professional practice. The cost of one verification call is
much less than the cost of acting on a wrong claim.

## Two specific patterns worth naming

**The supervisor pattern** (from `mountaineering`). One agent runs many short-lived
sub-agents (climbers) and watches their results across iterations. The supervisor owns
strategy, the climbers own execution. Sub-agents have no memory of past climbs — that's
deliberate. The supervisor accumulates the learning.

**The scout-and-report pattern.** Spawn a sub-agent to investigate something
expensive (read 200 files, scrape a site, query a slow database) and *return only a
summary*. Protects your context window from the raw data while preserving the insight.
Sub-agent reads 200 files; you receive 200 words. This is what the `Explore` agent type
exists for.

## Composing with other patterns

* **`async-tasks.md`** — await-and-resume sub-agents are exactly the wake-on-completion
  pattern.
* **`context-boundaries.md`** — the sub-agent's completion is a context boundary; what
  survives is what you wrote before spawning, plus the result the sub-agent returned.
* **`journal-as-breadcrumbs.md`** — leave a journal entry naming the sub-agent, the
  task, the success path, and the failure path before spawning. On the wake-up turn,
  that journal entry is your only memory of why you spawned it.
* **`fallback-chains.md`** — if a sub-agent fails (timeout, error, garbage output),
  what's the next fallback? Usually: do it yourself, or escalate to the human.
* **`coordination.md`** — concurrent sub-agents that touch shared resources (Playwright
  profile, repo, file regions, the same external account) need explicit claims. Without
  them, two `acpx` calls fight. The mkdir-as-claim pattern is the cheapest fix.

## Anti-patterns

* **Don't spawn a sub-agent to do something you can do in two tool calls.** Spawn cost
  is real; sub-agents are for work that would burn meaningful context if done inline.
* **Don't await-and-resume for every sub-agent.** If the result has its own home (a
  file, a channel), let the sub-agent put it there and move on.
* **Don't trust a sub-agent's claim without a verification probe** for anything
  consequential.
* **Don't pass the conversation transcript as context.** Distill the relevant facts
  first; raw transcripts are noisy and burn the sub-agent's context too.
