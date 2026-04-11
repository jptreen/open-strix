#!/usr/bin/env python3
"""Example Matrix poller for the channel handler pattern.

Queries an HTTP bridge for decrypted messages and emits JSONL events
with channel_type="matrix" so the framework routes replies back via
the registered channel handler.

Requires a running Matrix bridge that exposes:
  GET /messages?since=<unix-ms>  → {ok, messages: [{sender, room_id, body, event_id, timestamp, encrypted}]}
  POST /send                     → {ok, event_id}

The bridge handles E2EE (olm/megolm), sync, and device management.
This poller only speaks HTTP.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATE_DIR = Path(os.environ.get("STATE_DIR", Path(__file__).resolve().parent))
CURSOR_FILE = STATE_DIR / "cursor.json"
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://127.0.0.1:29317")

# Max event IDs remembered between runs to dedupe ties on the cursor boundary.
SEEN_MAX = 200
HTTP_TIMEOUT = 10


def log(msg: str) -> None:
    print(f"[matrix-poller] {msg}", file=sys.stderr)


def load_cursor() -> dict:
    if not CURSOR_FILE.exists():
        return {"since": int(time.time() * 1000), "seen_event_ids": []}
    try:
        data = json.loads(CURSOR_FILE.read_text())
        data.setdefault("since", 0)
        data.setdefault("seen_event_ids", [])
        return data
    except json.JSONDecodeError as e:
        log(f"cursor corrupt, starting fresh: {e}")
        return {"since": int(time.time() * 1000), "seen_event_ids": []}


def save_cursor(cursor: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CURSOR_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cursor, indent=2))
    tmp.replace(CURSOR_FILE)


def fetch_messages(since: int) -> dict | None:
    url = f"{BRIDGE_URL.rstrip('/')}/messages?" + urllib.parse.urlencode({"since": since})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "open-strix-matrix-poller/1.0"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        log(f"bridge query failed: {e}")
        return None


def main() -> int:
    cursor = load_cursor()
    since = int(cursor.get("since") or 0)
    seen_set = set(cursor.get("seen_event_ids") or [])

    resp = fetch_messages(since)
    if resp is None or not resp.get("ok"):
        return 0

    messages = resp.get("messages") or []
    new_since = since

    for msg in messages:
        event_id = msg.get("event_id") or ""
        ts = int(msg.get("timestamp") or 0)

        if event_id and event_id in seen_set:
            continue

        sender = msg.get("sender", "<unknown>")
        room_id = msg.get("room_id", "<unknown>")
        body = msg.get("body", "")

        # Emit a JSONL event with channel routing metadata.
        # channel_type="matrix" tells the framework to use the registered
        # matrix handler for replies. sender and event_id allow message
        # history attribution.
        event = {
            "prompt": f"Matrix message from {sender} in {room_id}\n{body}",
            "channel_id": room_id,
            "channel_type": "matrix",
            "sender": sender,
            "event_id": event_id,
        }
        print(json.dumps(event))

        if event_id:
            seen_set.add(event_id)
        if ts > new_since:
            new_since = ts

    pruned_seen = list(seen_set)[-SEEN_MAX:]
    save_cursor({"since": new_since, "seen_event_ids": pruned_seen})
    return 0


if __name__ == "__main__":
    sys.exit(main())
