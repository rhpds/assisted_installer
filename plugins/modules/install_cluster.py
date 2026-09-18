#!/usr/bin/python

# Copyright: (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type
import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.rhpds.assisted_installer.plugins.module_utils import access_token


DOCUMENTATION = r'''
---
module: install_cluster

short_description: Installs the OpenShift cluster.

version_added: "1.0.0"

description: creates a new cluster on console.redhat.com/openshift

options:
    cluster_id:
        description: ID of the cluster
        required: true
        type: str
    wait_timeout:
        description: Wait timeout in seconds
        required: False
        type: int
        default: 1800
    offline_token:
        description: Offline token from console.redhat.com
        required: true
        type: str
    delay:
        description: Delay time between checks
        required: False
        type: int
        default: 60

author:
    - Alberto Gonzalez (@agonzalezrh)
'''

EXAMPLES = r'''
- name: Start cluster installation
  rhpds.assisted_installer.install_cluster:
    cluster_id: "{{ newcluster.result.id }}"
    offline_token: "{{ offline_token }}"
    wait_timeout: 1800
    delay: 60
'''

RETURN = r'''
result:
    description: Result from the API call
    type: dict
    returned: always

'''

ASSISTED_INSTALLER_API = "https://api.openshift.com/api/assisted-install/v2"

# Red Hat SSO access tokens are typically valid for several minutes. Refresh
# a bit before actual expiry (clock skew / a slow round-trip) instead of
# calling the token endpoint on every single polling iteration - this both
# reduces load on sso.redhat.com and reduces how often we are exposed to any
# transient errors it returns.
TOKEN_REFRESH_SKEW_SECONDS = 60
DEFAULT_TOKEN_LIFETIME_SECONDS = 300


class AccessTokenCache:
    """Caches an SSO access token for the duration of a single module run,
    only refreshing it once it is close to expiring instead of on every
    polling iteration."""

    def __init__(self, module, offline_token):
        self._module = module
        self._offline_token = offline_token
        self._token = None
        self._expires_at = 0.0

    def get(self):
        if self._token is None or time.monotonic() >= self._expires_at:
            self._refresh()
        return self._token

    def _refresh(self):
        response, data = access_token.get_access_token_data(self._offline_token)
        if response.status_code != 200 or "access_token" not in data:
            self._module.fail_json(msg='Error getting access token ', **data)
        self._token = data["access_token"]
        expires_in = data.get("expires_in", DEFAULT_TOKEN_LIFETIME_SECONDS)
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            expires_in = DEFAULT_TOKEN_LIFETIME_SECONDS
        self._expires_at = time.monotonic() + max(expires_in - TOKEN_REFRESH_SKEW_SECONDS, 0)


def run_module():
    # define available arguments/parameters a user can pass to the module
    module_args = dict(
        cluster_id=dict(type='str', required=True),
        offline_token=dict(type='str', required=True),
        wait_timeout=dict(type='int', required=False, default=1800),
        delay=dict(type='int', required=False, default=60),
    )

    session = access_token._get_session()

    # seed the result dict in the object
    # we primarily care about changed and state
    # changed is if this module effectively modified the target
    # state will include any data that you want your module to pass back
    # for consumption, for example, in a subsequent task
    result = dict(
        changed=False,
    )

    # the AnsibleModule object will be our abstraction working with Ansible
    # this includes instantiation, a couple of common attr would be the
    # args/params passed to the execution, as well as if the module
    # supports check mode
    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    token_cache = AccessTokenCache(module, module.params['offline_token'])

    def auth_headers():
        return {
            "Authorization": "Bearer " + token_cache.get(),
            "Content-Type": "application/json"
        }

    response = session.post(
        ASSISTED_INSTALLER_API + "/clusters/" + module.params['cluster_id'] + "/actions/install",
        headers=auth_headers(),
    )
    data = access_token.safe_json(response)
    if "code" in data:
        module.fail_json(msg='ERROR: ', **data)

    retries = 0
    cluster_installed = False
    max_retries = module.params['wait_timeout'] / module.params['delay']

    while retries < max_retries and cluster_installed is False:
        # if the user is working with this module in only check mode we do not
        # want to make any changes to the environment, just return the current
        # state with no modifications
        if module.check_mode:
            module.exit_json(**result)

        # manipulate or modify the state as needed (this is going to be the
        # part where your module will do what it needs to do)
        result['access_token'] = token_cache.get()

        response = session.get(
            ASSISTED_INSTALLER_API + "/clusters/" + module.params['cluster_id'],
            headers=auth_headers(),
        )
        data = access_token.safe_json(response)
        if "code" in data:
            module.fail_json(msg='ERROR: ', **data)
        if data.get('status') == "installed":
            cluster_installed = True
            result['result'] = data
        elif data.get('status') == "ready":
            # Retry start installation if the cluster was moved to ready again
            response = session.post(
                ASSISTED_INSTALLER_API + "/clusters/" + module.params['cluster_id'] + "/actions/install",
                headers=auth_headers(),
            )
            data = access_token.safe_json(response)
            if "code" in data:
                module.fail_json(msg='ERROR: ', **data)
        else:
            time.sleep(module.params['delay'])

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
