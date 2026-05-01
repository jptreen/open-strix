# Establishing Skills & Environmental Awareness

Skills and environmental awareness develop after the basics are in place. Don't rush
to build skills during your first week — focus on conversations, identity, and a
schedule first. This guide is for when you're ready to extend.

## When to Create a Skill

The **skill-creator builtin skill** (`/.open_strix_builtin_skills/skill-creator/SKILL.md`)
covers the mechanics of writing good skills — trigger descriptions, authoring checklist,
file structure. Read it when you're ready to build one.

The harder question is **when** to build one:

Create a skill when you notice a repeated pattern:
- You keep doing the same multi-step task → automate it
- A domain requires context you won't remember → document it
- You need to interface with an external system → codify the interface

**Don't create a skill when:**
- The task has only happened once (wait for repetition)
- A memory block or state file would suffice (skills are for procedures, not context)
- A builtin skill already covers it (check your builtin skills first)

## Establishing S4 (Environmental Awareness)

In VSM terms (see `mountaineering/philosophy.md`), **S4** is your *intelligence and
adaptation* function — your capacity to perceive what's happening in the world, reach
the people and systems that matter, and adjust your behavior in response. A new agent's
S4 starts shallow: a daily check-in, a fuzzy sense of who your human is. Real S4 is
*built up over weeks*, in response to friction that the basics don't cover.

The `patterns` builtin skill is the brainstorm bank for S4. Don't read it cover-to-cover
during onboarding — read each file *the first time you hit the friction shape it
addresses*. Each one converts a behavioral instinct ("I should pay more attention to X")
into a structural artifact (a poller, an OS hook, a memory block edit, a fallback
chain).

### People

As you interact with your human, they'll mention other people. Track them:
- Name and how to reference (Discord ID for mentions, etc.)
- Relationship to your human
- Communication style and when they're active
- What they care about

**Start with a `relationships` memory block** for the 2-5 most important people.
Add a `state/people/` directory if the network grows larger.

When you need to *reach* people across platforms (Discord, email, Bluesky, phone), the
`patterns/messaging.md` file is the menu of channels — including "ping the human at
the keyboard" via `osascript`/`notify-send` for synchronous attention-grabs.

### External Information

An agent with no external information intake operates in a bubble. The specific sources
depend on your purpose:

- Research agent → arXiv scanning, paper reviews
- Social agent → Bluesky monitoring, engagement tracking
- Work agent → project status, deadlines
- General → news, industry developments

The shape — scheduled scan → filter → surface only what matters → log even silence — is
exactly the **poller pattern**. See `pollers/SKILL.md` for the mechanics and
`patterns/world-scanning.md` for *what's worth scanning* across ~15 categories (issue
trackers, RSS, weather/AQI, civic feeds, the web-as-a-database, and the underused
"Inversions" — noticing what *didn't* happen).

**Quality signal:** You should surface things the human didn't already know. If every
"finding" is something they saw first, your scanning isn't adding value.

### Channel Awareness

For Discord-based agents, understand your channel landscape. A phone-book state file
works well:

```markdown
# Channel Reference

## buddy-channel (ID: 123456)
- Type: Social, multi-agent
- Members: Tim, Agent1, Agent2
- Behavior: Casual, banter OK
- Peak hours: 8am-10pm ET
```

Update this as the landscape changes. New channels, new members, behavior shifts.

### OS-Level Awareness

The operating system knows when interesting things happen — laptop lid opens, screen
unlocks, USB plugged in, network joined. You don't need a poller for these; the OS will
*tell* you. See `patterns/os-events-{macos,windows,linux}.md` for the catalog of
events and the declarative hook mechanism on each platform (launchd `WatchPaths`, Task
Scheduler with Event-Log triggers, systemd path/timer units, udev rules).

This matters more than it sounds. "React when the human gets back to their machine"
becomes a one-line `pmset -g log` tail; "wake up when the Downloads folder gets a new
PDF" becomes a `LaunchAgent` plist.

## Establishing S3 (The Interest Backlog)

S4 is *perceiving the world*. S2 is *peace between parallel things*. **S3 is operations
management — knowing what's been done vs. not, choosing what to work on next, closing
loops on commitments.** In VSM terms, S3 allocates resources across S1 units in the
present.

For an agent, S3's hardest problem isn't deciding — it's *capturing*. Operational work
is invisible until it's tracked. Without an external source of truth, your sense of
"what's pending" lives only in your last few turns of context, and rots the moment a
boundary fires.

The pattern that solves this — covered in `patterns/interest-backlog.md` — is small:

1. Install **chainlink** (Rust CLI issue tracker — see `five-whys/CHAINLINK_SETUP.md`)
2. Whenever *anything* feels odd / broken / annoying / surprising → log a one-line
   issue with the `interest` label
3. A poller every ~30 minutes drains the backlog into your attention
4. If the backlog drains to zero, that's success, not idle time

The single load-bearing insight: **the hard part is logging more, not less.** The
default agent instinct is to push through small frictions silently. Interest backlog
inverts that — *every* friction is logged; filtering happens at drain time, not
capture time.

**Three early habits worth establishing during onboarding** (chainlink takes 10-20 min
to install once; benefit forever):

1. **Install chainlink** if it's not already there. Use a *separate* `.chainlink/`
   directory for the interest backlog vs. RCA chains — mixing them causes noise.
2. **Add to `~/checkpoint.md`**: a line like *"If anything in this turn felt odd,
   broken, surprising, or annoying — log a one-line `interest` issue before
   journaling."* This makes the discipline run on every single turn (since checkpoint.md
   is returned by every `journal` call). The lowest-effort, highest-leverage way to
   bootstrap the practice.
3. **Register the drain poller** with a 30-minute cadence (the JSON is in
   `patterns/interest-backlog.md`).

S3 maturity is felt mostly in *what you no longer drop*. Interest items capture the
small frictions and curious observations that previously evaporated at the next context
boundary.

## Establishing S2 (Coordination Between Parallel Things)

S4 deepens as you add new ways to *perceive* the world. **S2 deepens as you add new
parallel S1s** — channels you respond in, scheduled jobs, pollers, async shell jobs,
sub-agents, peer agents. S2 is the VSM anti-oscillation function: what stops these
from fighting each other when each is doing the right thing locally.

S2 doesn't matter on day one. It matters by week two, when you have a daily check-in
schedule, two pollers, and a sub-agent skill — and one morning the daily summary
arrives twice, or the same `@you` mention gets two replies, or a state file flips back
and forth on consecutive turns.

The `patterns/coordination.md` file is the brainstorm bank for this. Don't read it
preemptively; reach for it the *first* time you see a collision symptom:

| Symptom | Likely S2 collision |
|---|---|
| Same artifact (message, file, PR) created twice | Idempotency missing — boundary fired twice |
| Daily X arrives twice on some days | Reactive vs. scheduled collision (no debounce) |
| Multiple pollers fail at the same minute every hour | Cron storm — all aligned to minute 0 |
| Two agents replied to the same human within seconds | Channel ownership not defined |
| State file flips back and forth on consecutive turns | Two writers — pure oscillation |
| Memory block contradicts current behavior | A second writer overwrote silently |

The toolkit is small and cheap: idempotency keys, schedule jitter, `mkdir`-as-claim,
single-writer state file conventions, reactive-vs-scheduled debounce, the drain pattern.
Most are five-line interventions.

**Two early habits worth establishing during onboarding** (before you have collisions
to recover from):

1. **When adding a new schedule, jitter the time.** `09:07` instead of `09:00`. Costs
   nothing; prevents the cron-storm class entirely.
2. **For any state file you create, name the writer in a comment at the top.** "This
   file is written by skill X. All other readers." Future-you will have one place to
   look when oscillation appears.

Beyond those, S2 is a *react-to-friction* skill like S4 — open the patterns file the
first time you hit a symptom from the table.

## Deepening S4 When Friction Appears

The shapes below are exactly what the `patterns` skill catalogs. The sign that you need
each is the friction in the left column:

| Friction you've hit | Pattern to reach for |
|---|---|
| "I didn't know X happened until told" | `patterns/world-scanning.md` — turn it into a poller |
| "I didn't notice X *didn't* happen" | `patterns/world-scanning.md` "Inversions" — dead-man switch |
| "I should have reacted when [system event]" | `patterns/os-events-*.md` — bind to the native hook |
| "I couldn't reach the human / another agent" | `patterns/messaging.md` — pick a channel, including OS notifications |
| "I burned tokens waiting for X" | `patterns/async-tasks.md` — `shell(async_mode=True)` blocking on the wake-up |
| "There's no API for the data I need" | `patterns/browser-automation.md` — Playwright + persistent profile |
| "The channel/scraper/source failed and I had no backup" | `patterns/fallback-chains.md` — explicit fall-through |
| "I needed help from another agent" | `patterns/multi-agent-handoff.md` — packaging context, awaiting results |
| "I lost context across an async wait / fresh turn" | `patterns/context-boundaries.md` — what survives, what doesn't |
| "I keep forgetting to do X / got corrected on the same thing" | `patterns/try-harder.md` — edit a memory block, edit checkpoint.md |
| "I'm stuck in a loop and pushing harder isn't working" | `patterns/circuit-breaker.md` then `patterns/try-harder.md` |
| "I have many overlapping ways to schedule this" | `patterns/scheduling.md` — the decision tree |
| "Two parallel things stepped on each other" / "the same X happened twice" | `patterns/coordination.md` — S2 collision toolkit |
| "I keep wanting to remember to look into X later" / something felt weird and I moved on | `patterns/interest-backlog.md` — log it; drain it on a cadence |

The general flow: friction appears → introspection finds the pattern in your behavior →
patterns gives the menu of structural fixes → you apply the fix as a *file edit*
(memory block, checkpoint.md, state file, schedule, poller). No "I'll try harder
next time" survives an agent's context window. A diff does.

## Putting It Together

Environmental awareness isn't built in a sitting — it accumulates. You won't have a full
S4 on day one, and trying to bootstrap one from a checklist would be over-engineering.
But by week two you should know who your human talks to, what channels exist, how to
behave in each, and have at least one poller, one schedule, and one OS-level hook
running that you didn't have on day one.

That deepening comes from operating, not planning. When friction appears, use it as a
prompt to open the relevant `patterns` file — that's the moment the file exists for.
