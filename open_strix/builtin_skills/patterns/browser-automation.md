# Browser Automation Patterns

When the data you want is behind a login wall, hidden in a SaaS dashboard with no API, or
rendered by JavaScript only after a real browser visits, you point a headless Chromium at
it. Playwright is the tool; the patterns below are what makes it durable enough to run
unattended on a cron.

The reference setup that inspired this file: a launchd agent fires hourly, a wrapper
script gates on freshness + battery, and on the green light it runs `claude -p "carry out
/scrape-linkedin skill"`. The skill drives a headless Chromium via **Playwright MCP**,
reads numbers off the rendered page, and commits a JSON snapshot to a separate git repo
that downstream agents poll. ~10 lines of bash, one skill, no scraper code per se — the
agent *is* the scraper.

## When to reach for a browser (vs `curl`)

Use a real browser when *any* of these is true:

* The page renders client-side (React/Vue/etc.) and `curl` returns an empty shell
* The data is behind a login wall with cookies, CSRF tokens, or a JS challenge
* The site uses Cloudflare / PerimeterX / hCaptcha that fingerprints non-browsers
* You need to *do* something (click "Export", drag a date range, submit a form)
* The thing you want is a screenshot or PDF, not text

Otherwise `curl` + `jq` is faster, lighter, and more reliable. Browsers are heavy — boot
time is seconds, RAM is hundreds of megs.

## Playwright MCP: let the agent drive

The killer move is **giving Claude the browser as a tool**, not writing a Playwright
script. With Playwright MCP exposed via `.mcp.json`, the agent can:

* Navigate, click, type, scroll, screenshot — all as tool calls
* Decide what to click *based on what the page looks like right now*
* Recover when a button moved, a modal appeared, or the layout changed
* Log in interactively the first time, then reuse the session

This trades determinism for adaptability. A traditional Playwright script breaks when the
site reships its DOM; an agent driving the browser just looks at the page and figures out
the new path. For unattended, low-frequency scraping of sites you don't own, that's worth
a lot.

## Screenshot + vision over DOM selectors

Once the agent is the driver, the most robust extraction strategy is to **screenshot the
relevant area and read it visually**, not query CSS selectors. Selectors break on every
redesign; rendered numbers don't. The LinkedIn skill reads "1,234 impressions" out of a
screenshot.

Use this when:

* The site is one you can't predict (no commitment to selector stability)
* The visible value is what you want anyway (counts, status badges, charts)
* You're already paying for a vision-capable model

Stay with selectors when you need *every* row of a long table, when you need
machine-precise values, or when you have a stable accessibility tree.

## The persistent-profile trick

`.playwright-profile/` is a real Chrome profile dir. Cookies, IndexedDB, local storage,
service workers — all of it persists across runs. Log in once, run forever.

```
playwright launch_persistent_context(user_data_dir=".playwright-profile")
```

This single line solves authentication for almost any personal-data scraper. Combine with:

* **Per-account profiles** — one dir per identity for multi-account workflows
* **Gitignore the profile dir** — it contains your session cookies
* **Back up the profile encrypted** if losing it would mean a painful re-login (e.g. 2FA
  on every fresh sign-in)

## Headful bootstrap, headless steady-state

The recovery path that keeps unattended scrapers alive:

1. **Default state**: `--headless` in the MCP config. No window, runs on a cron.
2. **Bootstrap or recovery**: remove `--headless`, run the skill, a Chromium window
   opens. You handle the captcha / verify-account / 2FA / "is this you" prompt by hand.
3. **Put `--headless` back**, scraper resumes invisibly with the freshly authorized
   session.

LinkedIn, Cloudflare-protected sites, and bank portals all eventually challenge you. The
fix is *always* "do it once with your face on the screen." Bake that path in from day one.

When the scraper detects the logged-out state, it should *ask the human for help* — not
silently fail. See `messaging.md` "Pinging the human at the keyboard" for one-liners that
fire an OS-native notification with the URL to click. The agent shouldn't be the one
sitting and waiting; the operator should be the one being paged.

Even better: combine the notification with `async-tasks.md` to *suspend this
conversation* until the human is done. The agent fires the notification, spawns a
blocking `osascript display dialog "Click OK once logged in"` with `async_mode=True`,
the turn ends, the operator handles the login at their leisure, clicks OK, and the agent
resumes the scrape with full context intact. Zero token cost during the wait.

## Run-if-needed gates (don't just cron-it)

A naive cron runs every hour whether or not it needs to. The wrapper-script pattern adds
preconditions:

* **Freshness**: `latest.json` younger than N hours? skip
* **Battery**: on battery below N%? skip (don't drain the laptop scraping for fun)
* **Network**: on a metered connection? skip
* **Wall clock**: middle of the night when the site is sketchy? skip
* **Lock file**: previous run still going? skip
* **Caller**: did the user just close the lid? wait

Cron / launchd / systemd timer fires *frequently*; the wrapper decides whether to *run*.
This is the same "frequent poller, rare event" shape from `world-scanning.md`, applied to
expensive jobs instead of cheap checks.

## Output to git (or another bus)

The scraper writes `latest.json`, commits, and pushes. Downstream agents `git pull` to
ingest. This separates "scrape" from "consume" cleanly:

* Multiple consumers can subscribe with no coupling to the scraper
* Git history is the audit log for free
* Diffs across snapshots are the *interesting* signal (see `world-scanning.md` —
  inversions and deltas)
* If the scraper breaks, the last good `latest.json` is still there

This pairs perfectly with the `messaging.md` "git as a mailbox" pattern.

## What this stack unlocks

Personal dashboards & exports from sites without APIs:

* LinkedIn creator analytics (the reference case)
* Substack / Beehiiv / Ghost subscriber numbers
* YouTube Studio metrics
* Spotify-for-Artists, Apple Music for Artists
* Patreon / Ko-fi / OnlyFans creator stats
* Strava heatmaps, Whoop / Oura web dashboards
* Goodreads, Letterboxd reading/watching history

Status checks behind login walls (your data, your auth):

* Bank balance, credit card pending charges
* Brokerage portfolio value
* Utility bill usage chart
* Cell carrier data remaining
* HSA / FSA balance

Toil automation — fill the form, click the button:

* Submit weekly timesheets
* File expense reports
* Renew library books
* Refresh a session cookie that an upstream cron job needs
* Click "Approve" on a routine workflow you always approve

Watchers — visit, compare, notify (combine with `pollers`):

* Reservation slots opening at a specific restaurant (Resy/OpenTable)
* Appointment slots at the DMV / consulate / Apple Store
* A specific listing returning to a real-estate site
* A SKU coming back in stock at a retailer that doesn't run an API
* A SaaS pricing page changing
* A competitor's homepage (visual diff)

Authenticated downloads:

* Pull your own data export from a service that emails it weekly
* Grab the PDF of an invoice that only exists in a portal
* Save a generated report that the SaaS won't email

Evidence capture:

* Screenshot a webpage at a specific time as proof it existed
* Save the rendered HTML for archival
* Generate a PDF of a long thread / page / dashboard

## Tactical patterns inside the browser

* **Snarf the JSON the page already fetches.** The XHR responses the page makes are often
  cleaner than the rendered HTML. Use `page.on("response")` or MCP equivalent to capture
  them; sometimes you can skip the rendering step entirely.
* **Prefer the accessibility tree over CSS selectors.** `getByRole("button", {name:
  "Export"})` survives redesigns far better than `.btn-primary.export-cta-2024`.
* **Wait for network idle, not arbitrary `sleep`s.** Or wait for a specific selector to
  exist. Real waits beat speculative ones.
* **Block third-party junk.** Ads, analytics, fonts — most pages load 50+ resources
  that you don't need. Block by URL pattern; pages load 3-5x faster.
* **Save a screenshot + DOM dump every run.** When the scraper breaks, you'll have the
  evidence of what changed.
* **One context, one tab, close on completion.** Browsers leak; long-lived ones leak
  more.
* **Run in a container or VM for isolation** if the site is sketchy or you don't trust
  the JS.
* **HAR replay for tests.** Record one real session as a HAR file; replay it offline so
  your scraper logic can be tested without hitting the site.

## Cautions

* **Be a polite citizen.** Low frequency, jitter, off-peak hours, your-data-only.
  Hammering someone else's site is rude and gets your IP banned.
* **Read the TOS.** Scraping your own data is almost always allowed; scraping other
  users' data rarely is.
* **Sessions die.** Plan for "log in again" being a manual step that will happen every
  few weeks. Surface a clear "go log in" notification when it does — see
  `messaging.md` "Pinging the human at the keyboard" for the OS-native one-liners
  (`osascript display notification` on macOS, `notify-send` on Linux, BurntToast on
  Windows). Include the URL to click in the message.
* **Headless detection is real.** Some sites detect headless Chromium and serve a
  different page. Workarounds: real Chrome (not Chromium), realistic user-agent,
  `playwright-extra` with stealth plugin, or just give up and run headful via Xvfb.
* **Rate-limit yourself before they rate-limit you.** Once an hour is plenty for a stats
  dashboard. Once a day is plenty for most things.
* **Boot cost is real.** Don't spin up a browser per event — batch work into a single
  session per run.
