"""Unit tests for plugins/module_utils/access_token.py.

This module has no ansible-specific imports, so it can be loaded directly
from its file path without needing the collection installed.
"""
import importlib.util
import pathlib
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


def _load_access_token():
    spec = importlib.util.spec_from_file_location(
        "access_token", str(REPO_ROOT / "plugins" / "module_utils" / "access_token.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


access_token = _load_access_token()


def _mock_response(status_code=200, json_value=None, json_error=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = json_value
    return response


class TestSafeJson:
    def test_returns_parsed_json_on_success(self):
        response = _mock_response(200, json_value={"access_token": "abc", "expires_in": 300})
        assert access_token.safe_json(response) == {"access_token": "abc", "expires_in": 300}

    def test_returns_status_and_body_on_non_json_response(self):
        # requests raises a ValueError subclass (JSONDecodeError) when the
        # body isn't valid JSON, e.g. an HTML error page from a gateway.
        response = _mock_response(
            502,
            json_error=ValueError("Expecting value: line 1 column 1 (char 0)"),
            text="<html><body>Bad Gateway</body></html>",
        )

        result = access_token.safe_json(response)

        assert result["http_status"] == 502
        assert "Bad Gateway" in result["raw_body"]

    def test_truncates_long_raw_body(self):
        response = _mock_response(
            500,
            json_error=ValueError("boom"),
            text="x" * 10000,
        )

        result = access_token.safe_json(response)

        assert len(result["raw_body"]) == 500


class TestGetAccessTokenData:
    def test_returns_response_and_safely_parsed_data(self, monkeypatch):
        response = _mock_response(200, json_value={"access_token": "abc"})
        monkeypatch.setattr(access_token, "_get_access_token", lambda offline_token: response)

        returned_response, data = access_token.get_access_token_data("offline-token")

        assert returned_response is response
        assert data == {"access_token": "abc"}

    def test_returns_safe_fallback_when_body_is_not_json(self, monkeypatch):
        response = _mock_response(503, json_error=ValueError("boom"), text="")
        monkeypatch.setattr(access_token, "_get_access_token", lambda offline_token: response)

        _, data = access_token.get_access_token_data("offline-token")

        assert data == {"http_status": 503, "raw_body": ""}


class TestRetryConfiguration:
    def test_retry_allows_post_and_covers_server_errors(self):
        # This is the exact gap that caused the original crash: urllib3's
        # Retry does not retry POST by default, so it must be added to
        # allowed_methods explicitly.
        assert "POST" in access_token._RETRY.allowed_methods
        for status in (429, 500, 502, 503, 504):
            assert status in access_token._RETRY.status_forcelist

    def test_retry_still_covers_get_and_patch_used_by_polling_modules(self):
        # access_token._get_session() is shared by every module in this
        # collection, including ones that poll with GET and update hosts
        # with PATCH (wait_for_hosts.py, install_cluster.py). Explicitly
        # setting allowed_methods={"POST"} (instead of extending the
        # default allowlist) would silently drop GET/PATCH from the
        # retryable set - this must not regress.
        for method in ("GET", "POST", "PATCH"):
            assert access_token._RETRY._is_method_retryable(method), (
                f"{method} must be retryable on the shared session"
            )

    def test_retry_does_not_raise_on_exhausted_status_retries(self):
        # With the default raise_on_status=True, urllib3 raises MaxRetryError
        # (which requests turns into RetryError) once retries on a
        # status_forcelist code are exhausted, so safe_json() never runs
        # during a sustained outage. raise_on_status=False must be set so
        # the final response is returned instead and safe_json()/the
        # http_status guard can report it as a clean module failure.
        assert access_token._RETRY.raise_on_status is False

    def test_retry_safe_also_does_not_raise_on_exhausted_status_retries(self):
        assert access_token._RETRY_SAFE.raise_on_status is False

    def test_retry_safe_excludes_post_and_patch(self):
        # _RETRY_SAFE is used for non-idempotent calls (e.g. POST /clusters)
        # where retrying after a transient 5xx risks double-applying the
        # operation server-side. POST/PATCH must not be status-retried.
        for method in ("POST", "PATCH"):
            assert not access_token._RETRY_SAFE._is_method_retryable(method), (
                f"{method} must not be status-retried on the non-idempotent-safe session"
            )

    def test_retry_safe_still_covers_get_and_other_idempotent_methods(self):
        for method in ("GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"):
            assert access_token._RETRY_SAFE._is_method_retryable(method), (
                f"{method} must still be retryable on the non-idempotent-safe session"
            )

    def test_get_session_defaults_to_the_standard_retry(self):
        session = access_token._get_session()
        adapter = session.get_adapter("https://example.com")
        assert adapter.max_retries is access_token._RETRY

    def test_get_session_with_retry_non_idempotent_false_uses_the_safe_retry(self):
        session = access_token._get_session(retry_non_idempotent=False)
        adapter = session.get_adapter("https://example.com")
        assert adapter.max_retries is access_token._RETRY_SAFE
