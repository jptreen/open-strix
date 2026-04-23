"""Phone book for resolving Discord user IDs and channel IDs.

Auto-populates from guild data on startup, incrementally updates as new
users are seen in messages.  Persists as ``state/phone-book.md``.

Supports optional enrichment via ``state/people.jsonl`` and
``state/channels.jsonl`` — operator-curated files that add cross-platform
aliases (Bluesky handles, emails, nicknames, etc.) to the auto-populated
Discord data.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PhoneBookEntry:
    id: str
    name: str
    kind: str  # "user" or "channel"
    is_bot: bool = False
    extra: str = ""  # e.g. channel type, roles
    aliases: dict[str, str] = field(default_factory=dict)  # platform -> handle


@dataclass
class PhoneBook:
    entries: dict[str, PhoneBookEntry] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, entry: PhoneBookEntry) -> bool:
        """Add or update an entry.  Returns True if the book changed."""
        existing = self.entries.get(entry.id)
        if existing is not None:
            changed = False
            if existing.name != entry.name:
                existing.name = entry.name
                changed = True
            if existing.extra != entry.extra and entry.extra:
                existing.extra = entry.extra
                changed = True
            if existing.is_bot != entry.is_bot:
                existing.is_bot = entry.is_bot
                changed = True
            return changed
        self.entries[entry.id] = entry
        return True

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def lookup(self, query: str) -> list[PhoneBookEntry]:
        """Search by name (substring, case-insensitive) or exact ID."""
        query_lower = query.lower().strip()
        # Strip mention formatting if present
        id_match = re.match(r"<[@#]!?(\d+)>", query)
        if id_match:
            query_lower = id_match.group(1)

        results: list[PhoneBookEntry] = []
        for entry in self.entries.values():
            if entry.id == query_lower:
                results.append(entry)
            elif query_lower in entry.name.lower():
                results.append(entry)
        return results

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def render_markdown(self) -> str:
        users = sorted(
            (e for e in self.entries.values() if e.kind == "user"),
            key=lambda e: e.name.lower(),
        )
        channels = sorted(
            (e for e in self.entries.values() if e.kind == "channel"),
            key=lambda e: e.name.lower(),
        )

        lines: list[str] = ["# Phone Book", "", "Auto-generated. Updated as new users and channels are discovered.", ""]

        if users:
            lines.append("## Users")
            lines.append("")
            lines.append("| Name | ID | Mention | Bot |")
            lines.append("|------|-----|---------|-----|")
            for u in users:
                mention = f"`<@{u.id}>`"
                bot_label = "yes" if u.is_bot else ""
                lines.append(f"| {u.name} | {u.id} | {mention} | {bot_label} |")
            lines.append("")

        if channels:
            lines.append("## Channels")
            lines.append("")
            lines.append("| Name | ID | Type |")
            lines.append("|------|-----|------|")
            for c in channels:
                lines.append(f"| {c.name} | {c.id} | {c.extra} |")
            lines.append("")

        lines.append("## Usage")
        lines.append("")
        lines.append("- To mention a user in send_message: use `<@USER_ID>` (e.g. `<@123456>`)")
        lines.append("- To send to a channel: use the channel ID as the `channel_id` parameter")
        lines.append("- To look up a user or channel: use the `lookup` tool")
        lines.append("")

        return "\n".join(lines)

    @classmethod
    def parse_markdown(cls, text: str) -> PhoneBook:
        """Parse a phone-book.md back into a PhoneBook.  Best-effort."""
        book = cls()
        section = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("## Users"):
                section = "users"
                continue
            if stripped.startswith("## Channels"):
                section = "channels"
                continue
            if stripped.startswith("## "):
                section = ""
                continue
            if not stripped.startswith("|") or stripped.startswith("|--") or stripped.startswith("| Name"):
                continue

            cells = [c.strip() for c in stripped.split("|")]
            # cells[0] is empty (before first |), cells[-1] may be empty too
            cells = [c for c in cells if c]

            if section == "users" and len(cells) >= 3:
                name = cells[0]
                id_ = cells[1]
                is_bot = len(cells) >= 4 and cells[3].lower() == "yes"
                book.add(PhoneBookEntry(id=id_, name=name, kind="user", is_bot=is_bot))
            elif section == "channels" and len(cells) >= 2:
                name = cells[0]
                id_ = cells[1]
                extra = cells[2] if len(cells) >= 3 else ""
                book.add(PhoneBookEntry(id=id_, name=name, kind="channel", extra=extra))

        return book


# ------------------------------------------------------------------
# Discord integration helpers
# ------------------------------------------------------------------


def populate_from_guilds(book: PhoneBook, guilds: list[Any]) -> bool:
    """Add channels from all guilds the bot can see.  Returns True if anything changed."""
    changed = False
    for guild in guilds:
        # Channels (no special intent needed)
        for channel in getattr(guild, "channels", []):
            channel_type = str(getattr(channel, "type", "")).replace("ChannelType.", "")
            if channel_type in ("category",):
                continue
            entry = PhoneBookEntry(
                id=str(channel.id),
                name=getattr(channel, "name", str(channel.id)),
                kind="channel",
                extra=channel_type,
            )
            if book.add(entry):
                changed = True

        # Members — only cached members (requires members intent for full list)
        for member in getattr(guild, "members", []):
            entry = PhoneBookEntry(
                id=str(member.id),
                name=str(getattr(member, "display_name", getattr(member, "name", str(member.id)))),
                kind="user",
                is_bot=bool(getattr(member, "bot", False)),
            )
            if book.add(entry):
                changed = True

    return changed


def update_from_message(book: PhoneBook, author: Any) -> bool:
    """Add or update a user entry from a message author.  Returns True if changed."""
    if author is None:
        return False
    author_id = str(getattr(author, "id", "")).strip()
    if not author_id:
        return False
    name = str(getattr(author, "display_name", getattr(author, "name", author_id)))
    is_bot = bool(getattr(author, "bot", False))
    return book.add(PhoneBookEntry(id=author_id, name=name, kind="user", is_bot=is_bot))


def update_from_fields(
    book: PhoneBook,
    *,
    author_id: str | None,
    name: str | None,
    is_bot: bool = False,
) -> bool:
    """Add or update a user entry from plain fields (poller path).

    Discord's update_from_message relies on the duck-typed Discord message
    author object. Poller-driven channels don't have that — they produce
    plain JSON — so this is the parallel entry point the scheduler uses
    to enrich the phone book after parsing poller events. Returns True
    if the book changed.
    """
    if not author_id:
        return False
    author_id = str(author_id).strip()
    if not author_id:
        return False
    display = str(name).strip() if name else author_id
    return book.add(
        PhoneBookEntry(id=author_id, name=display, kind="user", is_bot=bool(is_bot)),
    )


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------


def load_phone_book(path: Path) -> PhoneBook:
    """Load phone book from a markdown file."""
    if not path.exists():
        return PhoneBook()
    text = path.read_text(encoding="utf-8")
    return PhoneBook.parse_markdown(text)


def save_phone_book(book: PhoneBook, path: Path) -> None:
    """Save phone book as a markdown file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(book.render_markdown(), encoding="utf-8")


def export_to_jsonl(book: PhoneBook, people_path: Path, channels_path: Path) -> tuple[int, int]:
    """Export the current phone book as starter JSONL files.

    Creates ``people.jsonl`` and ``channels.jsonl`` from the auto-populated
    phone book so operators have a starting point to add cross-platform
    aliases (Bluesky handles, emails, etc.).

    Skips writing a file if it already exists (never overwrites).
    Returns (people_count, channels_count) of records written.
    """
    people_count = 0
    channels_count = 0

    users = [e for e in book.entries.values() if e.kind == "user"]
    channels = [e for e in book.entries.values() if e.kind == "channel"]

    if people_path.exists():
        logger.info("Skipping %s — already exists (%d users not exported)",
                     people_path.name, len(users))
    else:
        people_path.parent.mkdir(parents=True, exist_ok=True)
        with people_path.open("w", encoding="utf-8") as fh:
            for u in sorted(users, key=lambda e: e.name.lower()):
                record: dict[str, Any] = {
                    "name": u.name,
                    "type": "bot" if u.is_bot else "human",
                    "discord_id": u.id,
                    "discord_mention": f"<@{u.id}>",
                    "discord_display": u.name,
                    "bluesky": "",
                    "google_docs_name": "",
                    "google_docs_email": "",
                    "notes": "",
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                people_count += 1
        logger.info("Exported %d people to %s", people_count, people_path)

    if channels_path.exists():
        logger.info("Skipping %s — already exists (%d channels not exported)",
                     channels_path.name, len(channels))
    else:
        channels_path.parent.mkdir(parents=True, exist_ok=True)
        with channels_path.open("w", encoding="utf-8") as fh:
            for c in sorted(channels, key=lambda e: e.name.lower()):
                record = {
                    "name": c.name,
                    "discord_id": c.id,
                    "aliases": [],
                    "who": "",
                    "notes": c.extra or "",
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                channels_count += 1
        logger.info("Exported %d channels to %s", channels_count, channels_path)

    return people_count, channels_count


# ------------------------------------------------------------------
# JSONL enrichment — cross-platform aliases
# ------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file, returning a list of dicts.  Empty list if missing."""
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, 1):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping malformed JSON at %s:%d: %s", path, line_num, exc)
    if records:
        logger.info("Loaded %d records from %s", len(records), path.name)
    return records


def enrich_from_jsonl(book: PhoneBook, people_path: Path, channels_path: Path) -> None:
    """Merge operator-curated JSONL aliases into the phone book.

    ``people.jsonl`` records should have at minimum ``name`` and optionally:
    ``discord_id``, ``discord_display``, ``bluesky``, ``google_docs_name``,
    ``google_docs_email``, ``notes``, and any other key-value pairs.

    ``channels.jsonl`` records should have ``name``, ``discord_id``, and
    optionally ``aliases`` (list of nicknames) and ``notes``.
    """
    for person in _load_jsonl(people_path):
        discord_id = str(person.get("discord_id", "")).strip()
        name = str(person.get("name", "")).strip()
        if not name:
            continue

        # Build aliases dict from all extra fields
        aliases: dict[str, str] = {}
        if person.get("discord_display"):
            aliases["Discord"] = str(person["discord_display"])
        if person.get("bluesky"):
            aliases["Bluesky"] = str(person["bluesky"])
        if person.get("google_docs_name"):
            aliases["Docs"] = str(person["google_docs_name"])
        if person.get("google_docs_email"):
            aliases["Email"] = str(person["google_docs_email"])

        if discord_id and discord_id in book.entries:
            # Merge aliases into existing auto-populated entry
            book.entries[discord_id].aliases.update(aliases)
        elif discord_id:
            # Create new entry from JSONL
            is_bot = bool(person.get("type") == "bot" or person.get("is_bot"))
            book.add(PhoneBookEntry(
                id=discord_id, name=name, kind="user",
                is_bot=is_bot, aliases=aliases,
            ))
        # People without discord_id are stored as alias-only entries
        # keyed by name for prompt rendering but not in the ID-based book

    for channel in _load_jsonl(channels_path):
        discord_id = str(channel.get("discord_id", "")).strip()
        name = str(channel.get("name", "")).strip()
        if not name or not discord_id:
            continue
        channel_aliases = channel.get("aliases", [])
        aliases_dict: dict[str, str] = {}
        if isinstance(channel_aliases, list) and channel_aliases:
            aliases_dict["aka"] = ", ".join(str(a) for a in channel_aliases)
        if channel.get("notes"):
            aliases_dict["notes"] = str(channel["notes"])

        if discord_id in book.entries:
            book.entries[discord_id].aliases.update(aliases_dict)
        else:
            book.add(PhoneBookEntry(
                id=discord_id, name=name, kind="channel",
                aliases=aliases_dict,
            ))


def _format_person_line(entry: PhoneBookEntry) -> str:
    """Format a single person as a compact alias line."""
    parts = []
    if entry.aliases.get("Discord"):
        parts.append(f"Discord: {entry.aliases['Discord']}")
    if entry.aliases.get("Bluesky"):
        parts.append(f"Bluesky: {entry.aliases['Bluesky']}")
    if entry.aliases.get("Docs"):
        parts.append(f"Docs: {entry.aliases['Docs']}")
    if entry.aliases.get("Email"):
        parts.append(f"Email: {entry.aliases['Email']}")
    parts.append(f"ID: {entry.id}")
    detail = f" ({', '.join(parts)})" if parts else f" (ID: {entry.id})"
    bot_tag = " [bot]" if entry.is_bot else ""
    return f"- {entry.name}{bot_tag}{detail}"


def _format_channel_line(entry: PhoneBookEntry) -> str:
    """Format a single channel as a compact alias line."""
    parts = [f"ID: {entry.id}"]
    if entry.aliases.get("aka"):
        parts.append(f"aka: {entry.aliases['aka']}")
    return f"- {entry.name} ({', '.join(parts)})"


def render_aliases_block(book: PhoneBook) -> str:
    """Render a compact [PEOPLE] + [CHANNELS] block for prompt injection.

    This block is included in every turn prompt so the agent always has
    cross-platform alias context visible — preventing attribution errors
    from mismatched display names across Discord, Bluesky, email, etc.
    """
    users = sorted(
        (e for e in book.entries.values() if e.kind == "user"),
        key=lambda e: e.name.lower(),
    )
    channels = sorted(
        (e for e in book.entries.values() if e.kind == "channel"),
        key=lambda e: e.name.lower(),
    )

    lines: list[str] = []
    if users:
        lines.append("[PEOPLE — unified aliases across all platforms]")
        for u in users:
            lines.append(_format_person_line(u))

    if channels:
        if lines:
            lines.append("")
        lines.append("[CHANNELS — Discord channel aliases and IDs]")
        for c in channels:
            lines.append(_format_channel_line(c))

    return "\n".join(lines)
