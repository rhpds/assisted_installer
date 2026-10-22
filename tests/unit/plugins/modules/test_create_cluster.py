"""Unit tests for plugins/modules/create_cluster.py.

Reproduces the real-world crash this module hit in production (RHDP job
285609): a non-200/non-JSON response (from SSO or from the Assisted
Installer API) made `response.json()` raise an unhandled JSONDecodeError
instead of a clean, actionable module failure.

Requires ansible-core to be installed (for ansible.module_utils.basic),
since create_cluster.py imports AnsibleModule at module load time. See
tests/unit/conftest.py for how the module is loaded without the collection
being installed under ~/.ansible/collections.
"""
import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

pytest.importorskip("ansible", reason="ansible-core is required to import create_cluster.py")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from conftest import load_create_cluster  # noqa: E402

create_cluster = load_create_cluster()


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
    name="cluster-abc123",
    offline_token="offline-token",
    openshift_version="4.20",
    pull_secret=json.dumps({"auths": {}}),
    base_dns_domain="example.com",
    service_networks=None,
    olm_operators=None,
    high_availability_mode=None,
    additional_ntp_source=None,
    api_vips=None,
    cluster_network_cidr=None,
    cluster_network_host_prefix=None,
    cluster_networks=None,
    cpu_architecture=None,
    disk_encryption=None,
    http_proxy=None,
    https_proxy=None,
    hyperthreading=None,
    ignition_endpoint=None,
    ingress_vips=None,
    machine_networks=None,
    network_type=None,
    no_proxy=None,
    ocp_release_image=None,
    platform=None,
    schedulable_masters=None,
    service_network_cidr=None,
    ssh_public_key=None,
    tags=None,
    vip_dhcp_allocation=None,
)


def _run(monkeypatch, token_response, post_response=None):
    module = FakeModule(dict(DEFAULT_PARAMS))
    monkeypatch.setattr(create_cluster, "AnsibleModule", lambda *a, **kw: module)
    monkeypatch.setattr(
        create_cluster.access_token, "_get_access_token", lambda offline_token: token_response
    )

    session = MagicMock()
    if post_response is not None:
        session.post.return_value = post_response
    monkeypatch.setattr(create_cluster.access_token, "_get_session", lambda: session)

    return module, session


class TestCreateCluster:
    def test_fails_cleanly_when_token_response_is_not_json(self, monkeypatch):
        # SSO returned a non-200 status with a body that isn't JSON at all
        # (e.g. an HTML error page during an outage).
        token_response = _mock_response(
            502, json_error=ValueError("Expecting value: line 1 column 1 (char 0)"), text=""
        )
        module, _session = _run(monkeypatch, token_response)

        with pytest.raises(SystemExit):
            create_cluster.run_module()

        assert module.fail_json_calls[0]["http_status"] == 502

    def test_fails_cleanly_when_create_cluster_response_is_not_json(self, monkeypatch):
        # Reproduces RHDP job 285609: the token call succeeds, but the
        # subsequent POST /clusters call to api.openshift.com returns a
        # non-JSON body.
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        post_response = _mock_response(
            503, json_error=ValueError("boom"), text="<html>Service Unavailable</html>"
        )
        module, session = _run(monkeypatch, token_response, post_response)

        with pytest.raises(SystemExit):
            create_cluster.run_module()

        assert session.post.called
        assert module.fail_json_calls, "a non-JSON create-cluster response must fail, not report changed=True"
        assert module.fail_json_calls[0]["result"]["http_status"] == 503

    def test_succeeds_on_valid_response(self, monkeypatch):
        token_response = _mock_response(200, json_value={"access_token": "tok-1"})
        post_response = _mock_response(200, json_value={"id": "new-cluster-id"})
        module, session = _run(monkeypatch, token_response, post_response)

        with pytest.raises(SystemExit):
            create_cluster.run_module()

        assert session.post.called
        assert module.exit_json_calls
        result = module.exit_json_calls[0]
        assert result["changed"] is True
        assert result["result"]["id"] == "new-cluster-id"
