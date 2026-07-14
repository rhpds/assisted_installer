#!/usr/bin/python

# Copyright: (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type
import json
import requests
import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.rhpds.assisted_installer.plugins.module_utils import access_token


DOCUMENTATION = r'''
---
module: wait_for_hosts

short_description: Wait for the hosts to be ready and configure them.

version_added: "1.0.0"

description: Wait for the hosts to be ready and configure them.

options:
    cluster_id:
        description: ID of the cluster
        required: true
        type: str
    infra_env_id:
        description: The infra-env ID of the host to be updated. Required with hosts are assigned.
        required: false
        type: str
    offline_token:
        description: Offline token from console.redhat.com
        required: true
        type: str
    expected_hosts:
        description: Expected number of the hosts
        required: true
        type: int
    wait_timeout:
        description: Wait timeout in seconds
        required: False
        type: int
        default: 600
    delay:
        description: Delay time between checks
        required: False
        type: int
        default: 10

author:
    - Alberto Gonzalez (@agonzalezrh)
'''

EXAMPLES = r'''
- name: Wait for the hosts to be ready
  rhpds.assisted_installer.wait_for_hosts:
    cluster_id: "{{ newcluster.result.id }}"
    offline_token: "{{ offline_token }}"
    expected_hosts: 1
    wait_timeout: 1200
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
        infra_env_id=dict(type='str', required=False),
        offline_token=dict(type='str', required=True),
        expected_hosts=dict(type='int', required=True),
        wait_timeout=dict(type='int', required=False, default=600),
        delay=dict(type='int', required=False, default=10),
        configure_hosts=dict(type='list', required=False),
    )

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
        required_together=[['configure_hosts', 'infra_env_id']]
    )

    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(max_retries=5)
    session.mount('https://', adapter)

    retries = 0
    cluster_ready = False
    max_retries = module.params['wait_timeout'] / module.params['delay']
    last_json_error = None
    cluster_data = None
    while retries < max_retries and cluster_ready is False:
        retries += 1
        response = access_token._get_access_token(module.params['offline_token'])
        if response.status_code != 200:
            try:
                error_data = response.json()
            except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
                error_data = {"detail": "Access token request failed with status %d and non-JSON response" % response.status_code}
            module.fail_json(msg='Error getting access token ', **error_data)

        # if the user is working with this module in only check mode we do not
        # want to make any changes to the environment, just return the current
        # state with no modifications
        if module.check_mode:
            module.exit_json(**result)

        try:
            token_data = response.json()
        except (json.JSONDecodeError, requests.exceptions.JSONDecodeError) as e:
            module.warn("Failed to decode access token response (attempt %d/%d): %s" % (retries, int(max_retries), str(e)))
            time.sleep(module.params['delay'])
            continue

        # manipulate or modify the state as needed (this is going to be the
        # part where your module will do what it needs to do)
        result['access_token'] = token_data["access_token"]

        headers = {
            "Authorization": "Bearer " + token_data["access_token"],
            "Content-Type": "application/json"
        }
        response = session.get(
            "https://api.openshift.com/api/assisted-install/v2/clusters/" + module.params['cluster_id'],
            headers=headers,
        )

        try:
            cluster_data = response.json()
        except (json.JSONDecodeError, requests.exceptions.JSONDecodeError) as e:
            last_json_error = str(e)
            module.warn("Failed to decode cluster API response (attempt %d/%d): %s" % (retries, int(max_retries), last_json_error))
            time.sleep(module.params['delay'])
            continue

        if "code" in cluster_data:
            module.fail_json(msg='Request failed: ', **cluster_data)
        ready_hosts = 0
        added_hosts = 0
        for host in cluster_data['hosts']:
            if host['status'] == "known":
                ready_hosts = ready_hosts + 1
                if cluster_data['status'] == 'adding-hosts':
                    responsepost = session.post(
                        "https://api.openshift.com/api/assisted-install/v2/infra-envs/" + module.params['infra_env_id'] + "/hosts/" + host['id'] + "/actions/install",
                        headers=headers
                    )
                    try:
                        post_data = responsepost.json()
                    except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
                        post_data = {}
                    if "code" in post_data:
                        module.fail_json(msg='Request failed: ', **post_data)
            if host['status'] == 'added-to-existing-cluster':
                added_hosts = added_hosts + 1
            if 'configure_hosts' in module.params and module.params['configure_hosts'] is not None:
                for configure_host in module.params['configure_hosts']:
                    if host['requested_hostname'] == configure_host['hostname']:
                        if host['role'] != configure_host['role']:
                            data = {"host_role": configure_host['role']}
                            responsepatch = session.patch(
                                "https://api.openshift.com/api/assisted-install/v2/infra-envs/" + module.params['infra_env_id'] + "/hosts/" + host['id'],
                                headers=headers,
                                json=data
                            )
                            try:
                                patch_data = responsepatch.json()
                            except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
                                patch_data = {}
                            if "code" in patch_data:
                                module.fail_json(msg='Request failed: ', **patch_data)
                        if "installation_disk" in configure_host:
                            if host['installation_disk_path'] != configure_host['installation_disk']:
                                data = {"disks_selected_config": [{"id": configure_host['installation_disk'], "role": "install"}]}
                                responsepatch = session.patch(
                                    "https://api.openshift.com/api/assisted-install/v2/infra-envs/" + module.params['infra_env_id'] + "/hosts/" + host['id'],
                                    headers=headers,
                                    json=data
                                )
                                try:
                                    patch_data = responsepatch.json()
                                except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
                                    patch_data = {}
                                if "code" in patch_data:
                                    module.fail_json(msg='Request failed: ', **patch_data)
                        if "newname" in configure_host:
                                data = {"host_name": config_host["newname"]}
                                responsepatch = session.patch(
                                    "https://api.openshift.com/api/assisted-install/v2/infra-envs/" + module.params['infra_env_id'] + "/hosts/" + host['id'],
                                    headers=headers,
                                    json=data
                                )
                                try:
                                    patch_data = responsepatch.json()
                                except (json.JSONDecodeError, requests.exceptions.JSONDecodeError):
                                    patch_data = {}
                                if "code" in patch_data:
                                    module.fail_json(msg='Request failed: ', **patch_data)

            if ready_hosts == module.params['expected_hosts'] and cluster_data['status'] == "ready":
                cluster_ready = True
                result['result'] = cluster_data
            elif added_hosts == module.params['expected_hosts'] and cluster_data['status'] == "adding-hosts":
                cluster_ready = True
                result['result'] = cluster_data
            else:
                time.sleep(module.params['delay'])

    if cluster_data is not None:
        result['result'] = cluster_data
    elif last_json_error is not None:
        module.fail_json(msg='Timed out waiting for hosts. Last error: non-JSON API response: %s' % last_json_error)

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
