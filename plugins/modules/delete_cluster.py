#!/usr/bin/python

# Copyright: (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import requests
from ansible.module_utils.basic import AnsibleModule
from ansible_collections.rhpds.assisted_installer.plugins.module_utils import access_token

DOCUMENTATION = r'''
---
module:: Delete an OpenShift cluster definition.

version_added: "1.0.0"

description: Delete an OpenShift cluster definition from console

options:
    offline_token:
        description: Offline token from console.redhat.com
        required: true
        type: str
    cluster_id:
        description: Cluster ID to be delete
        required: false
        type: str
    cancel:
        description: The cluster whose installation is to be canceled.
        required: false
        type: str
        default: false


author:
    - Alberto Gonzalez (@agonzalezrh)
'''

EXAMPLES = r'''
- name: Remove Assisted Installer Cluster
  rhpds.assisted_installer.delete_cluster:
    cluster_id: "{{ cluster_id }}"
    offline_token: "{{ offline_token }}"
'''

RETURN = r'''
result:
    description: Result from the API call
    type: dict
    returned: always
'''


def run_module():
    # define available arguments/parameters a user can pass to the module
    module_args = dict(
        cluster_id=dict(type='str', required=True),
        offline_token=dict(type='str', required=True),
        cancel=dict(type='bool', required=False, default=False),
    )

    # Use the shared retrying session (backs off on 429/5xx) for the
    # Assisted Installer API calls too, not just the SSO token endpoint - a
    # plain requests.Session()/HTTPAdapter(max_retries=5) (connection
    # retries only) does not retry POST/DELETE/GET on 5xx responses at all.
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
        supports_check_mode=True,
    )

    # Connection-level failures (DNS, reset, timeout) still raise after the
    # shared session's retries are exhausted, regardless of
    # raise_on_status - requests surfaces those as RequestException
    # subclasses (ConnectionError, Timeout, RetryError, ...). Catch them
    # here so a sustained outage/network issue produces a clean module
    # failure instead of an unhandled traceback.
    try:
        response, token_data = access_token.get_access_token_data(module.params['offline_token'])
        if response.status_code != 200 or "access_token" not in token_data:
            module.fail_json(msg='Error getting access token ', **token_data)

        # if the user is working with this module in only check mode we do not
        # want to make any changes to the environment, just return the current
        # state with no modifications
        if module.check_mode:
            module.exit_json(**result)

        headers = {
            "Authorization": "Bearer " + token_data["access_token"],
            "Content-Type": "application/json"
        }
        if module.params['cancel']:
            response = session.post(
                "https://api.openshift.com/api/assisted-install/v2/clusters/" + module.params["cluster_id"] + "/actions/cancel",
                headers=headers,
            )
            # These DELETE/POST actions return an empty body (e.g. 204 No
            # Content) on success, and safe_json() can't tell "empty because it
            # succeeded" apart from "empty because of an outage" - it would
            # report both as {http_status, raw_body: ""}. So content length is
            # still checked first, same as before this fix, to avoid treating a
            # successful empty response as a failure. Only once there IS a
            # body do we need safe_json() instead of a bare response.json(), to
            # avoid crashing on a non-JSON error body (HTML error page, etc.)
            # from a transient 5xx/429 outage.
            if len(response.content) > 0:
                data = access_token.safe_json(response)
                if "code" in data or "http_status" in data:
                    result['result'] = data
                    module.fail_json(msg='Request failed: ', **result)
                else:
                    result['changed'] = True
            else:
                result['changed'] = True

        response = session.delete(
            "https://api.openshift.com/api/assisted-install/v2/clusters/" + module.params["cluster_id"],
            headers=headers,
        )
        # Key code only appears if there is an error
        if len(response.content) > 0:
            data = access_token.safe_json(response)
            if "code" in data or "http_status" in data:
                result['result'] = data
                module.fail_json(msg='Request failed: ', **result)
            else:
                result['changed'] = True
        else:
            result['changed'] = True

        response = session.get(
            "https://api.openshift.com/api/assisted-install/v2/infra-envs/",
            headers=headers,
            params={"cluster_id": module.params["cluster_id"]}
        )
        # Unlike the DELETE/POST actions above, a successful GET here always
        # returns a JSON list body (possibly empty, "[]"), never an empty one,
        # so there's no empty-body-means-success ambiguity - safe_json() can be
        # used unconditionally. A failure response is a JSON (or non-JSON, on
        # an outage) dict with "code"/"http_status" instead of a list.
        infra_envs = access_token.safe_json(response)
        if isinstance(infra_envs, dict) and ("code" in infra_envs or "http_status" in infra_envs):
            result['result'] = infra_envs
            module.fail_json(msg='Request failed: ', **result)
        for infra_env in infra_envs:
            response = session.delete(
                "https://api.openshift.com/api/assisted-install/v2/infra-envs/" + infra_env['id'],
                headers=headers,
            )
            if len(response.content) > 0:
                data = access_token.safe_json(response)
                if "code" in data or "http_status" in data:
                    result['result'] = data
                    module.fail_json(msg='Request failed: ', **result)
    except requests.exceptions.RequestException as e:
        module.fail_json(msg='Request failed: {0}'.format(e))

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
