"""Unit tests for the AccessTokenCache used by plugins/modules/install_cluster.py.

Requires ansible-core to be installed (for ansible.module_utils.basic),
since install_cluster.py imports AnsibleModule at module load time. See
tests/unit/conftest.py for how the module is loaded without the collection
being installed under ~/.ansible/collections.
"""
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

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
