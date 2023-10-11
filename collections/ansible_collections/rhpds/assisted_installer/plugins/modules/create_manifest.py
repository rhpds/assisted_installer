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
module: create_manifest

short_description: Creates a new OpenShift Discovery ISO.

version_added: "1.0.0"

description: Creates a new OpenShift Discovery ISO for Assisted Installer

options:
    cluster_id:
        description: All hosts that register will be associated with the specified cluster.
        required: true
        type: str
    content:
        description: The content for the new manifest to create.
        required: true
        type: str
    file_name:
        description: The file_name for the new manifest to create.
        required: true
        type: str
    folder:
        description: The folderfor the new manifest to create.
        required: true
        type: str
    offline_token:
        description: Offline token from console.redhat.com
        required: true
author:
    - Alberto Gonzalez (@agonzalezrh)
'''  # noqa

EXAMPLES = r'''
- name: Create Infrastructure environment
  rhpds.assisted_installer.create_manifest:
    cluster_id: "{{ newcluster.result.id }}"
    content: "{{ etcd_disk }}"
    file_name: "10-masters-storage-config"
    folder: "manifests"
    offline_token: "{{ offline_token }}"
    pull_secret: "{{ pull_secret }}"
  register: newinfraenv
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
        content=dict(type='str', required=True),
        file_name=dict(type='str', required=True),
        folder=dict(type='str', required=True),
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

    response = access_token._get_access_token(module.params['offline_token'])
    if response.status_code != 200:
        module.fail_json(msg='Error getting access token ', **response.json())

    # if the user is working with this module in only check mode we do not
    # want to make any changes to the environment, just return the current
    # state with no modifications
    if module.check_mode:
        module.exit_json(**result)

    result['access_token'] = response.json()["access_token"]

    headers = {
        "Authorization": "Bearer " + response.json()["access_token"],
        "Content-Type": "application/json"
    }
    params = module.params.copy()
    params.pop("cluster_id")
    params.pop("offline_token")
    response = session.post(
        "https://api.openshift.com/api/assisted-install/v2/clusters/"
        + module.params["cluster_id"] + "/manifests",
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
