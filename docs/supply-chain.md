# Supply-Chain Pinning Runbook

This is the standing convention for third-party code that enters the agent
runtime, dashboard UI, docs-as-commands, or operator bootstrap path.

The motivating attack class is maintainer or registry account compromise. In
September 2025, attackers phished the npm maintainer known as qix and published
malicious versions of packages including `debug`, `chalk`, and `ansi-styles`;
Palo Alto Networks reported that the affected packages represented billions of
weekly downloads and that the malicious versions were live for roughly two
hours:
https://www.paloaltonetworks.com/blog/cloud-security/npm-supply-chain-attack/

Pinning shrinks that publish-to-takedown window. SRI and lockfiles make the
selected artifact fail closed if it changes.

## Threat Model

Protected against:

- Floating CDN URLs changing underneath a served page.
- Floating `npx <tool>` or bootstrap installs picking up a compromised newest
  package during the window before removal.
- Mutable Google Fonts CSS changing the font binary or loading unexpected
  third-party content.
- Reviewed ClawHub/Skillflag skills being replaced by different instruction
  text after review.
- Advisory response requiring live registry access just to answer "do we use
  this package?"

Not protected against:

- A package author intentionally shipping malicious code in the pinned version.
- A transitive dependency that was already malicious when the lock was made.
- A compromised package manager, OS package, browser, shell, or TLS root.
- Agent skill behavior that was reviewed but is still semantically dangerous.
- Optional operator examples that are copied into a local config outside this
  repo. Example: the MCP server packages are pinned and scan-locked, but are
  not installed unless an operator enables them.

## Pin Inventory

CDN/SRI pins:

- `marked@18.0.3`, `chart.js@4.4.0`: `open_strix/web_ui.py`,
  `open_strix/ops_dashboard.py`, with `sha384-*` integrity.
- `@xterm/xterm@5.5.0`, `@xterm/addon-fit@0.10.0`,
  `@xterm/addon-web-links@0.11.0`: `claude_codes_chat` terminal template, with
  `sha384-*` integrity.
- `htmx.org@1.9.10`: botnet-manager templates/design handoff pages in
  kaleidoscope worktrees, with `sha384-*` integrity.
- `react@18.3.1`, `react-dom@18.3.1`, `@babel/standalone@7.29.0`:
  botnet-manager `design_handoff_botnet_manager/Botnet Manager.html` pages in
  kaleidoscope worktrees, with `sha384-*` integrity.

Self-hosted font pins:

- IBM Plex Sans/Mono: botnet-manager `static/fonts.css`,
  `design_handoff_botnet_manager/fonts.css`, and vendored `.woff2` files.
- Inter: `open_strix/static/fonts/inter.css` and vendored
  `InterVariable.woff2`.

npx/bootstrap pins:

- `clawhub@0.15.0`: open-strix skill acquisition docs and references.
- `skillflag@0.1.4`: open-strix skillflag install docs.
- `acpx@0.7.0`: `optional-skills/chainlink-worker/worker.py` and docs.
- `@anthropic-ai/claude-code@2.1.140`: `claude_codes_chat/bootstrap.py`.
- `eslint@10.3.0`, `depcheck@1.4.7`: `tony` hooks/docs, scanned under
  `supply-chain/locks/npm/dev-tools/`.
- `@modelcontextprotocol/server-brave-search@0.6.2` and
  `@modelcontextprotocol/server-github@2025.4.8`: open-strix `SETUP.md`.

PyPI pins:

- Every direct dependency in every in-scope `pyproject.toml` is exact-pinned;
  the adjacent `uv.lock` is the transitive lock.
- Kaleidoscope service pyprojects in the `kaleidoscope`,
  `kaleidoscope_e2e`, and `kaleidoscope_ux` worktrees:
  - `alert-receiver/pyproject.toml`: `fastapi==0.136.1`,
    `uvicorn[standard]==0.46.0`, `httpx==0.28.1`,
    `pydantic==2.13.4`, `pydantic-settings==2.14.1`,
    `pytest==9.0.3`, `pytest-asyncio==1.3.0`, `ruff==0.15.12`,
    `mypy==2.1.0`.
  - `botnet-manager/pyproject.toml`: `fastapi==0.136.1`,
    `uvicorn[standard]==0.46.0`, `pydantic==2.13.4`,
    `pydantic-settings==2.14.1`, `httpx==0.28.1`, `jinja2==3.1.6`,
    `pyyaml==6.0.3`, `python-multipart==0.0.28`,
    `psycopg[binary]==3.3.4`, `ansible-core==2.19.9`,
    `pytest==9.0.3`, `pytest-asyncio==1.3.0`, `ruff==0.15.12`,
    `mypy==2.1.0`.
  - `cost-watcher/pyproject.toml`: `httpx==0.28.1`,
    `psycopg[binary]==3.3.4`, `pydantic==2.13.4`,
    `pydantic-settings==2.14.1`, `pyyaml==6.0.3`, `pytest==9.0.3`,
    `pytest-mock==3.15.1`, `ruff==0.15.12`, `mypy==2.1.0`,
    `types-PyYAML==6.0.12.20260510`.
  - `openstrix/pyproject.toml`: `httpx==0.28.1`, `pyyaml==6.0.3`,
    `psycopg[binary]==3.3.4`, `pytest==9.0.3`,
    `pytest-asyncio==1.3.0`.
  - `synapse-health-monitor/pyproject.toml`: `httpx==0.28.1`,
    `pydantic==2.13.4`, `pydantic-settings==2.14.1`,
    `pytest==9.0.3`, `pytest-asyncio==1.3.0`, `ruff==0.15.12`,
    `mypy==2.1.0`.
- `open-strix/pyproject.toml`: `aiohttp==3.13.5`,
  `apscheduler==3.11.2`, `deepagents==0.6.1`, `discord-py==2.7.1`,
  `matplotlib==3.10.9`, `mcp==1.27.1`, `python-dotenv==1.2.2`,
  `pyyaml==6.0.3`, `pytest==9.0.3`, `pytest-aiohttp==1.1.0`,
  `pytest-asyncio==1.3.0`.
- `claude_codes_chat/pyproject.toml`: `click==8.3.3`,
  `textual==8.2.6`, `fastapi==0.136.1`,
  `uvicorn[standard]==0.46.0`, `pytest==9.0.3`, `playwright==1.59.0`.
- `tony/pyproject.toml`: `google-api-python-client==2.196.0`,
  `google-auth==2.52.0`, `google-auth-httplib2==0.4.0`,
  `google-auth-oauthlib==1.4.0`, `matrix-nio==0.25.2`,
  `oauth2client==4.1.3`, `open-strix==0.1.43`,
  `pdfplumber==0.11.9`, `pytz==2026.2`, `requests==2.34.0`.
- Build-system pins:
  - Hatchling projects in the kaleidoscope, kaleidoscope_e2e,
    kaleidoscope_ux, and open-strix worktrees use `hatchling==1.29.0` in
    `[build-system].requires`.
  - `claude_codes_chat/pyproject.toml` uses `setuptools==82.0.1` and
    `setuptools-scm==10.0.5` in `[build-system].requires`.
- Deploy-time Ansible paths that use `pip` consume hash-pinned
  `requirements.lock` files and install local packages with
  `--no-deps --no-build-isolation`.
- Ansible deploy lockfiles are the source of truth for their full transitive
  package sets:
  - `alert-receiver/requirements.lock`, `botnet-manager/requirements.lock`,
    `cost-watcher/requirements.lock`, `openstrix/requirements.lock`.
  - `ansible/roles/openstrix-install/files/requirements.lock` in worktrees that
    deploy the in-repo OpenStrix helper from role-local dependency pins.
  - `ansible/roles/litellm-proxy/files/requirements.lock`: includes
    `litellm==1.83.14`, `litellm-enterprise==0.1.39`,
    `fastapi==0.124.4`, `uvicorn==0.33.0`, `mcp==1.26.0`,
    `pydantic==2.12.5`, and the hashed transitive closure.
  - `ansible/roles/synapse/files/requirements.lock` and
    `requirements-aarch64-qemu.lock`: include `matrix-synapse==1.152.1`,
    `psycopg2-binary==2.9.12`, `pydantic==2.13.4`,
    `python-multipart==0.0.28`, `pyyaml==6.0.3`, and the hashed
    transitive closure.

npm scan locks:

- `supply-chain/locks/npm/*-tree.txt` records `npm ls --all`.
- `supply-chain/locks/npm/<tool>/package-lock.json` is scan-only input for
  Dependabot/npm audit.

Skill content:

- ClawHub/Skillflag-origin optional skills are vendored under
  `optional-skills/`.
- `optional-skills/manifest.json` records each approved directory hash.
- `open_strix/skill_integrity.py` rejects ClawHub/Skillflag-origin skills that
  are missing from the manifest or whose hash has drifted.

Advisory scanning:

- `.github/dependabot.yml` enables weekly `uv` and `npm` scans.
- Triage and credential-rotation procedure:
  `kaleidoscope/docs/supply-chain-advisory-triage.md`.
- GitHub's supported ecosystem list documents `uv` and `npm` support:
  https://docs.github.com/en/code-security/dependabot/ecosystems-supported-by-dependabot/supported-ecosystems-and-repositories

## Bump Runbooks

### CDN + SRI

1. Change only the versioned URL.
2. Fetch the exact URL and regenerate SRI:

   ```bash
   url='https://cdn.jsdelivr.net/npm/pkg@x.y.z/path/file.js'
   curl -fsSL "$url" | openssl dgst -sha384 -binary | openssl base64 -A
   ```

3. Write the value as `integrity="sha384-..."`.
4. Keep `crossorigin="anonymous"` on CDN assets.
5. Run a grep gate:

   ```bash
   rg -n 'https://(cdn|unpkg|jsdelivr|cdnjs)\\.' . -g '!**/.venv/**'
   ```

6. Smoke the page that loads the asset. If the browser reports an SRI mismatch,
   do not remove integrity; re-fetch the URL, verify the version/path, and check
   whether the CDN package layout changed.

### Fonts

1. Fetch from the upstream font repo, not Google Fonts CSS.
2. Keep the license-compatible `.woff2` assets vendored beside `fonts.css`.
3. Replace all `fonts.googleapis.com` and `fonts.gstatic.com` references.
4. Run:

   ```bash
   rg -n 'fonts\\.(googleapis|gstatic)\\.com' .
   ```

### npx / Bootstrap Tools

1. Resolve the target version:

   ```bash
   npm view <package> version
   ```

2. Update every copy-paste command and subprocess invocation to
   `npx <package>@<exact-version>`.
3. Regenerate scan artifacts:

   ```bash
   npm install --package-lock-only --ignore-scripts --no-audit --no-fund <package>@<version>
   npm ls --all --omit=dev
   npm audit --package-lock-only
   ```

4. Smoke a read-only command such as `--help`, `inspect`, or `sessions show`.

### PyPI

1. **Check `[tool.uv.sources]` first.** If the dep being pinned has an entry
   in that block (i.e. it is sourced from a git URL, not the PyPI registry),
   STOP — see "Internal forks" below. Do not change its `dependencies = [...]`
   entry, and do NOT delete its `[tool.uv.sources]` block.
2. Update the exact direct pin in `pyproject.toml`.
3. Run `uv lock` in that project directory.
4. If Ansible deploys the project, regenerate `requirements.lock` using the
   command in that file's header.
5. If the project is installed locally by Ansible, keep build backends in the
   requirements export and keep the package install on
   `--no-deps --no-build-isolation`.
6. Run focused tests and affected deploy syntax checks:

   ```bash
   make deploy-check PLAYBOOK=<playbook>.yml ARGS='--syntax-check'
   ```

### Internal forks (`[tool.uv.sources]`)

Internal forks of trusted upstream projects — e.g. `jptreen/open-strix` — are
intentionally NOT subject to the exact-pinning rule. The rule defends against
external maintainer/registry compromise; an internal fork's threat surface is
our own commit history and ssh credentials, not a public package registry.

The convention:

```toml
# pyproject.toml
dependencies = [
    "open-strix",                                # bare name, no version
]

[tool.uv.sources]
open-strix = { git = "https://github.com/jptreen/open-strix", branch = "main" }
```

`uv lock` resolves the bare name through the source override and pins it to a
specific commit SHA in `uv.lock`. The lock IS the reproducibility surface; the
`pyproject.toml` entry is an identity statement ("this dep is ours, track our
trunk"), not a version constraint.

Do NOT:

- Pin the dep in `dependencies = [...]` (e.g. `open-strix==0.1.43`). PyPI's
  `0.1.43` is upstream's wheel, not the fork's — see incident `tony-dvy`.
- Delete the `[tool.uv.sources]` block as part of a dep-pinning sweep.
- SHA-pin the fork in `[tool.uv.sources]`. Branch-tracking is intentional;
  `uv.lock` carries the SHA. SHA-pinning here forces a manual bump for every
  fork commit, which negates the fork's purpose.

Bump procedure for an internal fork:

1. Land changes on the fork's tracked branch (e.g. `main` on
   `jptreen/open-strix`).
2. In the consumer project (e.g. `tony`):

   ```bash
   uv lock --upgrade-package <dep-name>
   ```

   This re-resolves the bare name through the source override and updates
   `uv.lock` to the fork's new HEAD SHA.
3. Commit the `uv.lock` change and deploy as usual.

If a future dep-pinning sweep encounters a `[tool.uv.sources]` entry, the
correct action is **leave it alone**. The inline tripwire comments next to
the override block (in consumer projects) reiterate this; both `tony` and
`open-strix` projects carry them.

### ClawHub / Skillflag Skills

1. Inspect before installing:

   ```bash
   npx clawhub@0.15.0 inspect <slug> --files
   npx clawhub@0.15.0 inspect <slug> --file SKILL.md
   <tool> --skill export <id> > /tmp/<id>-skill.tar
   tar -tf /tmp/<id>-skill.tar
   ```

2. Review `SKILL.md`, scripts, templates, and any network/file operations.
3. Vendor the reviewed directory under `optional-skills/`.
4. Update `optional-skills/manifest.json` with the new directory hash.
5. Start open-strix once; startup must refuse unmanifested ClawHub/Skillflag
   origins and accept the manifest-listed copy.

Treat every new skill as code review. It enters the agent's system prompt and
can carry runnable scripts.

## Pre-Add Gate

Before adding a new third-party entry point:

- CDN script/link: exact version and SRI, or self-host it.
- npm/`npx`: exact version and scan lock.
- PyPI: exact direct pin and `uv.lock`. **Exception:** internal forks
  declared via `[tool.uv.sources]` use bare names + branch-tracking — see the
  "Internal forks" subsection under PyPI Bump Runbooks.
- Ansible `pip`: hash-pinned requirements or no-deps local install backed by a
  lock.
- Skill content: inspected, vendored, manifest-hashed.
- Scanner coverage: Dependabot or a documented equivalent.

If a scanner finds a no-fix advisory in an optional example, document whether it
is runtime-active. Do not enable optional credential-bearing examples until a
fixed package exists.
