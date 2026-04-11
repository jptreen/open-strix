from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import open_strix.app as app_mod
from open_strix.phone_book import (
    PhoneBook,
    PhoneBookEntry,
    enrich_from_jsonl,
    export_to_jsonl,
    load_phone_book,
    populate_from_guilds,
    render_aliases_block,
    save_phone_book,
    update_from_message,
)


class DummyAgent:
    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": []}


def _stub_agent_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())


# ------------------------------------------------------------------
# PhoneBook unit tests
# ------------------------------------------------------------------


def test_add_new_entry() -> None:
    book = PhoneBook()
    changed = book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    assert changed is True
    assert "123" in book.entries
    assert book.entries["123"].name == "alice"


def test_add_same_entry_no_change() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    changed = book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    assert changed is False


def test_add_updates_name() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    changed = book.add(PhoneBookEntry(id="123", name="Alice K", kind="user"))
    assert changed is True
    assert book.entries["123"].name == "Alice K"


def test_lookup_by_id() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    book.add(PhoneBookEntry(id="456", name="bob", kind="user"))
    results = book.lookup("123")
    assert len(results) == 1
    assert results[0].name == "alice"


def test_lookup_by_name_substring() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    book.add(PhoneBookEntry(id="456", name="bob", kind="user"))
    results = book.lookup("ali")
    assert len(results) == 1
    assert results[0].name == "alice"


def test_lookup_case_insensitive() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="Alice", kind="user"))
    results = book.lookup("ALICE")
    assert len(results) == 1


def test_lookup_by_mention_format() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    results = book.lookup("<@123>")
    assert len(results) == 1
    assert results[0].name == "alice"


def test_lookup_by_mention_with_exclamation() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    results = book.lookup("<@!123>")
    assert len(results) == 1


def test_lookup_channel_mention() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="999", name="general", kind="channel"))
    results = book.lookup("<#999>")
    assert len(results) == 1
    assert results[0].name == "general"


def test_lookup_no_results() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    results = book.lookup("charlie")
    assert len(results) == 0


# ------------------------------------------------------------------
# Markdown serialization
# ------------------------------------------------------------------


def test_render_and_parse_roundtrip() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user", is_bot=False))
    book.add(PhoneBookEntry(id="456", name="bot-helper", kind="user", is_bot=True))
    book.add(PhoneBookEntry(id="999", name="general", kind="channel", extra="text"))
    book.add(PhoneBookEntry(id="888", name="voice-chat", kind="channel", extra="voice"))

    md = book.render_markdown()
    parsed = PhoneBook.parse_markdown(md)

    assert len(parsed.entries) == 4
    assert parsed.entries["123"].name == "alice"
    assert parsed.entries["123"].is_bot is False
    assert parsed.entries["456"].is_bot is True
    assert parsed.entries["999"].kind == "channel"
    assert parsed.entries["999"].extra == "text"


def test_render_includes_mention_format() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    md = book.render_markdown()
    assert "`<@123>`" in md


def test_render_includes_usage_instructions() -> None:
    book = PhoneBook()
    md = book.render_markdown()
    assert "<@USER_ID>" in md
    assert "lookup" in md.lower()


# ------------------------------------------------------------------
# File persistence
# ------------------------------------------------------------------


def test_save_and_load_phone_book(tmp_path: Path) -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    book.add(PhoneBookEntry(id="999", name="general", kind="channel", extra="text"))

    path = tmp_path / "state" / "phone-book.md"
    save_phone_book(book, path)

    loaded = load_phone_book(path)
    assert len(loaded.entries) == 2
    assert loaded.entries["123"].name == "alice"
    assert loaded.entries["999"].kind == "channel"


def test_load_nonexistent_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.md"
    book = load_phone_book(path)
    assert len(book.entries) == 0


# ------------------------------------------------------------------
# Discord integration helpers
# ------------------------------------------------------------------


def test_populate_from_guilds() -> None:
    channel1 = SimpleNamespace(id=100, name="general", type="text")
    channel2 = SimpleNamespace(id=101, name="voice", type="voice")
    category = SimpleNamespace(id=102, name="Info", type="category")
    member1 = SimpleNamespace(id=200, display_name="alice", name="alice#1234", bot=False)
    member2 = SimpleNamespace(id=201, display_name="bot-helper", name="bot-helper#0000", bot=True)
    guild = SimpleNamespace(channels=[channel1, channel2, category], members=[member1, member2])

    book = PhoneBook()
    changed = populate_from_guilds(book, [guild])

    assert changed is True
    # Category should be excluded
    assert "102" not in book.entries
    # Channels
    assert book.entries["100"].name == "general"
    assert book.entries["100"].kind == "channel"
    assert book.entries["101"].name == "voice"
    # Users
    assert book.entries["200"].name == "alice"
    assert book.entries["200"].is_bot is False
    assert book.entries["201"].name == "bot-helper"
    assert book.entries["201"].is_bot is True


def test_populate_from_guilds_no_change_on_duplicate() -> None:
    channel = SimpleNamespace(id=100, name="general", type="text")
    guild = SimpleNamespace(channels=[channel], members=[])

    book = PhoneBook()
    populate_from_guilds(book, [guild])
    changed = populate_from_guilds(book, [guild])
    assert changed is False


def test_update_from_message() -> None:
    author = SimpleNamespace(id=300, display_name="carol", name="carol#5678", bot=False)
    book = PhoneBook()
    changed = update_from_message(book, author)
    assert changed is True
    assert book.entries["300"].name == "carol"


def test_update_from_message_with_mentions() -> None:
    """Verify that mentioned users can be individually added."""
    book = PhoneBook()
    mentioned = SimpleNamespace(id=400, display_name="dave", name="dave#0000", bot=False)
    changed = update_from_message(book, mentioned)
    assert changed is True
    assert book.entries["400"].name == "dave"


def test_update_from_message_none_author() -> None:
    book = PhoneBook()
    changed = update_from_message(book, None)
    assert changed is False


# ------------------------------------------------------------------
# Integration with OpenStrixApp
# ------------------------------------------------------------------


def test_app_initializes_phone_book(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    assert hasattr(app, "phone_book")
    assert isinstance(app.phone_book, PhoneBook)


def test_app_loads_existing_phone_book(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)

    # Pre-populate a phone book file
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    save_phone_book(book, state_dir / "phone-book.md")

    app = app_mod.OpenStrixApp(tmp_path)
    assert "123" in app.phone_book.entries
    assert app.phone_book.entries["123"].name == "alice"


@pytest.mark.asyncio
async def test_handle_discord_message_updates_phone_book(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAuthor:
        id = 555
        display_name = "eve"
        name = "eve#1234"
        bot = False

        def __str__(self) -> str:
            return "eve"

    class FakeChannel:
        def __init__(self) -> None:
            self.id = 999

    message = SimpleNamespace(
        id=12345,
        content="hello",
        channel=FakeChannel(),
        author=FakeAuthor(),
        attachments=[],
        mentions=[],
    )

    await app.handle_discord_message(message)

    # Author should be in the phone book now
    assert "555" in app.phone_book.entries
    assert app.phone_book.entries["555"].name == "eve"

    # Phone book should be persisted
    loaded = load_phone_book(app.layout.phone_book_file)
    assert "555" in loaded.entries


@pytest.mark.asyncio
async def test_handle_discord_message_captures_mentioned_users(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAuthor:
        id = 555
        display_name = "eve"
        name = "eve#1234"
        bot = False

        def __str__(self) -> str:
            return "eve"

    class FakeMentionedUser:
        id = 666
        display_name = "frank"
        name = "frank#5678"
        bot = False

    message = SimpleNamespace(
        id=12345,
        content="hey <@666>",
        channel=SimpleNamespace(id=999),
        author=FakeAuthor(),
        attachments=[],
        mentions=[FakeMentionedUser()],
    )

    await app.handle_discord_message(message)

    assert "555" in app.phone_book.entries
    assert "666" in app.phone_book.entries
    assert app.phone_book.entries["666"].name == "frank"


def test_lookup_tool_is_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    tools = {tool.name: tool for tool in app._build_tools()}
    assert "lookup" in tools


def test_lookup_tool_finds_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    app.phone_book.add(PhoneBookEntry(id="123", name="alice", kind="user"))

    tools = {tool.name: tool for tool in app._build_tools()}
    result = tools["lookup"].invoke({"query": "alice"})
    assert "alice" in result
    assert "123" in result
    assert "<@123>" in result


def test_lookup_tool_finds_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    app.phone_book.add(PhoneBookEntry(id="999", name="general", kind="channel", extra="text"))

    tools = {tool.name: tool for tool in app._build_tools()}
    result = tools["lookup"].invoke({"query": "general"})
    assert "general" in result
    assert "999" in result
    assert "Channel" in result


def test_lookup_tool_no_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    tools = {tool.name: tool for tool in app._build_tools()}
    result = tools["lookup"].invoke({"query": "nobody"})
    assert "No matches" in result


def test_phone_book_file_in_layout() -> None:
    from open_strix.config import RepoLayout

    layout = RepoLayout(home=Path("/fake"), state_dir_name="state")
    assert layout.phone_book_file == Path("/fake/state/phone-book.md")


def test_phone_book_extra_file_in_layout() -> None:
    from open_strix.config import RepoLayout

    layout = RepoLayout(home=Path("/fake"), state_dir_name="state")
    assert layout.phone_book_extra_file == Path("/fake/state/phone-book.extra.md")


def test_phone_book_extra_created_on_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    extra_path = app.layout.phone_book_extra_file
    assert extra_path.exists()
    content = extra_path.read_text(encoding="utf-8")
    assert "Manual Notes" in content
    assert "Channel Notes" in content
    assert "External Comms" in content
    assert "People Notes" in content


def test_phone_book_extra_not_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure the extra phone book is never overwritten once it exists."""
    _stub_agent_factory(monkeypatch)
    # Create the app once (creates the extra file)
    app_mod.OpenStrixApp(tmp_path)
    extra_path = tmp_path / "state" / "phone-book.extra.md"
    # Manually edit the file
    extra_path.write_text("My custom notes", encoding="utf-8")
    # Create app again (simulates restart)
    app_mod.OpenStrixApp(tmp_path)
    assert extra_path.read_text(encoding="utf-8") == "My custom notes"


# ------------------------------------------------------------------
# JSONL enrichment tests
# ------------------------------------------------------------------


def test_enrich_from_jsonl_merges_aliases(tmp_path: Path) -> None:
    """JSONL enrichment adds cross-platform aliases to existing entries."""
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))

    people = tmp_path / "people.jsonl"
    people.write_text(
        '{"name": "Alice Smith", "discord_id": "123", "bluesky": "@alice.bsky.social", "discord_display": "alice"}\n',
        encoding="utf-8",
    )
    channels = tmp_path / "channels.jsonl"

    enrich_from_jsonl(book, people, channels)

    entry = book.entries["123"]
    assert entry.aliases["Bluesky"] == "@alice.bsky.social"
    assert entry.aliases["Discord"] == "alice"


def test_enrich_from_jsonl_creates_new_entries(tmp_path: Path) -> None:
    """JSONL creates entries for people not yet in the phone book."""
    book = PhoneBook()

    people = tmp_path / "people.jsonl"
    people.write_text(
        '{"name": "Bob", "discord_id": "456", "bluesky": "@bob.bsky.social"}\n',
        encoding="utf-8",
    )
    channels = tmp_path / "channels.jsonl"

    enrich_from_jsonl(book, people, channels)

    assert "456" in book.entries
    assert book.entries["456"].name == "Bob"
    assert book.entries["456"].aliases["Bluesky"] == "@bob.bsky.social"


def test_enrich_from_jsonl_channels(tmp_path: Path) -> None:
    """JSONL channel enrichment adds aliases to channels."""
    book = PhoneBook()
    book.add(PhoneBookEntry(id="999", name="general", kind="channel"))

    people = tmp_path / "people.jsonl"
    channels = tmp_path / "channels.jsonl"
    channels.write_text(
        '{"name": "General Chat", "discord_id": "999", "aliases": ["main", "general", "home"]}\n',
        encoding="utf-8",
    )

    enrich_from_jsonl(book, people, channels)

    entry = book.entries["999"]
    assert "aka" in entry.aliases
    assert "main" in entry.aliases["aka"]
    assert "general" in entry.aliases["aka"]


def test_enrich_from_jsonl_missing_files(tmp_path: Path) -> None:
    """Enrichment with missing files is a no-op."""
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))

    enrich_from_jsonl(
        book,
        tmp_path / "nonexistent_people.jsonl",
        tmp_path / "nonexistent_channels.jsonl",
    )

    # Book unchanged
    assert len(book.entries) == 1
    assert book.entries["123"].aliases == {}


def test_enrich_from_jsonl_bot_type(tmp_path: Path) -> None:
    """JSONL entries with type=bot are marked as bots."""
    book = PhoneBook()

    people = tmp_path / "people.jsonl"
    people.write_text(
        '{"name": "Strix", "discord_id": "789", "type": "bot"}\n',
        encoding="utf-8",
    )
    channels = tmp_path / "channels.jsonl"

    enrich_from_jsonl(book, people, channels)

    assert book.entries["789"].is_bot is True


# ------------------------------------------------------------------
# Aliases block rendering tests
# ------------------------------------------------------------------


def test_render_aliases_block_empty() -> None:
    """Empty phone book renders empty string."""
    book = PhoneBook()
    assert render_aliases_block(book) == ""


def test_render_aliases_block_users_and_channels() -> None:
    """Aliases block includes both people and channels sections."""
    book = PhoneBook()
    book.add(PhoneBookEntry(
        id="123", name="Alice", kind="user",
        aliases={"Discord": "alice", "Bluesky": "@alice.bsky.social"},
    ))
    book.add(PhoneBookEntry(
        id="999", name="general", kind="channel",
        aliases={"aka": "main, home"},
    ))

    block = render_aliases_block(book)
    assert "[PEOPLE" in block
    assert "[CHANNELS" in block
    assert "Alice" in block
    assert "@alice.bsky.social" in block
    assert "general" in block
    assert "main, home" in block


def test_render_aliases_block_bot_tag() -> None:
    """Bot entries get a [bot] tag in the aliases block."""
    book = PhoneBook()
    book.add(PhoneBookEntry(
        id="789", name="Strix", kind="user", is_bot=True,
        aliases={"Bluesky": "@strix.example"},
    ))

    block = render_aliases_block(book)
    assert "[bot]" in block
    assert "Strix" in block


# ------------------------------------------------------------------
# Layout property tests for JSONL paths
# ------------------------------------------------------------------


def test_people_jsonl_in_layout() -> None:
    from open_strix.config import RepoLayout

    layout = RepoLayout(home=Path("/fake"), state_dir_name="state")
    assert layout.people_jsonl == Path("/fake/state/people.jsonl")


def test_channels_jsonl_in_layout() -> None:
    from open_strix.config import RepoLayout

    layout = RepoLayout(home=Path("/fake"), state_dir_name="state")
    assert layout.channels_jsonl == Path("/fake/state/channels.jsonl")


# ------------------------------------------------------------------
# App integration tests for JSONL enrichment
# ------------------------------------------------------------------


def test_app_loads_jsonl_enrichment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App loads JSONL enrichment at startup and merges into phone book."""
    _stub_agent_factory(monkeypatch)

    # Pre-populate phone book + JSONL
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    save_phone_book(book, state_dir / "phone-book.md")

    (state_dir / "people.jsonl").write_text(
        '{"name": "Alice Smith", "discord_id": "123", "bluesky": "@alice.bsky.social"}\n',
        encoding="utf-8",
    )

    app = app_mod.OpenStrixApp(tmp_path)
    assert app.phone_book.entries["123"].aliases.get("Bluesky") == "@alice.bsky.social"


def test_render_turn_prompt_includes_aliases() -> None:
    """render_turn_prompt includes aliases block when provided."""
    from open_strix.prompts import render_turn_prompt

    result = render_turn_prompt(
        journal_entries=[],
        memory_blocks=[],
        recent_messages=[],
        current_event={"event_type": "message", "prompt": "hello"},
        aliases_block="[PEOPLE]\n- Alice (ID: 123)",
    )
    assert "[PEOPLE]" in result
    assert "Alice" in result
    assert "Known people and channels" in result


def test_render_turn_prompt_omits_aliases_when_empty() -> None:
    """render_turn_prompt skips aliases section when block is empty."""
    from open_strix.prompts import render_turn_prompt

    result = render_turn_prompt(
        journal_entries=[],
        memory_blocks=[],
        recent_messages=[],
        current_event={"event_type": "message", "prompt": "hello"},
    )
    assert "Known people and channels" not in result


# ------------------------------------------------------------------
# export_to_jsonl — phone book → starter JSONL migration
# ------------------------------------------------------------------


def test_export_to_jsonl_creates_files(tmp_path: Path) -> None:
    """export_to_jsonl generates people.jsonl and channels.jsonl from phone book."""
    book = PhoneBook()
    book.add(PhoneBookEntry(id="111", name="Alice", kind="user"))
    book.add(PhoneBookEntry(id="222", name="Bob", kind="user", is_bot=True))
    book.add(PhoneBookEntry(id="333", name="general", kind="channel"))

    people_path = tmp_path / "people.jsonl"
    channels_path = tmp_path / "channels.jsonl"

    p_count, c_count = export_to_jsonl(book, people_path, channels_path)
    assert p_count == 2
    assert c_count == 1
    assert people_path.exists()
    assert channels_path.exists()

    import json
    people = [json.loads(line) for line in people_path.read_text().strip().splitlines()]
    assert len(people) == 2
    alice = next(p for p in people if p["name"] == "Alice")
    assert alice["type"] == "human"
    assert alice["discord_id"] == "111"
    assert alice["bluesky"] == ""  # placeholder

    bob = next(p for p in people if p["name"] == "Bob")
    assert bob["type"] == "bot"

    channels = [json.loads(line) for line in channels_path.read_text().strip().splitlines()]
    assert len(channels) == 1
    assert channels[0]["name"] == "general"


def test_export_to_jsonl_skips_existing(tmp_path: Path) -> None:
    """export_to_jsonl never overwrites existing JSONL files."""
    book = PhoneBook()
    book.add(PhoneBookEntry(id="111", name="Alice", kind="user"))

    people_path = tmp_path / "people.jsonl"
    channels_path = tmp_path / "channels.jsonl"
    people_path.write_text('{"name":"Existing"}\n')

    p_count, c_count = export_to_jsonl(book, people_path, channels_path)
    assert p_count == 0  # skipped because file exists
    assert c_count == 0  # no channels in book
    assert people_path.read_text() == '{"name":"Existing"}\n'  # unchanged


def test_export_then_enrich_roundtrip(tmp_path: Path) -> None:
    """Exported JSONL can be loaded back via enrich_from_jsonl."""
    book = PhoneBook()
    book.add(PhoneBookEntry(id="111", name="Alice", kind="user"))
    book.add(PhoneBookEntry(id="222", name="general", kind="channel"))

    people_path = tmp_path / "people.jsonl"
    channels_path = tmp_path / "channels.jsonl"
    export_to_jsonl(book, people_path, channels_path)

    # Create a fresh book and enrich from the exported files
    fresh_book = PhoneBook()
    enrich_from_jsonl(fresh_book, people_path, channels_path)
    assert "111" in fresh_book.entries
    assert fresh_book.entries["111"].name == "Alice"
    assert "222" in fresh_book.entries
    assert fresh_book.entries["222"].name == "general"


def test_load_jsonl_warns_on_malformed(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Malformed JSON lines produce a warning log."""
    import logging
    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text('{"good": true}\nnot json\n{"also_good": true}\n')

    from open_strix.phone_book import _load_jsonl
    with caplog.at_level(logging.WARNING):
        records = _load_jsonl(bad_file)
    assert len(records) == 2
    assert "malformed JSON" in caplog.text.lower() or "Skipping" in caplog.text
