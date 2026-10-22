"""Unit tests for plugins/modules/wait_for_hosts.py.

These reproduce the real-world crash this module hit in production
(RHDP job 305694): the Assisted Installer API (or the SSO token endpoint)
returns a non-200/non-JSON response mid-poll, and `response.json()` raises
an unhandled JSONDecodeError instead of a clean, actionable module failure.

Requires ansible-core to be installed (for ansible.module_utils.basic),
since wait_for_hosts.py imports AnsibleModule at module load time. See
tests/unit/conftest.py for how the module is loaded without the collection
being installed under ~/.ansible/collections.
"""
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

pytest.importorskip("ansible", reason="ansible-core is required to import wait_for_hosts.py")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from conftest import load_wait_for_hosts  # noqa: E402

wait_for_hosts = load_wait_for_hosts()


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


DEFAULT_PARAMS = dict(
    cluster_id="cluster-1",
    infra_env_id="infra-env-1",
    offline_token="offline-token",
    expected_hosts=1,
    wait_timeout=10,
    delay=10,
    configure_hosts=None,
)


def _run(monkeypatch, params, session_get_responses, token="tok-1"):
    module = FakeModule(dict(DEFAULT_PARAMS, **params))
    monkeypatch.setattr(wait_for_hosts, "AnsibleModule", lambda *a, **kw: module)

    token_cache = MagicMock()
    token_cache.get.return_value = token
    monkeypatch.setattr(
        wait_for_hosts.access_token, "AccessTokenCache", lambda *a, **kw: token_cache
    )

    session = MagicMock()
    session.get.side_effect = session_get_responses
    monkeypatch.setattr(wait_for_hosts.access_token, "_get_session", lambda: session)

    return module, session


class TestWaitForHosts:
    def test_fails_cleanly_when_cluster_status_response_is_not_json(self, monkeypatch):
        # Reproduces RHDP job 305694: the GET to
        # /clusters/{id} returns a non-200/non-JSON body (empty body / HTML
        # error page from a transient outage) instead of raising an
        # unhandled JSONDecodeError.
        response = _mock_response(
            502,
            json_error=ValueError("Expecting value: line 1 column 1 (char 0)"),
            text="",
        )
        module, _session = _run(monkeypatch, {}, [response])

        with pytest.raises(SystemExit):
            wait_for_hosts.run_module()

        assert len(module.fail_json_calls) == 1
        kwargs = module.fail_json_calls[0]
        assert kwargs["http_status"] == 502
        # Must not have attempted to crash on `data['hosts']` - fail_json
        # must be the very first failure, not a KeyError.
        assert "hosts" not in kwargs

    def test_fails_cleanly_on_api_error_payload(self, monkeypatch):
        # The API itself can return valid JSON that encodes an error
        # (e.g. {"code": "ERR-123", "reason": "..."}) - this must still be
        # treated as a failure, same as before this fix.
        response = _mock_response(404, json_value={"code": "ERR-404", "reason": "not found"})
        module, _session = _run(monkeypatch, {}, [response])

        with pytest.raises(SystemExit):
            wait_for_hosts.run_module()

        assert module.fail_json_calls[0]["code"] == "ERR-404"

    def test_succeeds_when_all_hosts_are_ready(self, monkeypatch):
        response = _mock_response(
            200,
            json_value={
                "status": "ready",
                "hosts": [{"id": "h1", "status": "known", "requested_hostname": "h1"}],
            },
        )
        module, _session = _run(monkeypatch, {"expected_hosts": 1}, [response])

        with pytest.raises(SystemExit):
            wait_for_hosts.run_module()

        assert module.exit_json_calls, "expected exit_json to be called on success"
        assert module.exit_json_calls[0]["result"]["status"] == "ready"

    def test_fails_cleanly_when_install_action_response_is_not_json(self, monkeypatch):
        # Covers the second api.openshift.com call (install action), which
        # had the exact same unguarded response.json() pattern.
        cluster_response = _mock_response(
            200,
            json_value={
                "status": "adding-hosts",
                "hosts": [{"id": "h1", "status": "known", "requested_hostname": "h1"}],
            },
        )
        module, session = _run(monkeypatch, {}, [cluster_response])
        session.post.return_value = _mock_response(
            503, json_error=ValueError("boom"), text="<html>Service Unavailable</html>"
        )

        with pytest.raises(SystemExit):
            wait_for_hosts.run_module()

        assert module.fail_json_calls
        assert module.fail_json_calls[0]["http_status"] == 503
