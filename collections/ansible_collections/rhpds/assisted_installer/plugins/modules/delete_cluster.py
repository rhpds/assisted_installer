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

    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(max_retries=5)
    session.mount('https://', adapter)

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

    response = access_token._get_access_token(module.params['offline_token'])
    if response.status_code != 200:
        module.fail_json(msg='Error getting access token ', **response.json())

    # if the user is working with this module in only check mode we do not
    # want to make any changes to the environment, just return the current
    # state with no modifications
    if module.check_mode:
        module.exit_json(**result)

    headers = {
        "Authorization": "Bearer " + response.json()["access_token"],
        "Content-Type": "application/json"
    }
    if module.params['cancel']:
        response = session.post(
            "https://api.openshift.com/api/assisted-install/v2/clusters/" + module.params["cluster_id"] + "/actions/cancel",
            headers=headers,
        )
        if len(response.content) > 0 and "code" in response.json():
            result['result'] = response.json()
            module.fail_json(msg='Request failed: ', **result)
        else:
            result['changed'] = True

    response = session.delete(
        "https://api.openshift.com/api/assisted-install/v2/clusters/" + module.params["cluster_id"],
        headers=headers,
    )
    # Key code only appears if there is an error
    if len(response.content) > 0 and "code" in response.json():
        result['result'] = response.json()
        module.fail_json(msg='Request failed: ', **result)
    else:
        result['changed'] = True

    response = session.get(
        "https://api.openshift.com/api/assisted-install/v2/infra-envs/",
        headers=headers,
        params={"cluster_id": module.params["cluster_id"]}
    )
    for infra_env in response.json():
        response = session.delete(
            "https://api.openshift.com/api/assisted-install/v2/infra-envs/" + infra_env['id'],
            headers=headers,
        )
        if len(response.content) > 0 and "code" in response.json():
            result['result'] = response.json()
            module.fail_json(msg='Request failed: ', **result)

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
