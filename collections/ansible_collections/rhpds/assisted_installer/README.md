# Ansible Collection for OpenShift Assisted Installer
This collection allows to create a cluster using the **OpenShift Assisted Installer**. 

## OpenShift Assisted Installer

OpenShift Assisted Installer is a user-friendly OpenShift installation solution for the various platforms.

The Assisted Service provides hosting of installation artifacts (Ignition files, installation configuration, discovery ISO), exposed UI or REST API, etc.

Assisted Installer provide the following advantages:

* A web interface to perform cluster installation without having to create the installation configuration file.
* Boostrap machine is no longer required, the bootstrapping process takes place on a random node of the cluster.
* A simplified deployment model that does not require in-depth knowledge of OpenShift.
* Flexible API.
* Deploying Single Node OpenShift (SNO).
* Installing OpenShift Virtualization and OpenShift Data Foundation (formerly OpenShift Container Storage) from the web interface.

## OpenShift Assisted Installer Ansible Collection

This collection allows you create, query and delete OpenShift Clusters using the Assisted Installer. 

The list of modules inside of the collection are the following:

Name | Description
--- | ---
rhpds.assisted_installer.create_cluster|Creates a new OpenShift cluster definition.
rhpds.assisted_installer.create_infra_env|Creates a new OpenShift Discovery ISO.
rhpds.assisted_installer.delete_cluster|Delete an OpenShift cluster definition.
rhpds.assisted_installer.download_credentials|Downloads credentials relating to the installed/installing cluster.
rhpds.assisted_installer.download_files|Downloads files relating to the installed/installing cluster.
rhpds.assisted_installer.get_credentials|Get the cluster admin credentials.
rhpds.assisted_installer.install_cluster|Installs the OpenShift cluster.
rhpds.assisted_installer.list_clusters| Retrieves the list of OpenShift clusters.
rhpds.assisted_installer.wait_for_hosts|Wait for the hosts to be ready and configure them.


## Example Usage

In this simple example, a **Single Node OpenShift** is created inside **OpenShift Virtualization**

```yaml
- name: Add the service (type LoadBalancer) for SNO
    kubernetes.core.k8s:
    definition: "{{ lookup('ansible.builtin.template', 'templates/sno_svc.yaml') }}"
    wait: true
    wait_timeout: 300
    vars:
    vmname: "{{ ocp_vmname_sno }}"
    svcname: "{{ ocp_vmname_sno }}-svc"
    namespace: "{{ ocp_namespace }}"

- name: Wait for the LoadBalancer value
    register: sno_svc
    kubernetes.core.k8s_info:
    api_version: v1
    kind: Service
    name: "{{ ocp_vmname_sno }}-svc"
    namespace: "{{ ocp_namespace }}"
    until: sno_svc.resources[0].status.loadBalancer.ingress[0].hostname | default('') != ''
    retries: 10
    delay: 2

- name: Add CNAME dns record
    amazon.aws.route53:
    state: present
    zone: "{{ dns_zone }}"
    record: "{{ item }}.{{ cluster_name}}.{{ cluster_domain }}"
    type: CNAME
    value: "{{ sno_svc.resources[0].status.loadBalancer.ingress[0].hostname }}"
    ttl: 30
    loop:
    - "api"
    - "*.apps"

- name: Create Assisted Installer Cluster
    rhpds.assisted_installer.create_cluster:
    name: "{{ cluster_name }}"
    openshift_version: "{{ cluster_version }}"
    base_dns_domain: "{{ cluster_domain }}"
    offline_token: "{{ offline_token }}"
    pull_secret: "{{ pull_secret }}"
    high_availability_mode: "None"
    service_networks:
        - cidr: "172.31.0.0/16"
    register: newcluster

- name: Create Infrastructure environment
    rhpds.assisted_installer.create_infra_env:
    name: "{{ cluster_name }}-infra-env"
    image_type: "{{ cluster_iso_type }}"
    cluster_id: "{{ newcluster.result.id }}"
    ssh_authorized_key: "{{ ssh_authorized_key }}"
    offline_token: "{{ offline_token }}"
    pull_secret: "{{ pull_secret }}"
    register: newinfraenv

- name: Create a SNO VM
    kubernetes.core.k8s:
    definition: "{{ lookup('ansible.builtin.template', 'templates/sno.yaml') }}"
    wait: true
    wait_timeout: 300
    wait_condition:
        status: true
        type: Ready
    vars:
    image_url: "{{ newinfraenv.result.download_url }}"
    vmname: "{{ ocp_vmname_sno }}"
    namespace: "{{ ocp_namespace }}"
    storageclass: "gp3-csi"

- name: Wait for the hosts to be ready
    rhpds.assisted_installer.wait_for_hosts:
    cluster_id: "{{ newcluster.result.id }}"
    offline_token: "{{ offline_token }}"
    expected_hosts: 1
    wait_timeout: 600

- name: Start cluster installation
    rhpds.assisted_installer.install_cluster:
    cluster_id: "{{ newcluster.result.id }}"
    offline_token: "{{ offline_token }}"
    wait_timeout: 1200

- name: Obtain OpenShift cluster credentials
    register: credentials
    rhpds.assisted_installer.get_credentials:
    cluster_id: "{{ newcluster.result.id }}"
    offline_token: "{{ offline_token }}"

- name: Display credentials
    debug:
    var: credentials.result
```

## Examples Playbooks ready to use

* [List existing clusters](https://github.com/rhpds/assisted_installer/blob/main/examples/list_clusters.yaml)
* OpenShift Virtualization/Kubevirt examples:
    * [Install SNO](https://github.com/rhpds/assisted_installer/blob/main/examples/sno_kubevirt.yaml)
    * [Install SNO with operator LSO](https://github.com/rhpds/assisted_installer/blob/main/examples/sno_lso_kubevirt.yaml)
    * [Install Compact cluster](https://github.com/rhpds/assisted_installer/blob/main/examples/compact_kubevirt.yaml)
    * [Install Compact cluster with ODF](https://github.com/rhpds/assisted_installer/blob/main/examples/compact_odf_kubevirt.yaml)
    * [Install Full cluster](https://github.com/rhpds/assisted_installer/blob/main/examples/full_kubevirt.yaml)
    * [Install Full cluster with ODF](https://github.com/rhpds/assisted_installer/blob/main/examples/full_odf_kubevirt.yaml)

* vShere examples: coming soon
* other platforms: coming soon

To try them:
```sh
cd examples/
vim vars/config.yaml
ansible-playbook sno_kubevirt.yaml (or another example)
```

**_IMPORTANT_**: Default examples are using AWS Route53 for the DNS, the testing cluster is an AWS Cluster with baremetal nodes.

## Licensing

GNU General Public License v3.0 or later.

See [LICENSE](https://www.gnu.org/licenses/gpl-3.0.txt) to see the full text.
