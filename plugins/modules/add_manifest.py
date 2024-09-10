#!/usr/bin/python

# Copyright: (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type
import requests
import os

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
module: add_manifest
short_description: Downloads files relating to the installed/installing cluster.

version_added: "1.0.0"

description: Downloads files relating to the installed/installing cluster.

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
    offline_token:
        description: Offline token from console.redhat.com
        required: true
        type: str
    file_name:
        description: The manifest name.
        required: true
        type: str
    content:
        description: The manifest content.
        required: true
        type: str
    folder:
        description: The folder for the manifest.
        required: true
        type: str
author:
    - Rabin (@rabin-io)
'''

EXAMPLES = r'''
- name: Add custom manifest
  rhpds.assisted_installer.add_manifest:
    cluster_id: "{{ newcluster.result.id }}"
    offline_token: "{{ offline_token }}"
    file_name: "xyz.yaml"
    content: "{{ lookup('file', 'manifest.yaml') }}"
    folder: manifests
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
        file_name=dict(type='str', required=True),
        content=dict(type='str', required=True),
        folder=dict(type='str', required=False, default='manifests'),
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
        supports_check_mode=False
    )

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

    data = {
        "file_name": module.params['file_name'],
        "content": module.params['content'],
        "folder": module.params['folder']
    }

    my_logger.debug(str(data))

    response = session.post(
        f"{module.params['ai_api_endpoint']}/{api.REGISTER_CLUSTER}/{module.params['cluster_id']}/manifests",
        headers=headers,
        json=data
    )

    if "code" in response:
        module.fail_json(msg='Request failed: ' + str(response.json()))
    else:
        result['changed'] = True

    my_logger.debug(str(response.json()))

    result['result'] = response.content

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
