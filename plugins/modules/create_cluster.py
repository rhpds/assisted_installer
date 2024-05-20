#!/usr/bin/python

# Copyright: (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type
import requests
import json

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.rhpds.assisted_installer.plugins.module_utils import access_token
from ansible_collections.rhpds.assisted_installer.plugins.module_utils import api
from ansible_collections.rhpds.assisted_installer.plugins.module_utils import defaults

DOCUMENTATION = r'''
---
module: create_cluster

short_description: Creates a new OpenShift cluster definition.

version_added: "1.0.0"

description: Creates a new OpenShift cluster definition using Assisted Installer

options:
    ai_api_endpoint:
        description: The AI Endpoint
        required: false
        type: str
    validate_certificate:
        description: validate the API certificate
        required: false
        type: bool
    name:
        description: Name of the cluster
        required: true
        type: str
    offline_token:
        description: Offline token from console.redhat.com
        required: true
        type: str
    openshift_version:
        description: OpenShift version to be installed
        required: true
        type: str
    pull_secret:
        description: Pull secret to be used for OpenShift
        required: true
        type: str
    base_dns_domain:
        description: Base domain of the cluster. All DNS records must be sub-domains of this base and include the cluster name.
        required: true
        type: str
    service_networks:
        description: Defines the service networks range
        required: false
        type: list
    olm_operators:
        description: Defines the operators to be installed
        required: false
        type: list
    high_availability_mode:
        description: Guaranteed availability of the installed cluster. 'Full' installs a Highly-Available cluster over multiple master nodes whereas 'None' installs a full cluster over one node.
        required: false
        type: str
    additional_ntp_source:
        description: A comma-separated list of NTP sources (name or IP) going to be added to all the hosts.
        required: false
        type: str
    api_vips:
        description: The virtual IPs used to reach the OpenShift cluster's API. Enter one IP address for single-stack clusters, or up to two for dual-stack clusters (at most one IP address per IP stack used). The order of stacks should be the same as order of subnets in Cluster Networks, Service Networks, and Machine Networks.
        required: false
        type: list
    cluster_network_cidr:
        description: IP address block from which Pod IPs are allocated. This block must not overlap with existing physical networks. These IP addresses are used for the Pod network, and if you need to access the Pods from an external network, configure load balancers and routers to manage the traffic.
        required: false
        type: str
    cluster_network_host_prefix:
        description: The subnet prefix length to assign to each individual node. For example, if clusterNetworkHostPrefix is set to 23, then each node is assigned a /23 subnet out of the given cidr (clusterNetworkCIDR), which allows for 510 (2^(32 - 23) - 2) pod IPs addresses. If you are required to provide access to nodes from an external network, configure load balancers and routers to manage the traffic
        required: false
        type: int
    cluster_networks:
        description: Cluster networks that are associated with this cluster.
        required: false
        type: list
    cpu_architecture:
        description: The CPU architecture of the image (x86_64/arm64/etc).
        required: false
        type: str
    disk_encryption:
        description: Enable/disable disk encryption
        required: false
        type: dict
    http_proxy:
        description: A proxy URL to use for creating HTTP connections outside the cluster.  http://<username>:<pswd>@<ip>:<port>
        required: false
        type: str
    https_proxy:
        description: A proxy URL to use for creating HTTPS connections outside the cluster.  http://<username>:<pswd>@<ip>:<port>
        required: false
        type: str
    hyperthreading:
        description: Enable/disable hyperthreading on master nodes, worker nodes, or all nodes.
        required: false
        type: str
    ignition_endpoint:
        description: Explicit ignition endpoint overrides the default ignition endpoint.
        required: false
        type: dict
    ingress_vips:
        description: The virtual IPs used for cluster ingress traffic. Enter one IP address for single-stack clusters, or up to two for dual-stack clusters (at most one IP address per IP stack used). The order of stacks should be the same as order of subnets in Cluster Networks, Service Networks, and Machine Networks.
        required: false
        type: list
    machine_networks:
        description: Machine networks that are associated with this cluster.
        required: false
        type: list
    network_type:
        description: The desired network type used.
        required: false
        type: str
    no_proxy:
        description: An "*" or a comma-separated list of destination domain names, domains, IP addresses, or other network CIDRs to exclude from proxying.
        required: false
        type: str
    ocp_release_image:
        description: OpenShift release image URI.
        required: false
        type: str
    platform:
        description: The configuration for the specific platform upon which to perform the installation.
        required: false
        type: dict
    schedulable_masters:
        description: Schedule workloads on masters
        required: false
        type: bool
    service_network_cidr:
        description: The IP address pool to use for service IP addresses. You can enter only one IP address pool. If you need to access the services from an external network, configure load balancers and routers to manage the traffic.
        required: false
        type: str
    ssh_public_key:
        description: SSH public key for debugging OpenShift nodes.
        required: false
        type: str
    tags:
        description: A comma-separated list of tags that are associated to the cluster.
        required: false
        type: str
    vip_dhcp_allocation:
        description: Indicate if virtual IP DHCP allocation mode is enabled.
        required: false
        type: bool

author:
    - Alberto Gonzalez (@agonzalezrh)
'''  # noqa

EXAMPLES = r'''
- name: Create a new SNO Assisted Installer Cluster
  rhpds.assisted_installer.create_cluster:
    name: "{{ cluster_name }}"
    high_availability_mode: "None"
    openshift_version: "{{ cluster_version }}"
    base_dns_domain: "{{ cluster_domain }}"
    offline_token: "{{ offline_token }}"
    pull_secret: "{{ pull_secret }}"
    service_networks:
      - cidr: "172.31.0.0/16"
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
        ai_api_endpoint=dict(type='str', required=False, default=defaults.ai_api_endpoint),
        validate_certificate=dict(type='bool', required=False, default=defaults.validate_certificate),
        name=dict(type='str', required=True),
        offline_token=dict(type='str', required=True),
        openshift_version=dict(type='str', required=True),
        pull_secret=dict(type='str', required=True),
        base_dns_domain=dict(type='str', required=True),
        service_networks=dict(type='list', required=False),
        olm_operators=dict(type='list', required=False),
        high_availability_mode=dict(type='str', required=False),
        additional_ntp_source=dict(type='str', required=False),
        api_vips=dict(type='list', required=False),
        cluster_network_cidr=dict(type='str', required=False),
        cluster_network_host_prefix=dict(type='int', required=False),
        cluster_networks=dict(type='list', required=False),
        cpu_architecture=dict(type='str', required=False),
        disk_encryption=dict(type='dict', required=False),
        http_proxy=dict(type='str', required=False),
        https_proxy=dict(type='str', required=False),
        hyperthreading=dict(type='str', required=False),
        ignition_endpoint=dict(type='dict', required=False),
        ingress_vips=dict(type='list', required=False),
        machine_networks=dict(type='list', required=False),
        network_type=dict(type='str', required=False),
        no_proxy=dict(type='str', required=False),
        ocp_release_image=dict(type='str', required=False),
        platform=dict(type='dict', required=False),
        schedulable_masters=dict(type='bool', required=False),
        service_network_cidr=dict(type='str', required=False),
        ssh_public_key=dict(type='str', required=False),
        tags=dict(type='str', required=False),
        vip_dhcp_allocation=dict(type='bool', required=False)
    )
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(max_retries=5)
    session.mount('https://', adapter)
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

    # if the user is working with this module in only check mode we do not
    # want to make any changes to the environment, just return the current
    # state with no modifications
    if module.check_mode:
        module.exit_json(**result)

    params = module.params.copy()
    params.pop("offline_token")
    params.pop("ai_api_endpoint")
    params.pop("validate_certificate")

    if "cluster_id" in params:
        params.pop("cluster_id")
    params["pull_secret"] = json.loads(params["pull_secret"])
    response = session.post(
        module.params['ai_api_endpoint'] + '/' + api.REGISTER_CLUSTER,
        headers=headers,
        json=params
    )
    result['result'] = response.json()
    # Key code only appears if there is an error
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
