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


def _run(monkeypatch, params, session_get_responses, token="tok-1", mock_sleep=True):
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

    sleep_mock = None
    if mock_sleep:
        sleep_mock = MagicMock()
        monkeypatch.setattr(wait_for_hosts.time, "sleep", sleep_mock)

    return module, session, sleep_mock


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
        module, _session, _sleep = _run(monkeypatch, {}, [response])

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
        module, _session, _sleep = _run(monkeypatch, {}, [response])

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
        module, _session, _sleep = _run(monkeypatch, {"expected_hosts": 1}, [response])

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
        module, session, _sleep = _run(monkeypatch, {}, [cluster_response])
        session.post.return_value = _mock_response(
            503, json_error=ValueError("boom"), text="<html>Service Unavailable</html>"
        )

        with pytest.raises(SystemExit):
            wait_for_hosts.run_module()

        assert module.fail_json_calls
        assert module.fail_json_calls[0]["http_status"] == 503

    def test_sleeps_once_per_poll_not_once_per_host(self, monkeypatch):
        # Reproduces the nested-in-the-host-loop bug: with multiple hosts in
        # a single poll response, none of them ready yet, time.sleep(delay)
        # must be called exactly once for that poll - not once per host.
        response = _mock_response(
            200,
            json_value={
                "status": "adding-hosts",
                "hosts": [
                    {"id": "h1", "status": "discovering", "requested_hostname": "h1"},
                    {"id": "h2", "status": "discovering", "requested_hostname": "h2"},
                    {"id": "h3", "status": "discovering", "requested_hostname": "h3"},
                ],
            },
        )
        ready_response = _mock_response(
            200,
            json_value={
                "status": "ready",
                "hosts": [{"id": "h1", "status": "known", "requested_hostname": "h1"}],
            },
        )
        # wait_timeout=20, delay=10 -> max_retries == 2 polls, enough for the
        # not-ready poll plus the ready poll below.
        module, _session, sleep_mock = _run(
            monkeypatch,
            {"expected_hosts": 1, "wait_timeout": 20, "delay": 10},
            [response, ready_response],
        )

        with pytest.raises(SystemExit):
            wait_for_hosts.run_module()

        assert module.exit_json_calls, module.fail_json_calls
        # One poll with 3 not-yet-ready hosts must sleep exactly once, not
        # three times (once per host).
        assert sleep_mock.call_count == 1

    def test_fails_with_timeout_message_when_hosts_never_become_ready(self, monkeypatch):
        # Reproduces the other half of the bug: `retries` was never
        # incremented, so wait_timeout was never enforced and the module
        # would poll forever instead of failing once the deadline passed.
        not_ready_response = _mock_response(
            200,
            json_value={
                "status": "adding-hosts",
                "hosts": [{"id": "h1", "status": "discovering", "requested_hostname": "h1"}],
            },
        )
        # wait_timeout=20, delay=10 -> max_retries == 2 polls.
        module, session, sleep_mock = _run(
            monkeypatch,
            {"wait_timeout": 20, "delay": 10},
            [not_ready_response, not_ready_response, not_ready_response],
        )

        with pytest.raises(SystemExit):
            wait_for_hosts.run_module()

        assert session.get.call_count == 2, "must stop polling once wait_timeout elapses"
        assert sleep_mock.call_count == 2
        assert not module.exit_json_calls, "a timeout must not be reported as success"
        assert module.fail_json_calls
        assert "Timed out" in module.fail_json_calls[0]["msg"]
