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
module: list_clusters

short_description: Retrieves the list of OpenShift clusters.

version_added: "1.0.0"

description: Retrieves the list of OpenShift cluster using Assisted Installer.

options:
    get_unregistered_clusters:
        description: Whether to return clusters that have been unregistered.
        required: false
        type: bool
    openshift_cluster_id:
        description: A specific cluster to retrieve.
        required: false
        type: str
        choices: [ absent, present ]
    ams_subscription_ids:
        description: If non-empty, returned Clusters are filtered to those with matching subscription IDs.
        required: false
        type: list
    with_hosts:
        description: Include hosts in the returned list.
        required: false
        type: bool
    owner:
        description: If provided, returns only clusters that are owned by the specified user.
        required: false
        type: str
    offline_token:
        description: Offline token from console.redhat.com
        required: true
        type: str

author:
    - Alberto Gonzalez (@agonzalezrh)
'''

EXAMPLES = r'''
- name: Create a new SNO Assisted Installer Cluster
  rhpds.assisted_installer.clusters:
    name: "{{ cluster_name }}"
    high_availability_mode: "None"
    openshift_version: "{{ cluster_version }}"
    base_dns_domain: "{{ cluster_domain }}"
    offline_token: "{{ offline_token }}"
    pull_secret: "{{ pull_secret }}"
    high_availability_mode: "None"
  register: newcluster
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
        get_unregistered_clusters=dict(type='str', required=False),
        openshift_cluster_id=dict(type='str', required=False),
        ams_subscription_ids=dict(type='list', required=False),
        with_hosts=dict(type='bool', required=False),
        owner=dict(type='str', required=False),
        offline_token=dict(type='str', required=True)
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
    params = module.params.copy()
    params.pop("offline_token")
    response = session.get(
        "https://api.openshift.com/api/assisted-install/v2/clusters",
        headers=headers,
    )

    result['result'] = response.json()

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
