"""Unit tests for JSONDecodeError origin detection in the event worker.

See tony-9k6: empty HTTP response bodies from the model provider and
malformed tool arguments from the agent both surface as ``JSONDecodeError``,
but require different handling. The detector used by the event worker's
except branch walks the traceback to disambiguate.
"""

from __future__ import annotations

import json

from open_strix.app import _is_http_body_parse_error


def _raise_json_decode_from_fake_filename(fake_filename: str) -> json.JSONDecodeError:
    """Raise a JSONDecodeError whose traceback contains ``fake_filename``.

    We compile a small snippet with a custom filename so the raised
    frame's ``co_filename`` is exactly what we want — this lets us
    simulate an exception raised from any module path (e.g. inside
    httpx) without monkey-patching httpx itself.
    """
    source = (
        "import json\n"
        "def _invoke():\n"
        "    return json.loads('')\n"
        "_invoke()\n"
    )
    code = compile(source, fake_filename, "exec")
    try:
        exec(code, {})
    except json.JSONDecodeError as exc:
        return exc
    raise AssertionError("expected JSONDecodeError to be raised")


class TestIsHttpBodyParseError:
    def test_detects_httpx_frame_in_traceback(self) -> None:
        """Any frame whose filename contains 'httpx' ⇒ True."""
        exc = _raise_json_decode_from_fake_filename(
            "/usr/local/lib/python3.11/site-packages/httpx/_models.py"
        )
        assert _is_http_body_parse_error(exc) is True

    def test_detects_httpx_frame_with_venv_path(self) -> None:
        """Realistic venv path (matches the traceback in tony-9k6)."""
        exc = _raise_json_decode_from_fake_filename(
            "/home/tony/tony/.venv/lib/python3.11/site-packages/httpx/_models.py"
        )
        assert _is_http_body_parse_error(exc) is True

    def test_rejects_agent_code_path(self) -> None:
        """Non-httpx origin (e.g. agent tool-arg parsing) ⇒ False."""
        exc = _raise_json_decode_from_fake_filename(
            "/home/tony/tony/open_strix/tools.py"
        )
        assert _is_http_body_parse_error(exc) is False

    def test_rejects_stdlib_json_path(self) -> None:
        """Raising from a module path that merely mentions json ⇒ False.

        Ensures the check is strictly for httpx, not any JSON-adjacent
        filename.
        """
        exc = _raise_json_decode_from_fake_filename(
            "/opt/app/custom_json_helpers.py"
        )
        assert _is_http_body_parse_error(exc) is False

    def test_handles_no_traceback(self) -> None:
        """JSONDecodeError without a ``__traceback__`` returns False.

        A detached exception (one never raised, or with its traceback
        cleared) can't be attributed to httpx. Default to the safer
        classification of 'not http body'.
        """
        exc = json.JSONDecodeError("no tb", "", 0)
        # Explicit: no traceback attached when constructed directly.
        assert exc.__traceback__ is None
        assert _is_http_body_parse_error(exc) is False

    def test_walks_full_traceback_chain(self) -> None:
        """httpx frame anywhere in the chain (not just innermost) ⇒ True.

        The raise site for empty-body is inside json/decoder.py; the
        httpx frame appears higher in the chain as the caller.
        """
        # Nested call: outer function lives in a 'httpx/_models.py'
        # filename, inner raises. Traceback will include both frames.
        outer = compile(
            "import json\n"
            "def outer():\n"
            "    json.loads('')\n"
            "outer()\n",
            "/fake/httpx/_models.py",
            "exec",
        )
        try:
            exec(outer, {})
        except json.JSONDecodeError as exc:
            assert _is_http_body_parse_error(exc) is True
        else:
            raise AssertionError("expected JSONDecodeError")
