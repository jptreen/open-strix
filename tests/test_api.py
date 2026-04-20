"""Tests for the loopback REST API."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from open_strix.api import _build_app
from open_strix.models import AgentEvent
from open_strix.shell_jobs import ShellJobRegistry


def _make_mock_app() -> MagicMock:
    """Create a minimal mock OpenStrixApp with enqueue_event."""
    app = MagicMock()
    app.enqueue_event = AsyncMock()
    app.log_event = MagicMock()
    app.shell_jobs = None
    return app


async def _wait_for_job_exit(job, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while job.exit_code is None:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"job {job.job_id} did not exit within {timeout}s")
        await asyncio.sleep(0.01)


@pytest.fixture
def mock_strix():
    return _make_mock_app()


@pytest.fixture
def aiohttp_app(mock_strix):
    return _build_app(mock_strix)


@pytest.mark.asyncio
async def test_health(aiohttp_client, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.get("/api/health")
    assert resp.status == 200
    body = await resp.json()
    assert body == {"status": "ok"}


@pytest.mark.asyncio
async def test_post_event_queues(aiohttp_client, mock_strix, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.post(
        "/api/event",
        json={"source": "test-harness", "prompt": "hello world"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "queued"
    assert body["source"] == "test-harness"

    mock_strix.enqueue_event.assert_called_once()
    event = mock_strix.enqueue_event.call_args[0][0]
    assert isinstance(event, AgentEvent)
    assert event.event_type == "api_event"
    assert event.prompt == "hello world"
    assert event.source_id == "api:test-harness"


@pytest.mark.asyncio
async def test_post_event_missing_prompt(aiohttp_client, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.post("/api/event", json={"source": "test"})
    assert resp.status == 400
    body = await resp.json()
    assert "prompt is required" in body["error"]


@pytest.mark.asyncio
async def test_post_event_invalid_json(aiohttp_client, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.post(
        "/api/event",
        data=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_post_event_default_source(aiohttp_client, mock_strix, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.post("/api/event", json={"prompt": "no source"})
    assert resp.status == 200
    body = await resp.json()
    assert body["source"] == "api"

    event = mock_strix.enqueue_event.call_args[0][0]
    assert event.source_id == "api:api"


@pytest.mark.asyncio
async def test_post_event_with_channel_id(aiohttp_client, mock_strix, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.post(
        "/api/event",
        json={"prompt": "targeted", "channel_id": "123456"},
    )
    assert resp.status == 200

    event = mock_strix.enqueue_event.call_args[0][0]
    assert event.channel_id == "123456"


@pytest.mark.asyncio
async def test_shell_jobs_list_defaults_to_running_scope(aiohttp_client, mock_strix, aiohttp_app, tmp_path):
    mock_strix.shell_jobs = ShellJobRegistry(tmp_path / "jobs")
    running = mock_strix.shell_jobs.spawn("sleep 0.8", argv=["bash", "-lc", "sleep 0.8"])
    finished = mock_strix.shell_jobs.spawn("echo done", argv=["bash", "-lc", "echo done"])
    await _wait_for_job_exit(finished)

    client = await aiohttp_client(aiohttp_app)
    resp = await client.get("/api/shell-jobs")

    assert resp.status == 200
    body = await resp.json()
    assert body["scope"] == "running"
    assert [job["job_id"] for job in body["jobs"]] == [running.job_id]
    assert body["jobs"][0]["status"] == "running"


@pytest.mark.asyncio
async def test_shell_jobs_list_supports_all_scope(aiohttp_client, mock_strix, aiohttp_app, tmp_path):
    mock_strix.shell_jobs = ShellJobRegistry(tmp_path / "jobs")
    running = mock_strix.shell_jobs.spawn("sleep 0.8", argv=["bash", "-lc", "sleep 0.8"])
    finished = mock_strix.shell_jobs.spawn("echo done", argv=["bash", "-lc", "echo done"])
    await _wait_for_job_exit(finished)

    client = await aiohttp_client(aiohttp_app)
    resp = await client.get("/api/shell-jobs?scope=all")

    assert resp.status == 200
    body = await resp.json()
    assert body["scope"] == "all"
    returned_ids = [job["job_id"] for job in body["jobs"]]
    assert running.job_id in returned_ids
    assert finished.job_id in returned_ids


@pytest.mark.asyncio
async def test_shell_job_detail_returns_output(aiohttp_client, mock_strix, aiohttp_app, tmp_path):
    mock_strix.shell_jobs = ShellJobRegistry(tmp_path / "jobs")
    cmd = "printf 'hello\\n'; printf 'problem\\n' >&2"
    job = mock_strix.shell_jobs.spawn(cmd, argv=["bash", "-lc", cmd])
    await _wait_for_job_exit(job)

    client = await aiohttp_client(aiohttp_app)
    resp = await client.get(f"/api/shell-jobs/{job.job_id}")

    assert resp.status == 200
    body = await resp.json()
    assert body["job_id"] == job.job_id
    assert "hello" in body["stdout_tail"]
    assert "problem" in body["stderr_tail"]


@pytest.mark.asyncio
async def test_shell_job_detail_unknown_job_returns_404(aiohttp_client, mock_strix, aiohttp_app, tmp_path):
    mock_strix.shell_jobs = ShellJobRegistry(tmp_path / "jobs")
    client = await aiohttp_client(aiohttp_app)
    resp = await client.get("/api/shell-jobs/j_missing")

    assert resp.status == 404
    body = await resp.json()
    assert "unknown job_id" in body["error"]
