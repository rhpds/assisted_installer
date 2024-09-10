#!/usr/bin/python

# Copyright: (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type
import requests
import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.rhpds.assisted_installer.plugins.module_utils import access_token
from ansible_collections.rhpds.assisted_installer.plugins.module_utils import api
from ansible_collections.rhpds.assisted_installer.plugins.module_utils import defaults

import logging
import logging.handlers
my_logger = logging.getLogger('MyLogger')
my_logger.setLevel(logging.DEBUG)
handler = logging.handlers.SysLogHandler(address = '/dev/log')
my_logger.addHandler(handler)

DOCUMENTATION = r'''
---
module: install_cluster

short_description: Installs the OpenShift cluster.

version_added: "1.0.0"

description: creates a new cluster on console.redhat.com/openshift

options:
    ai_api_endpoint:
        description: The AI Endpoint
        required: false
        type: str
    validate_certificate:
        description: validate the API certificate
        required: false
        type: bool
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


def run_module():
    # define available arguments/parameters a user can pass to the module
    module_args = dict(
        ai_api_endpoint=dict(type='str', required=False, default=defaults.ai_api_endpoint),
        validate_certificate=dict(type='bool', required=False, default=defaults.validate_certificate),
        cluster_id=dict(type='str', required=True),
        offline_token=dict(type='str', required=True),
        wait_timeout=dict(type='int', required=False, default=1800),
        delay=dict(type='int', required=False, default=60),
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
    headers = {
        "Content-Type": "application/json"
    }
    if module.params['offline_token'] != 'None':
        response = access_token._get_access_token(module.params['offline_token'])
        if response.status_code != 200:
            module.fail_json(msg='Error getting access token ', **response.json())
        headers.update({
            "Authorization": "Bearer " + response.json()["access_token"],
        })
        result['access_token'] = response.json()["access_token"]

    ### TODO: Can we make idempotent?
    response = session.post(
        f"{module.params['ai_api_endpoint']}/{api.REGISTER_CLUSTER}/{module.params['cluster_id']}/actions/install",
        headers=headers,
    )
    if "code" in response.json():
        module.fail_json(msg='ERROR: ', **response.json())

    retries = 0
    cluster_installed = False
    max_retries = module.params['wait_timeout'] / module.params['delay']

    while retries < max_retries and cluster_installed is False:
        headers = {
            "Content-Type": "application/json"
        }
        if module.params['offline_token'] != 'None':
            response = access_token._get_access_token(module.params['offline_token'])
            if response.status_code != 200:
                module.fail_json(msg='Error getting access token ', **response.json())
            result['access_token'] = response.json()["access_token"]
            headers.update({
                "Authorization": "Bearer " + response.json()["access_token"],
            })
        # if the user is working with this module in only check mode we do not
        # want to make any changes to the environment, just return the current
        # state with no modifications
        if module.check_mode:
            module.exit_json(**result)

        # manipulate or modify the state as needed (this is going to be the
        # part where your module will do what it needs to do)
        response = session.get(
            f"{module.params['ai_api_endpoint']}/{api.REGISTER_CLUSTER}/{module.params['cluster_id']}",
            headers=headers,
        )
        if "code" in response.json():
            module.fail_json(msg='ERROR: ', **response.json())
        if response.json()['status'] == "installed":
            cluster_installed = True
            result['result'] = response.json()
        else:
            time.sleep(module.params['delay'])

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
