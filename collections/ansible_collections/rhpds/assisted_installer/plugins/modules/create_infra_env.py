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
module: create_infra_env

short_description: Creates a new OpenShift Discovery ISO.

version_added: "1.0.0"

description: Creates a new OpenShift Discovery ISO for Assisted Installer

options:
    additional_ntp_sources:
        description: A comma-separated list of NTP sources (name or IP) going to be added to all the hosts.
        required: false
        type: str
    additional_trust_bundle:
        description: 	PEM-encoded X.509 certificate bundle. Hosts discovered by this infra-env will trust the certificates in this bundle. Clusters formed from the hosts discovered by this infra-env will also trust the certificates in this bundle.  
        required: false
        type: str
    cluster_id:
        description: All hosts that register will be associated with the specified cluster.
        required: true
        type: str
    cpu_architecture:
        description: The CPU architecture of the image (x86_64/arm64/etc).
        required: false
        type: str
    ignition_config_override:
        description: JSON formatted string containing the user overrides for the initial ignition config.
        required: false
        type: str
    image_type:
        description: Type of image to be generated, full-iso or minimal-iso.
        required: false
        type: str
    kernel_arguments:
        description: List of kernel arugment objects that define the operations and values to be applied.
        required: false
        type: list
    name:
        description: Name of the infra-env.
        required: false
        type: str
    openshift_version:
        description: Version of the OpenShift cluster (used to infer the RHCOS version - temporary until generic logic implemented).
        required: false
        type: str
    proxy:
        description: Proxy configuration
        required: false
        type: dict
    pull_secret:
        description: The pull secret obtained from Red Hat OpenShift Cluster Manager at console.redhat.com/openshift/install/pull-secret.
        required: true
        type: str
    ssh_authorized_key:
        description: SSH public key for debugging the installation.
        required: false
        type: str
    static_network_config:
        description: Static network configuration
        required: false
        type: list
author:
    - Alberto Gonzalez (@agonzalezrh)
'''  # noqa

EXAMPLES = r'''
- name: Create Infrastructure environment
  rhpds.assisted_installer.create_infra_env:
    name: "{{ cluster_name }}-infra-env"
    image_type: "{{ cluster_iso_type }}"
    cluster_id: "{{ newcluster.result.id }}"
    ssh_authorized_key: "{{ ssh_authorized_key }}"
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
        name=dict(type='str', required=True),
        offline_token=dict(type='str', required=True),
        cluster_id=dict(type='str', required=True),
        pull_secret=dict(type='str', required=True),
        image_type=dict(type='str', required=False),
        ssh_authorized_key=dict(type='str', required=False),
        additional_ntp_sources=dict(type='str', required=False),
        additional_trust_bundle=dict(type='str', required=False),
        cpu_architecture=dict(type='str', required=False),
        ignition_config_override=dict(type='str', required=False),
        kernel_arguments=dict(type='list', required=False),
        openshift_version=dict(type='str', required=False),
        proxy=dict(type='dict', required=False),
        static_network_config=dict(type='list', required=False),
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
    params.pop("offline_token")
    params["pull_secret"] = json.loads(params["pull_secret"])
    response = session.post(
        "https://api.openshift.com/api/assisted-install/v2/infra-envs",
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
