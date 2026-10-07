"""Interactive DigitalOcean provisioning and GitHub configuration orchestration."""
import json
import re
import shutil

from digitalocean import DigitalOcean, named, latest_version, check_certificate
from setup_environment import (GitHub, ENVIRONMENT, DEFAULT_REPO, ask, hidden,
                               authenticate, save)
from deploy import configuration
from pulumi_setup import prepare, build_spec


def token(label):
    value = hidden(f'{label} access token (required, hidden): ')
    if not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f'{label} token is required and must not contain whitespace')
    return value


def collect(client, defaults):
    clusters = client.list('/kubernetes/clusters', 'kubernetes_clusters')
    domains = client.list('/domains', 'domains')
    certificates = client.list('/certificates', 'certificates')
    previous = next((c for c in clusters if c['id'] == defaults.get('DOKS_CLUSTER_ID')), None)
    cluster_name = ask('Cluster name (existing names are reused)',
                       previous['name'] if previous else defaults.get('DOKS_CLUSTER_NAME', 'apexfission-maven'))
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', cluster_name):
        raise ValueError('Cluster name must use 1-63 lowercase letters, numbers or internal hyphens')
    cluster = named(clusters, cluster_name)
    body = None
    if cluster:
        print(f'Found cluster {cluster["id"]}. Pulumi will import or reconcile its declared configuration.')
    else:
        options = client.request('GET', '/kubernetes/options')['options']
        regions = [item['slug'] for item in options['regions']]
        sizes = [item['slug'] for item in options['sizes']]
        if not regions or not sizes:
            raise RuntimeError('DigitalOcean returned no supported regions or node sizes')
        region = ask('Region', 'nyc1' if 'nyc1' in regions else regions[0])
        size = ask('Worker size', 's-2vcpu-4gb')
        version = ask('Kubernetes version', latest_version(options['versions']))
        count = ask('Number of workers', '1')
        if region not in regions or size not in sizes:
            raise ValueError('Unsupported region or size. Check DigitalOcean Kubernetes options.')
        if version not in [v['slug'] for v in options['versions']]:
            raise ValueError('Choose a currently supported Kubernetes version slug')
        if not count.isascii() or not count.isdigit() or not 1 <= int(count) <= 100:
            raise ValueError('Worker count must be an integer between 1 and 100')
        body = {'name': cluster_name, 'region': region, 'version': version,
                'node_pools': [{'name': 'maven', 'size': size, 'count': int(count)}],
                'ha': False, 'auto_upgrade': True, 'surge_upgrade': True,
                'maintenance_policy': {'day': 'saturday', 'start_time': '06:00'}}
    domain_names = sorted(d['name'] for d in domains)
    suggested_host = 'maven.' + domain_names[0] if domain_names else 'maven.your-domain.com'
    print('Use a hostname you own. New managed certificates require DNS delegated to DigitalOcean.')
    hostname = ask('Maven hostname', defaults.get('REPOSILITE_HOSTNAME', suggested_host)).lower()
    certificate_name = ask('Certificate name', defaults.get('DO_CERTIFICATE_NAME',
                                                           cluster_name + '-tls'))
    # Reuse deployment validation; the real UUID is supplied after provisioning.
    configuration({'DOKS_CLUSTER_ID': '00000000-0000-0000-0000-000000000000',
                   'REPOSILITE_HOSTNAME': hostname, 'DO_CERTIFICATE_NAME': certificate_name})
    if hostname == 'maven.your-domain.com':
        raise ValueError('Replace the suggested placeholder hostname with a domain you own')
    certificate = named(certificates, certificate_name)
    domain = None
    create_domain = False
    if certificate:
        check_certificate(certificate, hostname)
        if certificate.get('state') != 'verified':
            raise ValueError('Existing certificate must be verified before Pulumi import')
        if certificate.get('type') == 'lets_encrypt':
            matching = [d for d in domain_names if hostname == d or hostname.endswith('.' + d)]
            domain = max(matching, key=len) if matching else None
    else:
        matching = [d for d in domain_names if hostname == d or hostname.endswith('.' + d)]
        domain = ask('DNS zone (domain you own)', max(matching, key=len) if matching
                     else hostname.split('.', 1)[1]).lower()
        if '.' not in domain or not (hostname == domain or hostname.endswith('.' + domain)):
            raise ValueError('DNS zone must be the domain containing the Maven hostname')
        create_domain = domain not in domain_names
    return {'cluster': cluster, 'cluster_body': body, 'hostname': hostname,
            'certificate': certificate, 'certificate_name': certificate_name,
            'domain': domain, 'create_domain': create_domain}


def review(repo, plan):
    print(f'\nRepository: {repo}\nGitHub environment: {ENVIRONMENT}')
    cluster = plan['cluster']
    if cluster:
        print(f'Reuse cluster: {cluster["name"]} ({cluster["id"]}), region {cluster["region"]}')
    else:
        body = plan['cluster_body']
        pool = body['node_pools'][0]
        print(f'CREATE BILLABLE cluster: {body["name"]}, region {body["region"]}, '
              f'{pool["count"]} x {pool["size"]}, Kubernetes {body["version"]}')
        print('Control-plane HA: off. Automatic/surge upgrades: on (may add temporary workers).')
        print('Maintenance: Saturday 06:00 UTC. Workers continue billing until deleted.')
        print('Review pricing: https://docs.digitalocean.com/products/kubernetes/details/pricing/')
    print(f'Hostname: {plan["hostname"]}')
    print(f'{"Reuse" if plan["certificate"] else "Create managed"} certificate: '
          f'{plan["certificate_name"]}')
    if not plan['certificate']:
        print(f'{"Create" if plan["create_domain"] else "Reuse"} DNS zone: {plan["domain"]}')
        print('At your registrar, delegate this zone to ns1.digitalocean.com, '
              'ns2.digitalocean.com, ns3.digitalocean.com before certificate issuance.')
        print('Existing DNS records are not migrated. DNS delegation may take time to propagate.')
    print('Save generated cluster UUID, hostname and certificate name to GitHub; '
          'set/replace DIGITALOCEAN_ACCESS_TOKEN (hidden).')
    print('Existing GitHub environment protection rules are preserved.')
    return ask('Prepare a Pulumi preview for these resources? yes/no', 'no').lower() == 'yes'


def provision(client, github, repo, plan, do_token, existing, pulumi_client, previous=None):
    stage = 'Pulumi infrastructure setup'
    try:
        spec = build_spec(plan, previous)
        pulumi_client.verify_ownership(plan)
        values = pulumi_client.apply(spec)
        if values is None:
            return
        print(f'DOKS cluster UUID: {values["DOKS_CLUSTER_ID"]}', flush=True)
        stage = 'GitHub environment setup'
        save(github, repo, values, do_token, existing)
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError(f'Setup stopped during {stage}. Resources and Pulumi state are retained; '
                           'no rollback was attempted. Inspect the selected stack and GitHub settings, '
                           'then rerun with the same backend and stack. ' + str(exc)) from None
    print('\nInfrastructure and GitHub environment configured; variables and secret metadata verified.')
    print(f'GitHub settings: https://github.com/{repo}/settings/environments')
    print('Next: follow docs/setup.md steps 2-3 to install privately and create a persistent admin.')
    print('Then run Actions > Deploy Reposilite on main and point the hostname A record at the LB IP.')
    print('No application deployment or workflow was triggered. See docs/pulumi.md for infrastructure updates.')


def run(args):
    executable = shutil.which('gh')
    if not executable:
        raise RuntimeError('Install GitHub CLI from https://cli.github.com/ and reopen your terminal')
    repo = args.repo or ask('GitHub repository', DEFAULT_REPO)
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('Repository must use owner/repo format on github.com')
    gh_token = None if args.saved_login else token('GitHub')
    github = GitHub(executable, gh_token)
    identity = authenticate(github)
    repository = json.loads(github.run('api', f'repos/{repo}'))
    if not repository.get('permissions', {}).get('admin'):
        raise RuntimeError('An account with repository administrator access is required')
    repo = repository['full_name']
    print(f'Signed in as {identity}. Configuring {repo} / {ENVIRONMENT}.')
    names = github.run('api', f'repos/{repo}/environments', '--paginate',
                       '--jq', '.environments[].name').splitlines()
    existing = any(name.casefold() == ENVIRONMENT.casefold() for name in names)
    defaults = {}
    if existing:
        defaults = {item['name']: item['value'] for item in json.loads(github.run(
            'variable', 'list', '--repo', repo, '--env', ENVIRONMENT, '--json', 'name,value'))}
    do_token = token('DigitalOcean')
    client = DigitalOcean(do_token)
    pulumi_client, previous = prepare(do_token, ask)
    if previous:
        defaults.update({
            'DOKS_CLUSTER_NAME': previous['cluster']['properties']['name'],
            'REPOSILITE_HOSTNAME': previous['hostname'],
            'DO_CERTIFICATE_NAME': previous['certificate']['name'],
        })
        # The selected stack is authoritative even if GitHub points to another cluster.
        defaults.pop('DOKS_CLUSTER_ID', None)
    plan = collect(client, defaults)
    if not review(repo, plan):
        print('Cancelled. No cloud resources or GitHub settings were changed.')
        return
    provision(client, github, repo, plan, do_token, existing, pulumi_client, previous)
