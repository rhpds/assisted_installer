"""Unit tests for plugins/modules/delete_cluster.py.

delete_cluster.py previously built its own plain requests.Session() +
HTTPAdapter(max_retries=5) (connection retries only - no 429/5xx retry)
and called bare response.json() at every call site, with no handling for
a non-200/non-JSON response (transient 5xx/429 from sso.redhat.com or
api.openshift.com), which raised an unhandled JSONDecodeError instead of
a clean module failure. It now routes through
access_token.get_access_token_data()/safe_json() and the shared
access_token._get_session(), matching
list_clusters.py/create_cluster.py/wait_for_hosts.py/install_cluster.py.

Requires ansible-core to be installed (for ansible.module_utils.basic),
since delete_cluster.py imports AnsibleModule at module load time. See
tests/unit/conftest.py for how the module is loaded without the collection
being installed under ~/.ansible/collections.
"""
import pathlib
import sys
from unittest.mock import MagicMock

import pytest
import requests

pytest.importorskip("ansible", reason="ansible-core is required to import delete_cluster.py")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from conftest import load_delete_cluster  # noqa: E402

delete_cluster = load_delete_cluster()


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
        self.warnings = []

    def fail_json(self, **kwargs):
        self.fail_json_calls.append(kwargs)
        raise SystemExit("fail_json called")

    def exit_json(self, **kwargs):
        self.exit_json_calls.append(kwargs)
        raise SystemExit("exit_json called")

    def warn(self, msg):
        self.warnings.append(msg)


def _mock_response(status_code=200, json_value=None, json_error=None, text="", content=b""):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    # content defaults to b"" (empty body, e.g. a 204 No Content success)
    # unless explicitly given, or derived from text/json_value so callers
    # don't have to specify both.
    if content:
        response.content = content
    elif text:
        response.content = text.encode()
    elif json_value is not None:
        response.content = b"non-empty"
    else:
        response.content = b""
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = json_value
    return response


DEFAULT_PARAMS = dict(
    cluster_id="cluster-1",
    offline_token="offline-token",
    cancel=False,
)


def _run(monkeypatch, params=None, session_overrides=None):
    module = FakeModule(dict(DEFAULT_PARAMS, **(params or {})))
    monkeypatch.setattr(delete_cluster, "AnsibleModule", lambda *a, **kw: module)

    session = MagicMock()
    for attr, value in (session_overrides or {}).items():
        setattr(session, attr, value)
    monkeypatch.setattr(delete_cluster.access_token, "_get_session", lambda: session)

    return module, session


class TestDeleteCluster:
    def test_fails_cleanly_when_token_response_is_not_json(self, monkeypatch):
        # SSO returned a non-200 status with a body that isn't JSON at all
        # (e.g. an HTML error page during an outage).
        token_response = _mock_response(
            502, json_error=ValueError("Expecting value: line 1 column 1 (char 0)"), text=""
        )
        module, session = _run(monkeypatch)
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert module.fail_json_calls[0]["http_status"] == 502
        assert not session.delete.called, "must fail before attempting any delete"

    def test_check_mode_exits_before_calling_the_api(self, monkeypatch):
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        module, session = _run(monkeypatch)
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        module.check_mode = True

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert not session.delete.called
        assert not session.post.called
        assert not session.get.called
        assert module.exit_json_calls

    def test_cancel_non_json_response_is_non_fatal_and_delete_still_proceeds(self, monkeypatch):
        # A non-JSON body (HTML error page / truncated response) from the
        # cancel POST during a transient outage must be treated as non-fatal
        # - the DELETE should still be attempted.
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        cancel_response = _mock_response(
            503, json_error=ValueError("boom"), text="<html>Service Unavailable</html>"
        )
        delete_response = _mock_response(204, content=b"")
        infra_envs_response = _mock_response(200, json_value=[])
        module, session = _run(monkeypatch, params={"cancel": True})
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        session.post.return_value = cancel_response
        session.delete.return_value = delete_response
        session.get.return_value = infra_envs_response

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert session.post.called
        assert session.delete.called, "DELETE must proceed even when cancel fails"
        assert module.exit_json_calls, module.fail_json_calls
        assert module.exit_json_calls[0]["changed"] is True
        assert len(module.warnings) == 1
        assert "non-fatal" in module.warnings[0]

    def test_cancel_with_empty_success_body_is_not_treated_as_a_failure(self, monkeypatch):
        # The cancel action can return an empty body (e.g. 204 No Content)
        # on success. safe_json() can't distinguish "empty because it
        # succeeded" from "empty because of an outage" - both look like
        # {http_status, raw_body: ""} - so the module must check content
        # length before deciding to parse at all, same as before this fix.
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        cancel_response = _mock_response(204, content=b"")
        delete_response = _mock_response(204, content=b"")
        infra_envs_response = _mock_response(200, json_value=[])
        module, session = _run(monkeypatch, params={"cancel": True})
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        session.post.return_value = cancel_response
        session.delete.return_value = delete_response
        session.get.return_value = infra_envs_response

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert module.exit_json_calls, module.fail_json_calls
        assert module.exit_json_calls[0]["changed"] is True

    def test_cancel_failure_is_non_fatal_and_delete_still_proceeds(self, monkeypatch):
        # The AI API rejects POST /actions/cancel with 409 for clusters
        # that aren't installing (e.g. pending-for-input, insufficient).
        # This must be treated as non-fatal - the DELETE should still be
        # attempted so the cluster is actually removed.
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        cancel_response = _mock_response(
            409, json_value={"code": "409", "reason": "Cluster is not installing"}
        )
        delete_response = _mock_response(204, content=b"")
        infra_envs_response = _mock_response(200, json_value=[])
        module, session = _run(monkeypatch, params={"cancel": True})
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        session.post.return_value = cancel_response
        session.delete.return_value = delete_response
        session.get.return_value = infra_envs_response

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert session.post.called, "cancel must be attempted"
        assert session.delete.called, "DELETE must proceed even when cancel fails"
        assert module.exit_json_calls, module.fail_json_calls
        assert module.exit_json_calls[0]["changed"] is True
        assert len(module.warnings) == 1
        assert "non-fatal" in module.warnings[0]

    def test_fails_cleanly_when_delete_response_is_not_json(self, monkeypatch):
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        delete_response = _mock_response(
            504, json_error=ValueError("boom"), text="<html>Gateway Timeout</html>"
        )
        module, session = _run(monkeypatch)
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        session.delete.return_value = delete_response

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert session.delete.called
        assert module.fail_json_calls
        assert module.fail_json_calls[0]["result"]["http_status"] == 504
        assert not session.get.called, "must fail before listing infra-envs"

    def test_fails_cleanly_when_infra_envs_list_response_is_not_json(self, monkeypatch):
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        delete_response = _mock_response(204, content=b"")
        infra_envs_response = _mock_response(
            503, json_error=ValueError("boom"), text="<html>Service Unavailable</html>"
        )
        module, session = _run(monkeypatch)
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        session.delete.return_value = delete_response
        session.get.return_value = infra_envs_response

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert module.fail_json_calls
        assert module.fail_json_calls[0]["result"]["http_status"] == 503

    def test_fails_cleanly_when_infra_env_delete_response_is_not_json(self, monkeypatch):
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        delete_response = _mock_response(204, content=b"")
        infra_envs_response = _mock_response(200, json_value=[{"id": "infra-env-1"}])
        infra_env_delete_response = _mock_response(
            502, json_error=ValueError("boom"), text="<html>Bad Gateway</html>"
        )
        module, session = _run(monkeypatch)
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        session.delete.side_effect = [delete_response, infra_env_delete_response]
        session.get.return_value = infra_envs_response

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert module.fail_json_calls
        assert module.fail_json_calls[0]["result"]["http_status"] == 502

    def test_fails_cleanly_on_api_error_payload(self, monkeypatch):
        # The API itself can return valid JSON that encodes an error
        # (e.g. {"code": "ERR-123", "reason": "..."}) - this must still be
        # treated as a failure, same as before this fix.
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        delete_response = _mock_response(404, json_value={"code": "ERR-404", "reason": "not found"})
        module, session = _run(monkeypatch)
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        session.delete.return_value = delete_response

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert module.fail_json_calls[0]["result"]["code"] == "ERR-404"

    def test_succeeds_on_full_happy_path_with_cancel_and_infra_envs(self, monkeypatch):
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        cancel_response = _mock_response(204, content=b"")
        cluster_delete_response = _mock_response(204, content=b"")
        infra_envs_response = _mock_response(
            200, json_value=[{"id": "infra-env-1"}, {"id": "infra-env-2"}]
        )
        infra_env_delete_response = _mock_response(204, content=b"")
        module, session = _run(monkeypatch, params={"cancel": True})
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        session.post.return_value = cancel_response
        session.delete.side_effect = [
            cluster_delete_response,
            infra_env_delete_response,
            infra_env_delete_response,
        ]
        session.get.return_value = infra_envs_response

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert session.post.called
        assert session.delete.call_count == 3
        assert module.exit_json_calls, module.fail_json_calls
        assert module.exit_json_calls[0]["changed"] is True

    def test_succeeds_with_no_infra_envs_and_no_cancel(self, monkeypatch):
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        cluster_delete_response = _mock_response(204, content=b"")
        infra_envs_response = _mock_response(200, json_value=[])
        module, session = _run(monkeypatch)
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        session.delete.return_value = cluster_delete_response
        session.get.return_value = infra_envs_response

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert not session.post.called, "cancel must not be called when cancel=False"
        assert session.delete.call_count == 1
        assert module.exit_json_calls, module.fail_json_calls
        assert module.exit_json_calls[0]["changed"] is True

    def test_fails_cleanly_on_connection_error(self, monkeypatch):
        # A connection-level failure (DNS, reset, timeout) still raises
        # after the shared session's retries are exhausted, regardless of
        # raise_on_status - this must produce a clean fail_json, not an
        # unhandled traceback.
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        module, session = _run(monkeypatch)
        monkeypatch.setattr(
            delete_cluster.access_token, "_get_access_token", lambda offline_token: token_response
        )
        session.delete.side_effect = requests.exceptions.ConnectionError("boom")

        with pytest.raises(SystemExit):
            delete_cluster.run_module()

        assert module.fail_json_calls
        assert "boom" in module.fail_json_calls[0]["msg"]
