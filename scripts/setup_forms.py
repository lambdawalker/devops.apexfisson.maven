"""Grouped setup fields and local validation; provider mutations stay in the wizard."""
from dataclasses import replace
import os
import re
import uuid
from urllib.parse import urlsplit

import setup_ui as ui
from deploy import configuration


TOKEN_ERROR = 'Enter a single token without whitespace or control characters.'


def valid_token(value):
    return bool(value) and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)


def validate_credentials(values):
    errors = {}
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', values['repo']):
        errors['repo'] = 'Use owner/repository format.'
    for key in ('github', 'digitalocean', 'cloudflare'):
        if values.get(key) and not valid_token(values[key]):
            errors[key] = TOKEN_ERROR
    if 'backend' in values:
        try:
            parts = urlsplit(values['backend'])
            if parts.scheme not in ('https', 's3', 'gs', 'azblob', 'file') or parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError()
            if (parts.scheme == 'file' and not parts.path) or (parts.scheme != 'file' and not parts.netloc):
                raise ValueError()
        except ValueError:
            errors['backend'] = 'Use a Pulumi backend URL without embedded credentials.'
    return errors


def credentials(args, default_repo):
    fields = [ui.Field('repo', 'GitHub repository', args.repo or default_repo)]
    if not args.saved_login:
        fields.append(ui.Field('github', 'GitHub access token', args.tokens.get('github', ''), secret=True,
                               required=not args.github_only or args.token_auth,
                               help='Repository administrator token. Leave blank to use saved gh login in GitHub-only mode.'))
    if not args.github_only:
        fields.extend([
            ui.Field('digitalocean', 'DigitalOcean access token', args.tokens.get('digitalocean', ''), secret=True),
            ui.Field('cloudflare', 'Cloudflare API token', args.tokens.get('cloudflare', ''), secret=True,
                     help='Zone DNS Edit and Zone Read for your zone.'),
            ui.Field('backend', 'Pulumi backend', os.environ.get('PULUMI_BACKEND_URL', 'https://api.pulumi.com')),
            ui.Field('dns', 'Is your domain managed by Cloudflare?', 'yes', choices=('yes', 'no')),
        ])
        next(f for f in fields if f.key == 'cloudflare').enabled_when = lambda v: v['dns'] == 'yes'
    values = ui.form('Credentials and repository', fields,
                     description='Review all fields, then Proceed. Tokens remain masked. ' +
                     ('Using saved GitHub CLI login. ' if args.saved_login else '') +
                     'Pulumi login will temporarily use the terminal.', validate=validate_credentials)
    args.repo = values['repo']
    args.tokens.update({k: values[k] for k in ('github', 'digitalocean', 'cloudflare') if k in values})
    args.pulumi_backend = values.get('backend')
    if values.get('dns') == 'no':
        ui.cancelled('Cloudflare DNS is required for provisioning. Use --github-only for existing infrastructure.')
        return False
    return True


def stack(organizations, preferred, project):
    fields = []
    if organizations:
        fields.append(ui.Field('organization', 'Pulumi organization',
                               preferred if preferred in organizations else organizations[0],
                               choices=tuple(organizations)))
    fields.append(ui.Field('stack', 'Stack name', 'production',
                           help=f'Project: {project}. Reuse the existing stack name on reruns.'))
    def validate(values):
        pattern = r'[A-Za-z0-9_.-]+' if organizations else r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,2}'
        return {} if re.fullmatch(pattern, values['stack']) else {'stack': 'Enter a valid stack name.'}
    values = ui.form('Pulumi stack', fields, validate=validate,
                     description='Select the stack whose state owns this infrastructure.')
    return f'{values["organization"]}/{project}/{values["stack"]}' if organizations else values['stack']


def validate_hostname(value):
    if value != value.strip():
        return 'Remove whitespace around the hostname.'
    try:
        configuration({'TLS_MODE': 'cloudflare', 'DOKS_CLUSTER_ID': str(uuid.UUID(int=0)),
                       'REPOSILITE_HOSTNAME': value})
    except ValueError:
        return 'Enter a DNS hostname without a scheme, port or path.'
    return None


def validate_infrastructure(values, *, clusters, projects, zones, regions, sizes, versions, previous=None):
    errors = {}
    name = values['cluster']
    matches = [c for c in clusters if c['name'] == name]
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', name):
        errors['cluster'] = 'Use 1–63 lowercase letters, numbers or internal hyphens.'
    elif len(matches) > 1:
        errors['cluster'] = 'Multiple clusters have this name; resolve the ambiguity first.'
    if previous and name != previous['cluster']['properties']['name']:
        errors['cluster'] = 'Reuse the cluster declared by this stack.'
    if not matches:
        for key, options in [('region', regions), ('size', sizes), ('version', versions)]:
            if values[key] not in options:
                errors[key] = 'Choose a currently supported value.'
        count = values['count']
        if not count.isascii() or not count.isdigit() or len(count) > 3 or not 1 <= int(count) <= 100:
            errors['count'] = 'Enter a whole number from 1 to 100.'
    if values['project'] not in [p['id'] for p in projects] or (previous and previous.get('projectId') and values['project'] != previous['projectId']):
        errors['project'] = 'Choose the project that owns this stack.'
    hostname = values['hostname'].lower()
    error = validate_hostname(hostname)
    if error:
        errors['hostname'] = error
    if previous and hostname != previous['hostname']:
        errors['hostname'] = 'Reuse the hostname declared by this stack.'
    zone = next((z for z in zones if z['name'] == values['zone']), None)
    if not zone or not (hostname == zone['name'] or hostname.endswith('.' + zone['name'])):
        errors['zone'] = 'Choose an active zone containing this hostname.'
    elif previous and previous.get('cloudflare') and zone['id'] != previous['cloudflare']['zoneId']:
        errors['zone'] = 'Reuse the Cloudflare zone declared by this stack.'
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']):
        errors['email'] = 'Enter a monitored email address for certificate notices.'
    return errors


def infrastructure(client, defaults, cloudflare, previous=None):
    from digitalocean import latest_version
    from setup_cloud import collect, choose_project
    clusters = client.list('/kubernetes/clusters', 'kubernetes_clusters')
    projects = client.list('/projects', 'projects')
    zones = cloudflare.list('/zones', status='active')
    if not projects or not zones:
        raise ui.InputError('Setup needs an accessible DigitalOcean project and an active Cloudflare zone.')
    github_cluster = next((c for c in clusters if c['id'] == defaults.get('DOKS_CLUSTER_ID')), None)
    name = github_cluster['name'] if github_cluster else defaults.get('DOKS_CLUSTER_NAME', 'apexfission-maven')
    current = next((c for c in clusters if c['name'] == name), None)
    # Discovery only; no resources are created to populate choices.
    options = client.request('GET', '/kubernetes/options')['options']
    regions = [r['slug'] for r in options['regions']]
    sizes = [r['slug'] for r in options['sizes']]
    versions = [r['slug'] for r in options['versions']]
    if not regions or not sizes or not versions:
        raise ui.InputError('DigitalOcean returned no supported cluster options.')
    old_cf = (previous or {}).get('cloudflare') or {}
    zone_name = old_cf.get('zoneName') or sorted(z['name'] for z in zones)[0]
    hostname = defaults.get('REPOSILITE_HOSTNAME', 'maven.' + zone_name)
    matches = [z['name'] for z in zones if hostname == z['name'] or hostname.endswith('.' + z['name'])]
    if matches and not old_cf:
        zone_name = max(matches, key=len)
    project = (previous or {}).get('projectId') or next((p['id'] for p in projects if p.get('is_default')), projects[0]['id'])
    declared = (previous or {}).get('cluster', {}).get('properties', {})
    pool = ({'size': declared['nodePool']['size'], 'count': declared['nodePool']['nodeCount']}
            if declared else current['node_pools'][0] if current else {})
    region = declared.get('region') or (current['region'] if current else ('nyc1' if 'nyc1' in regions else regions[0]))
    size = pool.get('size', 's-2vcpu-4gb' if 's-2vcpu-4gb' in sizes else sizes[0])
    version = declared.get('version') or (current['version'] if current else latest_version(options['versions']))
    new_cluster = lambda v: v['cluster'] not in [c['name'] for c in clusters]
    fields = [
        ui.Field('cluster', 'Cluster name', name, help='Existing: ' + (', '.join(c['name'] for c in clusters) or 'none') + '. Existing cluster settings are preserved.'),
        ui.Field('project', 'DigitalOcean project ID', project, choices=tuple(p['id'] for p in projects),
                 help='; '.join(p['name'] + ': ' + p['id'] for p in projects)),
        ui.Field('region', 'Region · new clusters only', region, choices=tuple(dict.fromkeys([region, *regions])), enabled_when=new_cluster),
        ui.Field('size', 'Worker size · new clusters only', size, choices=tuple(dict.fromkeys([size, *sizes])), enabled_when=new_cluster),
        ui.Field('version', 'Kubernetes version · new clusters only', version, choices=tuple(dict.fromkeys([version, *versions])), enabled_when=new_cluster),
        ui.Field('count', 'Worker count · new clusters only', str(pool.get('count', 1)), enabled_when=new_cluster),
        ui.Field('hostname', 'Maven hostname', hostname),
        ui.Field('zone', 'Cloudflare zone', zone_name, choices=tuple(z['name'] for z in zones)),
        ui.Field('email', 'ACME account email', old_cf.get('acmeEmail', 'hostmaster@' + zone_name)),
    ]
    def validate(values):
        return validate_infrastructure(values, clusters=clusters, projects=projects, zones=zones,
                                       regions=regions, sizes=sizes, versions=versions, previous=previous)
    errors = {}
    while True:
        values = ui.form('Infrastructure and DNS', fields, validate=validate, errors=errors,
                         description='Defaults come from the selected stack, GitHub and provider discovery. Proceed checks configuration and DNS; it does not apply the infrastructure plan.')
        answers = {
            'Cluster name (existing names are reused)': values['cluster'], 'Region': values['region'],
            'Worker size': values['size'], 'Kubernetes version': values['version'],
            'Number of workers': values['count'], 'Maven hostname': values['hostname'],
            'ACME account email': values['email'],
        }
        def prompt(label, default=''):
            return values['zone'] if label.startswith('Cloudflare zone (') else answers[label]
        try:
            plan = collect(client, defaults, cloudflare, previous, prompt=prompt)
            plan.update(choose_project(client, previous, selection=values['project']))
            return plan
        except ui.InputError as error:
            # Read-only discovery errors can be corrected without losing edits.
            fields = [replace(field, default=values[field.key]) for field in fields]
            errors = {'__form__': str(error)}


def github_environment(defaults, tokens, secret_exists):
    fields = [
        ui.Field('mode', 'TLS mode', defaults.get('TLS_MODE', 'cloudflare'), choices=('cloudflare', 'digitalocean')),
        ui.Field('cluster', 'DOKS cluster UUID', defaults.get('DOKS_CLUSTER_ID', '')),
        ui.Field('hostname', 'Maven hostname', defaults.get('REPOSILITE_HOSTNAME', '')),
        ui.Field('certificate', 'DigitalOcean certificate name', defaults.get('DO_CERTIFICATE_NAME', ''),
                 enabled_when=lambda v: v['mode'] == 'digitalocean'),
        ui.Field('token', 'DigitalOcean API token', tokens.get('digitalocean', ''), secret=True,
                 required=not secret_exists, help='Leave blank to keep the existing GitHub secret.' if secret_exists else 'Required for this environment.'),
    ]
    def validate(values):
        errors = {}
        try:
            uuid.UUID(values['cluster'])
        except ValueError:
            errors['cluster'] = 'Enter the cluster UUID, not its name.'
        error = validate_hostname(values['hostname'])
        if error:
            errors['hostname'] = error
        if values['token'] and not valid_token(values['token']):
            errors['token'] = TOKEN_ERROR
        if values['mode'] == 'digitalocean' and any(ord(c) < 32 or ord(c) == 127 for c in values['certificate']):
            errors['certificate'] = 'Enter a single-line certificate name.'
        return errors
    values = ui.form('GitHub environment', fields, validate=validate)
    return configuration({'TLS_MODE': values['mode'], 'DOKS_CLUSTER_ID': values['cluster'],
                          'REPOSILITE_HOSTNAME': values['hostname'], 'DO_CERTIFICATE_NAME': values['certificate']}), values['token']
