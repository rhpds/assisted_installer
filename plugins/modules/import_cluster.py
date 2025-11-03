#!/usr/bin/python

# Copyright: (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type
import requests
import json

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.rhpds.assisted_installer.plugins.module_utils import access_token


DOCUMENTATION = r'''
---
module: import_cluster

short_description: Import an AI cluster using minimal data associated with existing OCP cluster, in order to allow adding day2 hosts to that cluster

version_added: "1.0.0"

description: Import an AI cluster using minimal data associated with existing OCP cluster, in order to allow adding day2 hosts to that cluster

options:
    cluster_name:
        description: All hosts that register will be associated with the specified cluster.
        required: true
        type: str
    offline_token:
        description: Offline token from console.redhat.com
        required: true
    api_vip_dnsname:
        description: API DNS Domain of the cluster to be imported
        required: true
    openshift_cluster_id:
        description: Cluster ID of the cluster to be imported
        required: true
    api_endpoint:
        description: API endpoint URL
        required: false
        type: str
        default: https://api.openshift.com
author:
    - Alberto Gonzalez (@agonzalezrh)
'''  # noqa

EXAMPLES = r'''
- name: Import existing cluster
  rhpds.assisted_installer.import_cluster:
    cluster_name: "{{ cluster_name }}"
    offline_token: "{{ offline_token }}"
    api_vip_dnsname: "api.domain.com"
    openshift_cluster_id: "1234"
  register: imported_cluster
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
        cluster_name=dict(type='str', required=True),
        offline_token=dict(type='str', required=False),
        api_vip_dnsname=dict(type='str', required=True),
        openshift_cluster_id=dict(type='str', required=True),
        api_endpoint=dict(type='str', required=False, default='https://api.openshift.com')
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
        supports_check_mode=True
    )

    # Require offline_token only when using Red Hat's endpoint
    api_endpoint = module.params.get('api_endpoint', 'https://api.openshift.com')
    if api_endpoint == 'https://api.openshift.com' and not module.params.get('offline_token'):
        module.fail_json(msg='offline_token is required when using https://api.openshift.com')

    # Get access token only for Red Hat endpoint
    headers = {"Content-Type": "application/json"}
    if api_endpoint == 'https://api.openshift.com':
        response = access_token._get_access_token(module.params['offline_token'])
        if response.status_code != 200:
            module.fail_json(msg='Error getting access token ', **response.json())
        result['access_token'] = response.json()["access_token"]
        headers["Authorization"] = "Bearer " + response.json()["access_token"]

    # if the user is working with this module in only check mode we do not
    # want to make any changes to the environment, just return the current
    # state with no modifications
    if module.check_mode:
        module.exit_json(**result)

    params = module.params.copy()
    params.pop("cluster_name")
    if "offline_token" in params:
        params.pop("offline_token")
    if "api_endpoint" in params:
        params.pop("api_endpoint")
    params["name"] = module.params["cluster_name"]
    response = session.post(
        api_endpoint + "/api/assisted-install/v2/clusters/import",
        headers=headers,
        json=params
    )

    result['result'] = response.json()

    if "code" in response.json():
        module.fail_json(msg='Request failed: ', **result)
    else:
        result['changed'] = True

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
