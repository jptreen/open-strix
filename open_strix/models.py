from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentEvent:
    event_type: str
    prompt: str
    channel_id: str | None = None
    channel_name: str | None = None
    channel_conversation_type: str | None = None
    channel_visibility: str | None = None
    author: str | None = None
    author_id: str | None = None
    attachment_names: list[str] = field(default_factory=list)
    scheduler_name: str | None = None
    dedupe_key: str | None = None
    source_id: str | None = None
    source_platform: str | None = None
    channel_type: str | None = None
    # Whether the event originates from a bot (not a human). Discord's
    # handle_discord_message pulls this from message.author.bot; the
    # poller path now carries it through so other bots posting in a
    # subscribed channel are recorded with is_bot=True rather than the
    # previous hard-coded False.
    is_bot: bool = False
    # Real wall-clock timestamp of the source message as ISO 8601 UTC
    # ("2026-04-23T15:59:01.234567+00:00"). Absent means "unknown" and
    # _remember_message falls back to _utc_now_iso() (ingestion time).
    # Poller events now carry the platform's real timestamp so chronological
    # reasoning across bot downtime is accurate.
    timestamp: str | None = None
