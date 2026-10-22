"""Unit tests for plugins/modules/list_clusters.py.

Mirrors test_create_cluster.py: list_clusters.py previously called
response.json() directly with no handling for a non-200/non-JSON response
(transient 5xx/429 from sso.redhat.com or api.openshift.com), which raised
an unhandled JSONDecodeError instead of a clean module failure. It now
routes through access_token.get_access_token_data()/safe_json(), matching
create_cluster.py/wait_for_hosts.py/install_cluster.py.

Requires ansible-core to be installed (for ansible.module_utils.basic),
since list_clusters.py imports AnsibleModule at module load time. See
tests/unit/conftest.py for how the module is loaded without the collection
being installed under ~/.ansible/collections.
"""
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

pytest.importorskip("ansible", reason="ansible-core is required to import list_clusters.py")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from conftest import load_list_clusters  # noqa: E402

list_clusters = load_list_clusters()


class FakeModule:
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
    get_unregistered_clusters=None,
    openshift_cluster_id=None,
    ams_subscription_ids=None,
    with_hosts=None,
    owner=None,
    offline_token="offline-token",
)


def _run(monkeypatch, token_response, get_response=None):
    module = FakeModule(dict(DEFAULT_PARAMS))
    monkeypatch.setattr(list_clusters, "AnsibleModule", lambda *a, **kw: module)
    monkeypatch.setattr(
        list_clusters.access_token, "_get_access_token", lambda offline_token: token_response
    )

    session = MagicMock()
    if get_response is not None:
        session.get.return_value = get_response
    monkeypatch.setattr(list_clusters.access_token, "_get_session", lambda: session)

    return module, session


class TestListClusters:
    def test_fails_cleanly_when_token_response_is_not_json(self, monkeypatch):
        # SSO returned a non-200 status with a body that isn't JSON at all
        # (e.g. an HTML error page during an outage).
        token_response = _mock_response(
            502, json_error=ValueError("Expecting value: line 1 column 1 (char 0)"), text=""
        )
        module, _session = _run(monkeypatch, token_response)

        with pytest.raises(SystemExit):
            list_clusters.run_module()

        assert module.fail_json_calls[0]["http_status"] == 502

    def test_fails_cleanly_when_list_clusters_response_is_not_json(self, monkeypatch):
        # The token call succeeds, but the subsequent GET /clusters call to
        # api.openshift.com returns a non-JSON body.
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        get_response = _mock_response(
            503, json_error=ValueError("boom"), text="<html>Service Unavailable</html>"
        )
        module, session = _run(monkeypatch, token_response, get_response)

        with pytest.raises(SystemExit):
            list_clusters.run_module()

        assert session.get.called
        assert module.fail_json_calls, "a non-JSON list-clusters response must fail, not be returned as the result"
        assert module.fail_json_calls[0]["http_status"] == 503

    def test_succeeds_on_valid_response(self, monkeypatch):
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        get_response = _mock_response(200, json_value=[{"id": "cluster-1"}, {"id": "cluster-2"}])
        module, session = _run(monkeypatch, token_response, get_response)

        with pytest.raises(SystemExit):
            list_clusters.run_module()

        assert session.get.called
        assert module.exit_json_calls
        result = module.exit_json_calls[0]
        assert result["result"] == [{"id": "cluster-1"}, {"id": "cluster-2"}]

    def test_check_mode_exits_before_calling_the_api(self, monkeypatch):
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        module, session = _run(monkeypatch, token_response)
        module.check_mode = True

        with pytest.raises(SystemExit):
            list_clusters.run_module()

        assert not session.get.called
        assert module.exit_json_calls
