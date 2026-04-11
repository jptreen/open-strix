# Matrix Channel Handler Example

A working example of a config-driven channel handler that routes agent
replies to Matrix via an HTTP bridge. Demonstrates the full loop:

```
Matrix user → poller (channel_type="matrix") → agent → send_message()
→ channel handler registry → HTTP POST to bridge → encrypted reply
```

## How it works

### 1. Poller emits channel metadata

The poller queries the Matrix bridge for new messages and emits JSONL
with `channel_id` (room ID) and `channel_type` set to `"matrix"`:

```json
{
  "prompt": "Message from @alice:matrix.org in !room:matrix.org\nHello!",
  "channel_id": "!abc123:matrix.org",
  "channel_type": "matrix",
  "sender": "@alice:matrix.org",
  "event_id": "$evt456"
}
```

The scheduler extracts `channel_id` and `channel_type` onto the
`AgentEvent`, which sets `current_channel_type` for the duration of the
turn.

### 2. Config registers the handler

In `config.yaml`:

```yaml
channel_handlers:
  matrix:
    send_url: "http://127.0.0.1:29317/send"
    body_map: '{"room_id": "{channel_id}", "body": "{text}"}'
```

`channel_handlers` maps a `channel_type` string to an HTTP endpoint.
The `body_map` template uses `{channel_id}` and `{text}` placeholders,
which are JSON-escaped at substitution time to prevent injection.

### 3. Agent replies normally

The agent calls `send_message(text="Hello back!")` without specifying
a channel. The framework resolves the handler from `current_channel_type`
and POSTs to the configured `send_url`.

No changes to the agent's tool usage — routing is transparent.

## Files

- `poller.py` — Minimal poller that queries an HTTP bridge and emits
  channel-typed JSONL events
- `pollers.json` — Cron registration for the scheduler
- `config-snippet.yaml` — The `channel_handlers` block to add to your
  `config.yaml`

## Adapting for other platforms

The pattern works for any platform with an HTTP send API:

1. Write a poller that emits `channel_id` + `channel_type`
2. Add a `channel_handlers` entry pointing to the platform's send endpoint
3. The `body_map` template maps open-strix's `{channel_id}` and `{text}`
   to whatever the API expects

For example, a Slack handler might look like:

```yaml
channel_handlers:
  slack:
    send_url: "https://slack.com/api/chat.postMessage"
    body_map: '{"channel": "{channel_id}", "text": "{text}"}'
```
