"""Provision/reuse DigitalOcean and test Reposilite by public IPv4, without DNS."""
import json
import re
import time

import setup_ui as ui
from digitalocean import DigitalOcean, latest_version, named
from independent_setup import (Api, approve, cluster_access, credentials, fields, launch,
                               load_settings, require, save_settings)
from digitalocean_stages import gateway, gateway_ip, ingress, apply_objects, private_bootstrap, check_http


def execute(args):
    ui.stage('credentials')
    require('doctl', 'kubectl', 'helm')
    token = credentials(args, 'digitalocean')
    read = DigitalOcean(token)
    api = Api('digitalocean', token)
    ui.stage('discovery')
    saved = load_settings()
    clusters = read.list('/kubernetes/clusters', 'kubernetes_clusters')
    options = read.request('GET', '/kubernetes/options')['options']
    projects = read.list('/projects', 'projects')
    if not projects:
        raise ui.InputError('Create a DigitalOcean project first.')
    values = fields('DigitalOcean infrastructure', [
        ui.Field('name', 'Cluster name (an existing name is reused)', saved.get('cluster_name', 'apexfission-maven')),
        ui.Field('region', 'Region for a new cluster', 'nyc1', choices=tuple(x['slug'] for x in options['regions'])),
        ui.Field('size', 'Worker size for a new cluster', 's-2vcpu-4gb', choices=tuple(x['slug'] for x in options['sizes'])),
        ui.Field('version', 'Kubernetes version for a new cluster', latest_version(options['versions']),
                 choices=tuple(x['slug'] for x in options['versions'])),
        ui.Field('project', 'Project ID for a new cluster', next((p['id'] for p in projects if p.get('is_default')), projects[0]['id']),
                 choices=tuple(p['id'] for p in projects))],
        validate=lambda v: {} if re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', v['name'])
        else {'name': 'Use a lowercase cluster name (1–63 characters).'},
        description='One worker, HA off. Existing cluster settings stay unchanged.\n' +
                    '\n'.join(p['id'] + ': ' + p['name'] for p in projects))
    cluster = named(clusters, values['name'])
    if saved.get('cluster_id') and (not cluster or cluster['id'] != saved['cluster_id']):
        raise ui.InputError('Selected cluster differs from saved setup outputs. Review .local/independent-setup.json before changing targets.')
    summary = (f'Reuse cluster {cluster["id"]}.' if cluster else
               f'Create billable cluster {values["name"]}: {values["region"]}, 1 × {values["size"]}.')
    if not approve('Configure DigitalOcean and IP test', summary + '\nInstall Reposilite, a retained 20 GiB volume and a billable TCP load balancer.\n'
                   'Administrator bootstrap stays on localhost. Public IP test uses HTTP; use it for anonymous browsing only.\n'
                   'No DNS, Cloudflare, GitHub or Pulumi operations. Stop using the old combined wizard for this deployment.'):
        return
    ui.stage('apply')
    if cluster is None:
        # Recheck before creation. A timed-out POST is recovered by exact name on rerun.
        cluster = named(read.list('/kubernetes/clusters', 'kubernetes_clusters'), values['name'])
        if cluster is None:
            cluster = api.request('POST', '/kubernetes/clusters', {
                'name': values['name'], 'region': values['region'], 'version': values['version'],
                'node_pools': [{'name': 'maven', 'size': values['size'], 'count': 1}],
                'ha': False, 'auto_upgrade': True, 'surge_upgrade': True,
                'maintenance_policy': {'day': 'saturday', 'start_time': '06:00'}})['kubernetes_cluster']
            save_settings(cluster_id=cluster['id'], cluster_name=cluster['name'], pending_project=values['project'])
    save_settings(cluster_id=cluster['id'], cluster_name=cluster['name'])
    pending_project = load_settings().get('pending_project')
    if pending_project:
        api.request('POST', '/projects/' + pending_project + '/resources',
                    {'resources': ['do:kubernetes:' + cluster['id']]})
        members = read.list('/projects/' + pending_project + '/resources', 'resources')
        if not any(r['urn'] == 'do:kubernetes:' + cluster['id'] for r in members):
            raise ui.SetupError('Project assignment is not yet verified; rerun this stage.')
        save_settings(pending_project='')
    read.wait('/kubernetes/clusters/' + cluster['id'], 'kubernetes_cluster')
    with cluster_access(token, cluster['id']) as (kubectl, env):
        # Never downgrade a hostname/TLS deployment back to a hostless HTTP route.
        if kubectl('-n', 'reposilite', 'get', 'ingress', 'reposilite', '--ignore-not-found', '-o', 'name').strip():
            raise ui.InputError('Hostname routing already exists. Use setup_digitalocean_hostname.py; IP setup will not downgrade it.')
        private_bootstrap(kubectl, env)
        gateway(env)
        ip = gateway_ip(kubectl, api)
        save_settings(ip=ip)
        apply_objects(kubectl, [ingress('reposilite-ip')])
    ui.stage('verify')
    check_http('http://' + ip)
    ui.say(f'IP test: http://{ip}\nNext, run setup_cloudflare.py when you are ready.')


if __name__ == '__main__':
    raise SystemExit(launch('digitalocean', execute))
