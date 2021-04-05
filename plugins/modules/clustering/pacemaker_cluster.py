#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2016, Mathieu Bultel <mbultel@redhat.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = '''
---
module: pacemaker_cluster
short_description: Manage pacemaker clusters
author:
- Mathieu Bultel (@matbu)
description:
   - This module can manage a pacemaker cluster and nodes from Ansible using
     the pacemaker cli.
options:
    state:
      description:
        - Indicate desired state of the cluster
      choices: [ cleanup, offline, online, restart ]
      type: str
    node:
      description:
        - Specify which node of the cluster you want to manage. None == the
          cluster status itself, 'all' == check the status of all nodes.
      type: str
    timeout:
      description:
        - Timeout when the module should considered that the action has failed
      default: 300
      type: int
    force:
      description:
        - Force the change of the cluster state
      type: bool
      default: 'yes'
    enable:
      description:
        - Enable automatic cluster startup on all nodes
      type: bool
      default: 'yes'
'''
EXAMPLES = '''
---
- name: Set cluster Online
  hosts: localhost
  gather_facts: no
  tasks:
  - name: Get cluster state
    community.general.pacemaker_cluster:
      state: online
'''

RETURN = '''
changed:
    description: True if the cluster state has changed
    type: bool
    returned: always
out:
    description: The output of the current state of the cluster. It return a
                 list of the nodes state.
    type: str
    sample: 'out: [["  overcloud-controller-0", " Online"]]}'
    returned: always
rc:
    description: exit code of the module
    type: bool
    returned: always
'''

import time, re

from ansible.module_utils.basic import AnsibleModule


_PCS_CLUSTER_DOWN = "Error: cluster is not currently running on this node"

class ClusterConfiguration(object):
    def __init__(self, name, nodes, active_nodes, stonith_devices, resource_groups):
        self.name = name
        self.nodes = nodes
        self.active_nodes = active_nodes
        self.stonith_devices = stonith_devices
        self.resource_groups = resource_groups

def get_cluster_status(module):
    cmd = "pcs cluster status"
    rc, out, err = module.run_command(cmd)
    if out in _PCS_CLUSTER_DOWN:
        return 'offline'
    else:
        return 'online'


def get_cluster_configuration(module):
    # parse output of 'pcs cluster config' into object of type ClusterConfiguration
    cmd = "pcs config"
    rc, out, err = module.run_command(cmd)
    if rc == 1:
        module.fail_json(msg="Failed to get cluster configuration.\nCommand: `%s`\nError: %s" % (cmd, err))
    match_object = search(r'Cluster Name: (.*?)\n', out)
    name = match_object.group(1)
    match_object = search(r'Corosync Nodes:\n((?:\s\S+)*?)\n', out)
    corosync_nodes = match_object.group(1).strip().split(' ')
    match_object = search(r'Pacemaker Nodes:\n((?:\s\S+)*?)\n', out)
    pacemaker_nodes = match_object.group(1).strip().split(' ')
    # only include nodes in both corosync_nodes and pacemaker_nodes
    nodes = [node for node in corosync_nodes if node in pacemaker_nodes]


def get_node_status(module, node='all'):
    if node == 'all':
        cmd = "pcs cluster pcsd-status %s" % node
    else:
        cmd = "pcs cluster pcsd-status"
    rc, out, err = module.run_command(cmd)
    if rc == 1:
        module.fail_json(msg="Command execution failed.\nCommand: `%s`\nError: %s" % (cmd, err))
    status = []
    for o in out.splitlines():
        status.append(o.split(':'))
    return status


def clean_cluster(module, timeout):
    cmd = "pcs resource cleanup"
    rc, out, err = module.run_command(cmd)
    if rc == 1:
        module.fail_json(msg="Command execution failed.\nCommand: `%s`\nError: %s" % (cmd, err))


def set_cluster(module, state, timeout, force):
    if state == 'online':
        # if the cluster does not exist, create it
        current_status = get_cluster_status(module)
        if current_status != state:
            rc, out, err = module.run_command("pcs")
        cmd = "pcs cluster start"
    if state == 'offline':
        cmd = "pcs cluster stop"
        if force:
            cmd = "%s --force" % cmd
    rc, out, err = module.run_command(cmd)
    if rc == 1:
        module.fail_json(msg="Command execution failed.\nCommand: `%s`\nError: %s" % (cmd, err))

    t = time.time()
    ready = False
    while time.time() < t + timeout:
        cluster_state = get_cluster_status(module)
        if cluster_state == state:
            ready = True
            break
    if not ready:
        module.fail_json(msg="Failed to set the state `%s` on the cluster\n" % (state))


def set_node(module, state, timeout, force, node='all'):
    # map states
    if state == 'online':
        cmd = "pcs cluster start"
    if state == 'offline':
        cmd = "pcs cluster stop"
        if force:
            cmd = "%s --force" % cmd

    nodes_state = get_node_status(module, node)
    for node in nodes_state:
        if node[1].strip().lower() != state:
            cmd = "%s %s" % (cmd, node[0].strip())
            rc, out, err = module.run_command(cmd)
            if rc == 1:
                module.fail_json(msg="Command execution failed.\nCommand: `%s`\nError: %s" % (cmd, err))

    t = time.time()
    ready = False
    while time.time() < t + timeout:
        nodes_state = get_node_status(module)
        for node in nodes_state:
            if node[1].strip().lower() == state:
                ready = True
                break
    if not ready:
        module.fail_json(msg="Failed to set the state `%s` on the cluster\n" % (state))


def main():
    argument_spec = dict(
        state=dict(type='str', choices=['online', 'offline', 'restart', 'cleanup']),
        node=dict(type='str'),
        timeout=dict(type='int', default=300),
        force=dict(type='bool', default=True),
        members=list(type='bool', elements='str'),
    )

    module = AnsibleModule(
        argument_spec,
        supports_check_mode=True,
    )
    changed = False
    state = module.params['state']
    node = module.params['node']
    force = module.params['force']
    timeout = module.params['timeout']
    members = module.params['members']

    if state in ['online', 'offline']:
        # Get cluster status
        if node is None:
            cluster_state = get_cluster_status(module)
            if cluster_state == state:
                module.exit_json(changed=changed, out=cluster_state)
            else:
                set_cluster(module, state, timeout, members, force)
                cluster_state = get_cluster_status(module)
                if cluster_state == state:
                    module.exit_json(changed=True, out=cluster_state)
                else:
                    module.fail_json(msg="Fail to bring the cluster %s" % state)
        else:
            cluster_state = get_node_status(module, node)
            # Check cluster state
            for node_state in cluster_state:
                if node_state[1].strip().lower() == state:
                    module.exit_json(changed=changed, out=cluster_state)
                else:
                    # Set cluster status if needed
                    set_cluster(module, state, timeout, force)
                    cluster_state = get_node_status(module, node)
                    module.exit_json(changed=True, out=cluster_state)

    if state in ['restart']:
        set_cluster(module, 'offline', timeout, force)
        cluster_state = get_cluster_status(module)
        if cluster_state == 'offline':
            set_cluster(module, 'online', timeout, force)
            cluster_state = get_cluster_status(module)
            if cluster_state == 'online':
                module.exit_json(changed=True, out=cluster_state)
            else:
                module.fail_json(msg="Failed during the restart of the cluster, the cluster can't be started")
        else:
            module.fail_json(msg="Failed during the restart of the cluster, the cluster can't be stopped")

    if state in ['cleanup']:
        clean_cluster(module, timeout)
        cluster_state = get_cluster_status(module)
        module.exit_json(changed=True,
                         out=cluster_state)


if __name__ == '__main__':
    main()
