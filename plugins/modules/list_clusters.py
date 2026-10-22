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
        description: If provided, returns only clusters that are owned by the current user.
        required: false
        type: bool
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
        owner=dict(type='bool', required=False),
        offline_token=dict(type='str', required=True)
    )

    # Use the shared retrying session (backs off on 429/5xx) for the
    # Assisted Installer API call too, not just the SSO token endpoint - a
    # plain requests.Session()/HTTPAdapter(max_retries=5) does not retry
    # POST or 5xx responses at all.
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
        params = module.params.copy()
        params.pop("offline_token")
        # params was previously built but never passed to session.get() below,
        # so every filter option here (owner, with_hosts, ams_subscription_ids,
        # etc.) was silently ignored and this always returned the full,
        # unfiltered list of clusters in the account. requests already omits
        # any key whose value is None from the query string, so unset optional
        # args are safe to pass through as-is.
        #
        # owner is type='bool' so Ansible correctly coerces a YAML `owner: true`
        # to the Python bool True instead of the string "True". requests would
        # otherwise serialize that bool as the query string "True" (str(True)),
        # and the API treats `?owner=True` as an unrecognized value, silently
        # returning zero clusters instead of the owned-clusters filter the
        # caller asked for. Normalize to the lowercase string the API expects.
        if params.get("owner") is not None:
            params["owner"] = str(params["owner"]).lower()
        response = session.get(
            "https://api.openshift.com/api/assisted-install/v2/clusters",
            headers=headers,
            params=params,
        )
    except requests.exceptions.RequestException as e:
        module.fail_json(msg='Request failed: {0}'.format(e))

    data = access_token.safe_json(response)
    if "code" in data or "http_status" in data:
        module.fail_json(msg='Request failed: ', **data)

    result['result'] = data

    # in the event of a successful module execution, you will want to
    # simple AnsibleModule.exit_json(), passing the key/value results
    module.exit_json(**result)


def main():
    run_module()


if __name__ == '__main__':
    main()
