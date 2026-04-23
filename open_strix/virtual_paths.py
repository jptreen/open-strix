"""Virtual → real path mapping for agent-visible skill paths (tony-ugg).

The deepagents skills middleware (``deepagents.middleware.skills``) renders
paths like ``/skills/<name>/SKILL.md`` into the agent's system prompt.
These are *virtual-mode* paths understood by the deepagents
``FilesystemBackend`` (see ``readonly_backend.WriteGuardBackend``, which
wraps ``FilesystemBackend(virtual_mode=True)``) — the leading slash is
rooted at the agent's home dir, not the host filesystem root.

Deepagents' own ``read_file`` / ``write_file`` / ``edit_file`` /
``glob`` / ``ls`` tools resolve virtual paths correctly because they go
through the backend. Open-strix's custom overrides of the same tools
(in ``tools.py``) bypass the backend and hit the host FS directly — so
when the agent pastes a virtual path from the skills-discovery section
of its prompt, the open-strix tool fails with ``not_found`` while the
file exists at ``{home}{virtual_path}`` on disk.

Historical symptom (2026-04-20, captured in tony-ugg):

  - Agent saw ``/skills/adhd-research/SKILL.md`` in its skill list.
  - ``read_file`` with that path returned ``tool_call_error not_found``
    because ``Path("/skills/adhd-research/SKILL.md").resolve()`` doesn't
    remap against agent home.
  - Agent fell back to ``bash cat /skills/... 2>/dev/null || echo
    "File not readable"`` and interpreted the fallback string as file
    content (fixed separately via prompt hygiene in tony-ywg).
  - Agent then tried ``mkdir -p ~/skills/...`` which created a phantom
    ``/home/tony/skills/`` directory at the wrong location (real
    skills dir is ``/home/tony/tony/skills/``).

Fix: this module's ``resolve_virtual_path`` inspects the path for
known virtual prefixes (``/skills`` and ``BUILTIN_SKILLS_ROUTE``) and
re-roots them at ``home``. Paths without a recognised virtual prefix
fall through to ordinary ``Path.resolve()`` — no behaviour change for
existing callers that pass real absolute or relative paths.

This is narrower than a full backend proxy, which is the principled
long-term fix (Option D in the tony-ugg spec). The narrow remap does
not cover arbitrary nesting, symlinks, or the ``FilesystemBackend``'s
full path-normalisation logic; it covers the specific prefixes that
appear in the agent-facing skill prompt, which is the only source of
virtual paths the agent currently sees.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from .builtin_skills import BUILTIN_HOME_DIRNAME

# Keep in sync with ``readonly_backend.BUILTIN_SKILLS_ROUTE``
# (``/.open_strix_builtin_skills/``) and ``app.py`` which hardcodes
# ``/skills`` as the user-skills virtual source.
_USER_SKILLS_VIRTUAL_ROOT = "/skills"
_BUILTIN_SKILLS_VIRTUAL_ROOT = f"/{BUILTIN_HOME_DIRNAME}"


def _has_virtual_prefix(path: str, virtual_root: str) -> bool:
    """Return True if ``path`` is either ``virtual_root`` exactly or a
    child of it (``{virtual_root}/...``). Matches the full path segment,
    not a substring — so ``/skills-archive/x`` is NOT treated as under
    ``/skills``.
    """
    if path == virtual_root:
        return True
    return path.startswith(virtual_root + "/")


def resolve_virtual_path(path: str, home: Path) -> Path:
    """Resolve a path, remapping known virtual skill prefixes to real
    host paths rooted at ``home``.

    Returns a ``Path`` suitable for direct host-filesystem access
    (``.is_file()``, ``.read_text()``, etc.).

    Examples (assuming ``home = /home/tony/tony``)::

        resolve_virtual_path("/skills/adhd-research/SKILL.md", home)
          → /home/tony/tony/skills/adhd-research/SKILL.md

        resolve_virtual_path("/skills", home)
          → /home/tony/tony/skills

        resolve_virtual_path("/.open_strix_builtin_skills/memory/SKILL.md", home)
          → /home/tony/tony/.open_strix_builtin_skills/memory/SKILL.md

        resolve_virtual_path("/home/tony/tony/skills/x", home)
          → /home/tony/tony/skills/x           # already real, passthrough

        resolve_virtual_path("skills/x", home)
          → <cwd>/skills/x                     # relative, passthrough

        resolve_virtual_path("/etc/passwd", home)
          → /etc/passwd                        # not a virtual prefix

    Path-traversal inputs (e.g. ``/skills/../etc/passwd``) are not
    explicitly blocked here — this function is a path-rewrite helper,
    not a sandbox. Callers that need sandboxing should rely on the
    deepagents filesystem backend, which does its own validation.
    """
    for virtual_root in (_USER_SKILLS_VIRTUAL_ROOT, _BUILTIN_SKILLS_VIRTUAL_ROOT):
        if _has_virtual_prefix(path, virtual_root):
            # Strip leading slash and re-root at home. This matches the
            # ``FilesystemBackend(virtual_mode=True)`` semantics without
            # pulling the whole backend into this tool's call-site.
            remapped = home / path.lstrip("/")
            return remapped.expanduser().resolve()
    return Path(path).expanduser().resolve()


def _token_has_virtual_prefix(token: str) -> str | None:
    """Return the virtual root matched in ``token`` if it starts with a
    virtual skill prefix at a path-segment boundary; else ``None``.
    """
    for virtual_root in (_USER_SKILLS_VIRTUAL_ROOT, _BUILTIN_SKILLS_VIRTUAL_ROOT):
        if token == virtual_root or token.startswith(virtual_root + "/"):
            return virtual_root
    return None


def remap_virtual_paths_in_command(
    command: str,
    home: Path,
) -> tuple[str, list[tuple[str, str]]]:
    """Rewrite virtual skill-root tokens in a shell command.

    Bash commands routinely contain ``/skills/...`` as arguments when
    the agent copies paths out of its skills-discovery prompt. Without
    remapping, ``ls /skills/adhd-research/`` fails with "No such file
    or directory" on the host.

    Uses ``shlex.split`` to tokenize the command the way the shell
    does: quoted strings become single tokens, whitespace splits tokens,
    escapes are honoured. Only whole tokens that start with a virtual
    root (``/skills`` or ``/.open_strix_builtin_skills``) at a path
    boundary get rewritten. Tokens whose content happens to include
    ``/skills/`` but which aren't rooted there (``/etc/skills/foo``,
    ``--grep=/skills/log``, ``'the /skills/ namespace'``) are left
    alone.

    If ``shlex.split`` can't parse the command (unbalanced quotes,
    for instance), the original command is returned unchanged — better
    to let the shell give its own error than to mangle a command we
    don't understand.

    Returns the rewritten command (via ``shlex.join``) and a list of
    ``(original_token, remapped_token)`` substitutions performed —
    useful for logging.
    """
    # Use posix=False + whitespace_split=True so tokens retain their
    # surrounding quote characters. This lets us distinguish ``'/skills/'``
    # (a quoted literal string — a grep pattern or similar, leave alone)
    # from ``/skills/...`` (a bare path argument — remap). shlex.split
    # with posix=True strips the quotes and loses this distinction.
    lexer = shlex.shlex(command, posix=False)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return command, []

    substitutions: list[tuple[str, str]] = []
    remapped_tokens: list[str] = []
    changed = False
    for token in tokens:
        # A quoted token starts with one of `'` / `"` — those are
        # literals the agent explicitly marked as strings. Don't remap.
        if token and token[0] in ("'", '"'):
            remapped_tokens.append(token)
            continue
        if _token_has_virtual_prefix(token) is not None:
            real = str((home / token.lstrip("/")).expanduser().resolve())
            substitutions.append((token, real))
            remapped_tokens.append(real)
            changed = True
        else:
            remapped_tokens.append(token)

    if not changed:
        # No substantive change — return the original command verbatim
        # so downstream logging / display shows what the agent wrote.
        return command, []

    # Re-join with single spaces. posix=False tokens already include
    # whatever quotes were in the source, so no extra quoting needed.
    return " ".join(remapped_tokens), substitutions
