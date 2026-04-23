"""Loopback REST API for injecting events into the agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from .models import AgentEvent
from .shell_jobs import (
    parse_shell_job_tail_lines,
    normalize_shell_job_scope,
    normalize_shell_job_stream,
    shell_job_snapshots,
)

if TYPE_CHECKING:
    from .app import OpenStrixApp


def _build_app(strix: OpenStrixApp) -> web.Application:
    app = web.Application()

    async def post_event(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        source = body.get("source", "")
        prompt = body.get("prompt", "")
        if not prompt:
            return web.json_response({"error": "prompt is required"}, status=400)

        source_label = source or "api"
        event = AgentEvent(
            event_type="api_event",
            prompt=prompt,
            channel_id=body.get("channel_id"),
            source_id=f"api:{source_label}",
        )
        await strix.enqueue_event(event)
        return web.json_response({"status": "queued", "source": source_label})

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def list_shell_jobs(request: web.Request) -> web.Response:
        try:
            scope = normalize_shell_job_scope(request.query.get("scope"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        registry = getattr(strix, "shell_jobs", None)
        return web.json_response(
            {
                "scope": scope,
                "jobs": shell_job_snapshots(registry, scope=scope),
            },
        )

    async def shell_job_detail(request: web.Request) -> web.Response:
        registry = getattr(strix, "shell_jobs", None)
        if registry is None:
            return web.json_response({"error": "shell jobs unavailable"}, status=404)

        try:
            tail_lines = parse_shell_job_tail_lines(request.query.get("tail"))
            stream = normalize_shell_job_stream(request.query.get("stream"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        data = registry.read_output(
            request.match_info["job_id"],
            tail_lines=tail_lines,
            stream=stream,
        )
        if "error" in data:
            return web.json_response(data, status=404)
        return web.json_response(data)

    app.router.add_post("/api/event", post_event)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/shell-jobs", list_shell_jobs)
    app.router.add_get("/api/shell-jobs/{job_id}", shell_job_detail)
    return app


async def start_api(strix: OpenStrixApp, port: int) -> web.AppRunner:
    """Start the loopback API server. Returns the runner for cleanup."""
    app = _build_app(strix)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    strix.log_event("api_started", port=port)
    return runner
