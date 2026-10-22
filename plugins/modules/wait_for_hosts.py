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

ASSISTED_INSTALLER_API = "https://api.openshift.com/api/assisted-install/v2"


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

    # Use the shared retrying session (backs off on 429/5xx) for the Assisted
    # Installer API calls too, not just the SSO token endpoint - a plain
    # requests.Session()/HTTPAdapter(max_retries=5) does not retry POST/PATCH
    # or 5xx responses at all.
    session = access_token._get_session()

    # Cache the SSO access token instead of fetching a new one on every
    # polling iteration (this loop can run up to wait_timeout/delay times).
    # This both reduces load on sso.redhat.com and reduces how often this
    # module is exposed to any transient errors it returns.
    token_cache = access_token.AccessTokenCache(module, module.params['offline_token'])

    def auth_headers():
        return {
            "Authorization": "Bearer " + token_cache.get(),
            "Content-Type": "application/json"
        }

    retries = 0
    cluster_ready = False
    data = {}
    max_retries = module.params['wait_timeout'] / module.params['delay']
    while retries < max_retries and cluster_ready is False:
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
        if "code" in data or "http_status" in data:
            module.fail_json(msg='Request failed: ', **data)
        ready_hosts = 0
        added_hosts = 0
        for host in data['hosts']:
            if host['status'] == "known":
                ready_hosts = ready_hosts + 1
                if data['status'] == 'adding-hosts':
                    responsepost = session.post(
                        ASSISTED_INSTALLER_API + "/infra-envs/" + module.params['infra_env_id'] + "/hosts/" + host['id'] + "/actions/install",
                        headers=auth_headers()
                    )
                    post_data = access_token.safe_json(responsepost)
                    if "code" in post_data or "http_status" in post_data:
                        module.fail_json(msg='Request failed: ', **post_data)
            if host['status'] == 'added-to-existing-cluster':
                added_hosts = added_hosts + 1
            if 'configure_hosts' in module.params and module.params['configure_hosts'] is not None:
                for configure_host in module.params['configure_hosts']:
                    if host['requested_hostname'] == configure_host['hostname']:
                        if host['role'] != configure_host['role']:
                            patch_body = {"host_role": configure_host['role']}
                            responsepatch = session.patch(
                                ASSISTED_INSTALLER_API + "/infra-envs/" + module.params['infra_env_id'] + "/hosts/" + host['id'],
                                headers=auth_headers(),
                                json=patch_body
                            )
                            patch_data = access_token.safe_json(responsepatch)
                            if "code" in patch_data or "http_status" in patch_data:
                                module.fail_json(msg='Request failed: ', **patch_data)
                        if "installation_disk" in configure_host:
                            if host['installation_disk_path'] != configure_host['installation_disk']:
                                patch_body = {"disks_selected_config": [{"id": configure_host['installation_disk'], "role": "install"}]}
                                responsepatch = session.patch(
                                    ASSISTED_INSTALLER_API + "/infra-envs/" + module.params['infra_env_id'] + "/hosts/" + host['id'],
                                    headers=auth_headers(),
                                    json=patch_body
                                )
                                patch_data = access_token.safe_json(responsepatch)
                                if "code" in patch_data or "http_status" in patch_data:
                                    module.fail_json(msg='Request failed: ', **patch_data)
                        if "newname" in configure_host:
                            patch_body = {"host_name": configure_host["newname"]}
                            responsepatch = session.patch(
                                ASSISTED_INSTALLER_API + "/infra-envs/" + module.params['infra_env_id'] + "/hosts/" + host['id'],
                                headers=auth_headers(),
                                json=patch_body
                            )
                            patch_data = access_token.safe_json(responsepatch)
                            if "code" in patch_data or "http_status" in patch_data:
                                module.fail_json(msg='Request failed: ', **patch_data)

        # Evaluate readiness once per poll, after all hosts in this response
        # have been processed - not once per host. (This used to be nested
        # inside the `for host in data['hosts']:` loop above, which judged
        # readiness against a partial, still-accumulating per-host tally and
        # could call time.sleep() multiple times in a single poll whenever
        # there was more than one host.)
        if ready_hosts == module.params['expected_hosts'] and data['status'] == "ready":
            cluster_ready = True
            result['result'] = data
        elif added_hosts == module.params['expected_hosts'] and data['status'] == "adding-hosts":
            cluster_ready = True
            result['result'] = data
        else:
            time.sleep(module.params['delay'])
        retries += 1

    result['result'] = data

    if not cluster_ready:
        module.fail_json(msg='Timed out waiting for hosts to be ready', **result)

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
