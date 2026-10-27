"""Unit tests for plugins/modules/install_cluster.py.

Covers both the AccessTokenCache it uses (shared from module_utils) and
run_module() itself, including the non-JSON-response handling for its own
API calls (initial install POST, polling GET, retry-install POST).

Requires ansible-core to be installed (for ansible.module_utils.basic),
since install_cluster.py imports AnsibleModule at module load time. See
tests/unit/conftest.py for how the module is loaded without the collection
being installed under ~/.ansible/collections.
"""
import pathlib
import sys
from unittest.mock import MagicMock

import pytest
import requests

pytest.importorskip("ansible", reason="ansible-core is required to import install_cluster.py")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from conftest import load_install_cluster  # noqa: E402

install_cluster = load_install_cluster()


def _fake_module():
    module = MagicMock()
    module.fail_json.side_effect = SystemExit("fail_json called")
    return module


class TestAccessTokenCache:
    def test_fetches_token_on_first_call(self, monkeypatch):
        module = _fake_module()
        get_data = MagicMock(return_value=(MagicMock(status_code=200), {"access_token": "tok-1", "expires_in": 300}))
        monkeypatch.setattr(install_cluster.access_token, "get_access_token_data", get_data)

        cache = install_cluster.AccessTokenCache(module, "offline-token")
        token = cache.get()

        assert token == "tok-1"
        get_data.assert_called_once_with("offline-token")

    def test_reuses_cached_token_within_validity_window(self, monkeypatch):
        module = _fake_module()
        get_data = MagicMock(return_value=(MagicMock(status_code=200), {"access_token": "tok-1", "expires_in": 300}))
        monkeypatch.setattr(install_cluster.access_token, "get_access_token_data", get_data)

        cache = install_cluster.AccessTokenCache(module, "offline-token")
        first = cache.get()
        second = cache.get()
        third = cache.get()

        # Simulates several 60s poll iterations happening well within the
        # token's ~300s validity window: the SSO endpoint should only be
        # hit once, not once per poll.
        assert [first, second, third] == ["tok-1", "tok-1", "tok-1"]
        get_data.assert_called_once()

    def test_refreshes_once_cached_token_is_near_expiry(self, monkeypatch):
        module = _fake_module()
        responses = [
            (MagicMock(status_code=200), {"access_token": "tok-1", "expires_in": 300}),
            (MagicMock(status_code=200), {"access_token": "tok-2", "expires_in": 300}),
        ]
        get_data = MagicMock(side_effect=responses)
        monkeypatch.setattr(install_cluster.access_token, "get_access_token_data", get_data)

        fake_now = [1000.0]
        monkeypatch.setattr(install_cluster.time, "monotonic", lambda: fake_now[0])

        cache = install_cluster.AccessTokenCache(module, "offline-token")
        assert cache.get() == "tok-1"

        # Advance time past expires_in (300s) minus the refresh skew (60s).
        fake_now[0] += 300
        assert cache.get() == "tok-2"
        assert get_data.call_count == 2

    def test_fails_cleanly_with_actionable_message_on_non_json_error(self, monkeypatch):
        # This reproduces the original crash scenario: SSO returns a
        # non-200 status with a body that safe_json could not parse as
        # JSON. Instead of an unhandled JSONDecodeError, fail_json should
        # be called with the http_status/raw_body describing the failure.
        module = _fake_module()
        get_data = MagicMock(return_value=(MagicMock(status_code=502), {"http_status": 502, "raw_body": "<html>Bad Gateway</html>"}))
        monkeypatch.setattr(install_cluster.access_token, "get_access_token_data", get_data)

        cache = install_cluster.AccessTokenCache(module, "offline-token")

        with pytest.raises(SystemExit):
            cache.get()

        assert module.fail_json.call_count == 1
        _, kwargs = module.fail_json.call_args
        assert kwargs["http_status"] == 502
        assert "Bad Gateway" in kwargs["raw_body"]


class FakeModule:
    """Stand-in for AnsibleModule that records fail_json/exit_json calls.

    fail_json/exit_json raise SystemExit, mirroring AnsibleModule's real
    behavior (it calls sys.exit() internally), so run_module() unwinds the
    same way it would under the real Ansible runtime.
    """

    def __init__(self, params):
        self.params = params
        self.check_mode = False
        self.fail_json_calls = []
        self.exit_json_calls = []

    def fail_json(self, **kwargs):
        self.fail_json_calls.append(kwargs)
        raise SystemExit("fail_json called")

    def exit_json(self, **kwargs):
        self.exit_json_calls.append(kwargs)
        raise SystemExit("exit_json called")


def _mock_response(status_code=200, json_value=None, json_error=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = json_value
    return response


DEFAULT_INSTALL_PARAMS = dict(
    cluster_id="cluster-1",
    offline_token="offline-token",
    wait_timeout=10,
    delay=10,
)


def _run_install_cluster(monkeypatch, params=None, session_post_responses=None, session_get_responses=None):
    module = FakeModule(dict(DEFAULT_INSTALL_PARAMS, **(params or {})))
    monkeypatch.setattr(install_cluster, "AnsibleModule", lambda *a, **kw: module)

    token_cache = MagicMock()
    token_cache.get.return_value = "tok-1"
    monkeypatch.setattr(install_cluster, "AccessTokenCache", lambda *a, **kw: token_cache)

    session = MagicMock()
    if session_post_responses is not None:
        session.post.side_effect = session_post_responses
    if session_get_responses is not None:
        session.get.side_effect = session_get_responses
    monkeypatch.setattr(install_cluster.access_token, "_get_session", lambda: session)

    return module, session


class TestInstallClusterRunModule:
    """Covers the non-JSON-response gap in install_cluster.run_module() itself
    (as opposed to AccessTokenCache, covered above): the initial install POST,
    the polling GET, and the retry-install POST all used to only check
    `"code" in data`, so a non-JSON response (safe_json's {http_status,
    raw_body} fallback) would silently fall through instead of failing.
    """

    def test_fails_cleanly_when_initial_install_response_is_not_json(self, monkeypatch):
        post_response = _mock_response(
            502, json_error=ValueError("boom"), text="<html>Bad Gateway</html>"
        )
        module, session = _run_install_cluster(monkeypatch, session_post_responses=[post_response])

        with pytest.raises(SystemExit):
            install_cluster.run_module()

        assert not session.get.called, "must fail before ever polling"
        assert module.fail_json_calls
        assert module.fail_json_calls[0]["http_status"] == 502

    def test_fails_cleanly_when_polling_response_is_not_json(self, monkeypatch):
        post_response = _mock_response(200, json_value={"id": "cluster-1"})
        get_response = _mock_response(
            503, json_error=ValueError("boom"), text="<html>Service Unavailable</html>"
        )
        module, session = _run_install_cluster(
            monkeypatch,
            session_post_responses=[post_response],
            session_get_responses=[get_response],
        )

        with pytest.raises(SystemExit):
            install_cluster.run_module()

        assert session.get.called
        assert module.fail_json_calls, "a non-JSON polling response must fail, not loop forever"
        assert module.fail_json_calls[0]["http_status"] == 503

    def test_fails_cleanly_when_retry_install_response_is_not_json(self, monkeypatch):
        initial_post_response = _mock_response(200, json_value={"id": "cluster-1"})
        get_response = _mock_response(200, json_value={"status": "ready"})
        retry_post_response = _mock_response(
            504, json_error=ValueError("boom"), text="<html>Gateway Timeout</html>"
        )
        module, session = _run_install_cluster(
            monkeypatch,
            session_post_responses=[initial_post_response, retry_post_response],
            session_get_responses=[get_response],
        )

        with pytest.raises(SystemExit):
            install_cluster.run_module()

        assert session.post.call_count == 2
        assert module.fail_json_calls
        assert module.fail_json_calls[0]["http_status"] == 504

    def test_fails_cleanly_on_connection_error(self, monkeypatch):
        # A connection-level failure (DNS, reset, timeout) still raises
        # after the shared session's retries are exhausted, regardless of
        # raise_on_status - this must produce a clean fail_json, not an
        # unhandled traceback.
        module, session = _run_install_cluster(monkeypatch)
        session.post.side_effect = requests.exceptions.ConnectionError("boom")

        with pytest.raises(SystemExit):
            install_cluster.run_module()

        assert module.fail_json_calls
        assert "boom" in module.fail_json_calls[0]["msg"]
