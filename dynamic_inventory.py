#!/usr/bin/env python3

import argparse
import sys
import logging
import os
import json
import yaml
import re
import glob

from ansible.parsing.vault import VaultLib, get_file_vault_secret, is_encrypted_file
from ansible.parsing.yaml.loader import AnsibleLoader
from ansible.parsing.utils.jsonify import jsonify
from ansible.parsing.yaml.objects import AnsibleSequence, AnsibleUnicode
# from ansible.parsing.dataloader import DataLoader


logging.basicConfig(filename='/tmp/hosts.log', level=logging.DEBUG)

parser = argparse.ArgumentParser(description="Dynamic Inventory generator using hosts_var")
parser.add_argument("--list", action="store_true", help="List all hosts")
parser.add_argument("--host", type=str, dest="hostname", help="Get host variables")
parser.add_argument("--list-text", action="store_true", help="Get host and groups as simple text")
parser.add_argument("--vault-id", type=list, dest="vvaults", help="Set Vault")
args = parser.parse_args()

valid_extentions = ( 'yaml', 'yml', 'json', )
possible_main = ( 'main.yml', '000-main.yml', '00-main.yml', '0-main.yml' )
script_dir = os.path.dirname(os.path.realpath(__file__))
hosts_var_dir = 'host_vars'

options = {}

if os.path.isfile("%s/dynamic_inventory.cfg" % script_dir):
    with open("%s/dynamic_inventory.cfg" % script_dir) as f:
        options = json.load(f)

inventory_base = os.path.realpath("%s/../.." % (script_dir))
if "inventory_base" in options:
    if options["inventory_base"].startswith("/"):
        inventory_base = os.path.realpath("%s" % (options["inventory_base"]))
    else:
        inventory_base = os.path.realpath("%s/%s" % (script_dir, options["inventory_base"]))

inventory_path = "%s/%s" % (script_dir, hosts_var_dir)
if "inventory_path" in options:
    if options["inventory_path"].startswith("/"):
        inventory_path = "%s/%s" % (options["inventory_path"], hosts_var_dir)
    else:
        inventory_path = "%s/%s/%s" % (script_dir, options["inventory_path"], hosts_var_dir)

class Inventory:
    def __init__(self):
        self.groups = {}
        self.host_groups = set()
        self.meta = {
            'hostvars': {}
        }

        # self.__init_group(all_group)
        # self.__init_group(ungrouped_group)

    def __init_group(self, group):
        group = self.__normalize_group_name(group)
        self.host_groups.add(group)
        if group not in self.groups:
            self.groups[group] = Group()
            # if group != all_group:
            #     self.add_child(all_group, group)
        return self.groups[group]

    def __normalize_group_name(self, group):
        if 'group_prefix' in options:
            group = "%s_%s" % (options['group_prefix'], group)
        return re.sub(r'[\.,-]', "_", group)

    def add_host(self, group, host):
        self.__init_group(group).add_host(host)
        self.host_groups.add(host)
        # if group != all_group:
        #     self.add_host(all_group, host)

    def add_child(self, group, child):
        child = self.__normalize_group_name(child)
        self.__init_group(group).add_child(child)

    def add_host_vars(self, hostname, vars):
        self.meta['hostvars'][hostname] = vars

    def to_dict(self):
        inv = {
            '_meta': self.meta
        }
        for group, group_data in self.groups.items():
            inv[group] = group_data.to_dict()
        return inv
    
    def to_list(self):
        return list(self.host_groups)

class Group:
    def __init__(self):
        self.children = set()
        self.hosts = set()
        self.vars = {}

    def add_host(self, host):
        self.hosts.add(host)

    def add_child(self, child):
        self.children.add(child)

    def add_var(self, name, value):
        self.vars['name'] = value

    def to_dict(self):
        d = {}
        if len(self.hosts) > 0:
            d['hosts'] = list(self.hosts)
        if len(self.children) > 0:
            d['children'] = list(self.children)
        if len(self.vars) > 0:
            d['vars'] = list(self.vars)
        return d

def load_info(infopath):
    loader='yaml'
    infofiles = []
    infos = []

    if os.path.isfile(infopath):
        infofiles.append(infopath)
    elif os.path.isdir(infopath):
        for f in os.listdir(infopath):
            if os.path.isfile("%s/%s" % (infopath, f)):
                infofiles.append("%s/%s" % (infopath, f))
    for fpath in infofiles:
        if not fpath.endswith(valid_extentions):
            continue
        if not os.path.isfile(fpath):
            continue
        if fpath.endswith('json'):
            loader == 'json'
        with open(fpath, 'rb') as f:
            if is_encrypted_file(f):
                continue
            elif loader == 'yaml':
                infos.append(yaml.load(f, Loader=AnsibleLoader))
            elif loader == 'json':
                infos.append(json.load(f))
    info = dict({})
    for i in infos:
        if i is not None:
            info.update(i)
    return info

def build_grp_path(prefix, path, inventory, get_value):
    groups = []
    if len(path) > 0:
        p = path.pop()
        parents = build_grp_path(prefix, path, inventory, get_value)
        value = get_value(p)
        if value != None:
            vlist = None
            t = type(value)
            if t == list or t == AnsibleSequence:
                vlist = value
            else:
                vlist = [value]
            grp = None
            for v in vlist:
                for parent in parents:
                    grp = "%s_%s" % (parent, v)
                    inventory.add_child(parent, grp)
                if grp != None:
                    groups.append(grp)
        return groups
    else:
        return [prefix]

def get_host_groups(inventory, info):
    groups = set()
    
    if info == None:
        return list(groups)

    group_path = {}
    if 'group_path' in options:
        group_path = options['group_path']
        
    for prefix, value in group_path.items():
        if value == None:
            value = prefix
        p_list = None
        if type(value) is str:
            p_list = [value]
        elif type(value) is list:
            p_list = value
        for v in p_list:
            grp = build_grp_path(prefix, v.split('::'), inventory, lambda p: info[p] if p in info else None)
            for g in grp:
                if g != prefix:
                    groups.add(g)

    return list(groups)

def get_hostnames(hpath, inventory, info):
    hostnames = []

    hostname = os.path.splitext(os.path.basename(hpath))[0]
    
    hostnames.append(hostname)
    if 'fields' not in options:
        return hostnames
    
    if info == None:
        return hostnames
    for field in options['fields']:
        if field in info:
            hn = []
            f = info[field]
            t = type(f)
            if t is str or t is AnsibleUnicode:
                hn.append(f)
            elif t is list or t is AnsibleSequence:
                hn = f
            for h in hn:
                inventory.add_host_vars(h, info)
                hostnames.append(h)
    return hostnames


def add_host(hostnames, groups, inventory):
    for h in hostnames:
        for g in groups:
            inventory.add_host(g, h)

def get_hosts(path, inventory, current_group):
    for p in os.listdir(path):
        hpath = "%s/%s" % (path, p)
        fpath = hpath

        info = load_info(hpath)
        if 'disabled' in info and info['disabled']:
            continue
        hostnames = get_hostnames(fpath, inventory, info)
        if hostnames == None:
            continue

        groups = []
        
        groups = get_host_groups(inventory, info)
        if current_group != None:
            groups.append(current_group)
        add_host(hostnames, groups, inventory)

def print_host_list():
    inventory = Inventory()
    
    get_hosts(inventory_path, inventory, 'inv_' + os.path.basename(inventory_base))

    hosts_string = jsonify(inventory.to_dict())
    logging.info(hosts_string)
    print(hosts_string)

def list_text():
    inventory = Inventory()

    get_hosts(inventory_path, inventory, 'inv_' + os.path.basename(inventory_base))

    hosts_string = jsonify(inventory.to_list())
    print(hosts_string)

def print_host_vars(hostname):
    host_vars = {}
    print(jsonify(host_vars))

logging.debug(sys.argv)
logging.debug(os.environ)

if __name__ == "__main__":
    if not os.path.isdir(inventory_path):
        exit(0)
    if args.list:
        print_host_list()
        exit(0)
    elif args.hostname:
        print_host_vars(args.hostname.trim())
        exit(0)
    elif args.list_text:
        list_text()
        exit(0)
    else:
        parser.print_help()
        exit(-1)