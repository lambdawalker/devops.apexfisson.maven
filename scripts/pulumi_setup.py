"""Pulumi CLI boundary: nonsecret config, explicit stack, reviewed saved plans."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import setup_ui as ui

from deploy import configuration

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'apexfission-maven'
LOCAL = ROOT / '.local' / 'pulumi'


def build_spec(plan, previous=None):
    cluster = plan['cluster'] or plan['cluster_body']
    cloudflare = plan.get('tlsMode') == 'cloudflare'
    if previous:
        if (previous['cluster']['properties']['name'] != cluster['name']
                or previous['hostname'] != plan['hostname']
                or (not cloudflare and previous['certificate']['name'] != plan.get('certificate_name'))):
            raise ui.InputError('Selected resources differ from this Pulumi stack. '
                             'Use its existing names or deliberately edit its configuration first.')
        # Persist declared settings and import policy, not newly observed defaults.
        if cloudflare:
            result = copy.deepcopy(previous)
            result.update(tlsMode='cloudflare', cloudflare=plan['cloudflare'])
            if plan.get('projectId'):
                result.update(projectId=plan['projectId'], projectName=plan['projectName'])
            # A failed certificate absent from provider/state must never be recreated.
            if not plan.get('certificate'):
                result['certificate'] = None
            return result
        return previous
    pool = cluster['node_pools'][0]
    properties = {'name': cluster['name'], 'region': cluster['region'],
                  'version': cluster['version'], 'ha': cluster.get('ha', False),
                  'autoUpgrade': cluster.get('auto_upgrade', True),
                  'surgeUpgrade': cluster.get('surge_upgrade', True),
                  'nodePool': {'name': pool['name'], 'size': pool['size'],
                               'nodeCount': pool['count']}}
    if cluster.get('vpc_uuid'):
        properties['vpcUuid'] = cluster['vpc_uuid']
    cert = plan.get('certificate')
    if cert and cert.get('state') != 'verified':
        raise ui.InputError('Existing certificate must be verified before importing; finish DNS issuance first')
    external = bool(cert and cert.get('type') == 'custom')
    result = {
        'hostname': plan['hostname'],
        'domain': ({'name': plan['domain'],
                    'importId': None if plan['create_domain'] else plan['domain'],
                    'preserveImported': not plan['create_domain']} if plan['domain'] else None),
        'certificate': {'name': plan.get('certificate_name'),
                        'mode': 'external' if external else 'managed',
                        'importId': cert['name'] if cert and not external else None,
                        'preserveImported': bool(cert),
                        'domains': cert['dns_names'] if cert else [plan['hostname']]},
        'cluster': {'importId': cluster['id'] if plan['cluster'] else None,
                    'preserveImported': bool(plan['cluster']), 'properties': properties},
    }
    if cloudflare:
        result.update(tlsMode='cloudflare', cloudflare=plan['cloudflare'], domain=None, certificate=None)
    if plan.get('projectId'):
        result.update(projectId=plan['projectId'], projectName=plan['projectName'])
    return result


def preview_changes(output, managed_urns=()):
    summary = None
    allowed = {'same', 'create', 'update', 'import', 'read', 'read-discard', 'refresh'}
    for line in output.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        metadata = event.get('resourcePreEvent', {}).get('metadata', {})
        op = metadata.get('op')
        if op in ('create', 'import') and metadata.get('urn') in managed_urns:
            raise ui.SetupError('Previously managed resource is missing; refusing to recreate/adopt it. Inspect the selected stack and cluster before rerunning.')
        if op and op not in allowed:
            raise ui.SetupError(f'Wizard refuses Pulumi operation: {op}. No update was applied.')
        if op and op != 'same':
            ui.say(f'  {op}: {metadata.get("urn", "resource").split("::")[-1]}')
        if 'summaryEvent' in event:
            summary = event['summaryEvent']['resourceChanges']
    if summary is None:
        raise ui.SetupError('Pulumi preview did not return a summary; refusing to apply')
    if any(op not in allowed and count for op, count in summary.items()):
        raise ui.SetupError('Pulumi preview contains a destructive operation; refusing to apply')
    return summary


class Pulumi:
    def __init__(self, executable, token, backend):
        parts = urlsplit(backend)
        if parts.scheme not in ('https', 's3', 'gs', 'azblob', 'file') or parts.username or parts.password or parts.query or parts.fragment:
            raise ui.InputError('Use a Pulumi Cloud HTTPS URL or a supported DIY backend without embedded credentials')
        self.executable = executable
        self.env = dict(os.environ, DIGITALOCEAN_TOKEN=token, PULUMI_BACKEND_URL=backend,
                        PULUMI_PYTHON_CMD=sys.executable, PULUMI_SKIP_UPDATE_CHECK='true',
                        PULUMI_ENABLE_STREAMING_JSON_PREVIEW='true')
        for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_DEBUG', 'TF_LOG', 'TF_LOG_PATH'):
            self.env.pop(key, None)
        self.stack = None
        self.config_file = None
        self.managed_urns = set()

    def login(self):
        ui.say('Sign in to the selected Pulumi backend. Follow the CLI browser/token prompts.', flush=True)
        ui.say('An existing valid login can be reused; Pulumi manages its local credentials.', flush=True)
        env = dict(self.env)
        env.pop('DIGITALOCEAN_TOKEN', None)
        env.pop('CLOUDFLARE_API_TOKEN', None)
        try:
            # Login needs the real terminal for browser/device prompts and hidden input.
            # Never route it through run(), which captures output and disables interaction.
            result = ui.terminal(lambda: subprocess.run(
                [self.executable, 'login', self.env['PULUMI_BACKEND_URL']],
                cwd=ROOT / 'infrastructure', env=env, timeout=900,
            ))
        except subprocess.TimeoutExpired:
            raise ui.SetupError('Pulumi login timed out. Rerun setup to try signing in again.') from None
        if result.returncode:
            raise ui.SetupError('Pulumi login failed or was cancelled. Resolve the login message above '
                               'and rerun setup; stack selection has not started.')

    def run(self, *args):
        ui.check_cancelled()
        command = [self.executable, *args, '--non-interactive', '--color', 'never']
        if self.stack and args[0] in ('config', 'preview', 'up'):
            command += ['--stack', self.stack]
        if self.config_file and args[0] in ('config', 'preview', 'up'):
            command += ['--config-file', str(self.config_file)]
        try:
            result = ui.run_command(command, cwd=ROOT / 'infrastructure', env=self.env,
                                    private_stdout=(args[:2] in (('stack', 'export'), ('stack', 'output')) or args[0] == 'config'),
                                    preview_json=(args[0] == 'preview' and '--json' in args),
                                    text=True, encoding='utf-8', capture_output=True, timeout=2700)
        except subprocess.TimeoutExpired:
            raise ui.SetupError('Pulumi timed out; inspect the stack for an interrupted update before rerunning') from None
        if result.returncode:
            # The command boundary streams redacted diagnostics; keep this exception safe.
            raise ui.SetupError(f'Pulumi {" ".join(args[:2]) if args[0] in ("stack", "org", "config") else args[0]} failed (exit {result.returncode}). '
                               'Check Pulumi login, backend access, token scopes and stack update history. '
                               'Existing resources/state were retained; no rollback was attempted. ' +
                               (f'Inspect https://app.pulumi.com/{self.stack}' if self.stack and self.env['PULUMI_BACKEND_URL'].startswith('https://') else 'Inspect the selected backend stack.'))
        return result.stdout

    def select(self, ask):
        identity = json.loads(self.run('whoami', '--json'))
        backend = self.env['PULUMI_BACKEND_URL']
        cloud = backend.startswith('https://')
        organizations = identity.get('organizations', [])
        organizations = sorted({x['name'] if isinstance(x, dict) else x for x in organizations})
        if cloud:
            if not organizations:
                raise ui.InputError('Pulumi returned no organization memberships; join an organization before selecting a cloud stack')
            preferred = None
            try:
                preferred = self.run('org', 'get-default').strip()
            except RuntimeError:
                pass
            organization = preferred if preferred in organizations else organizations[0]
            if not ui.supports_forms():
                organization = ask('Pulumi organization (' + ', '.join(organizations) + ')', organization)
            if organization not in organizations:
                raise ui.InputError('Choose a Pulumi organization from your actual memberships')
            default = f'{organization}/{PROJECT}/production'
        else:
            default = 'production'
        if ui.supports_forms():
            from setup_forms import stack
            self.stack = stack(organizations if cloud else [], preferred if cloud else None, PROJECT)
        else:
            self.stack = ask('Pulumi stack', default)
        if not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,2}', self.stack):
            raise ui.InputError('Invalid Pulumi stack name')
        if cloud and (len(self.stack.split('/')) != 3 or self.stack.split('/')[1] != PROJECT):
            raise ui.InputError(f'Use OWNER/{PROJECT}/STACK for Pulumi Cloud')
        if cloud and self.stack.split('/')[0] not in organizations:
            raise ui.InputError('Stack owner must be one of your Pulumi organization memberships')
        LOCAL.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256((backend + '/' + self.stack).encode()).hexdigest()[:20]
        self.config_file = LOCAL / (name + '.yaml')
        ui.say(f'Pulumi backend: {backend}\nPulumi stack: {self.stack}')
        self.run('stack', 'select', self.stack, '--create')
        if not self.config_file.exists():
            state = json.loads(self.run('stack', 'export', '--stack', self.stack))
            if state.get('deployment', {}).get('resources'):
                self.run('config', 'refresh', '--force')
        configs = json.loads(self.run('config', '--json'))
        entry = configs.get(PROJECT + ':infrastructure')
        previous = json.loads(entry['value']) if entry else None
        if previous and previous.get('cloudflare') and not previous['cloudflare'].get('recordId'):
            state = json.loads(self.run('stack', 'export', '--stack', self.stack))
            records = [r for r in state.get('deployment', {}).get('resources', [])
                       if r.get('type') == 'cloudflare:index/dnsRecord:DnsRecord' and r.get('id')]
            if len(records) > 1:
                raise ui.InputError('Stack has ambiguous Cloudflare DNS ownership')
            if records:
                previous['cloudflare']['recordId'] = records[0]['id'].split('/')[-1]
        return previous

    def verify_ownership(self, plan):
        state = json.loads(self.run('stack', 'export', '--stack', self.stack))
        resources = state.get('deployment', {}).get('resources', [])
        record_id = plan.get('cloudflare', {}).get('recordId')
        if record_id and not any(item.get('custom') and not item.get('external')
                                 and item.get('type') == 'cloudflare:index/dnsRecord:DnsRecord'
                                 and item.get('id') in (record_id, plan['cloudflare']['zoneId'] + '/' + record_id)
                                 for item in resources):
            raise ui.InputError('Cloudflare A record is not owned by this stack; choose an unused hostname or review its migration separately')
        self.managed_urns = {item['urn'] for item in resources
                             if item.get('custom') and not item.get('external')
                             and item.get('id') and item.get('urn')}
        for item in resources:
            if not item.get('custom') or item.get('external'):
                continue
            kind = item['type']
            actual = None
            if kind == 'digitalocean:index/kubernetesCluster:KubernetesCluster':
                actual = (plan['cluster'] or {}).get('id')
            elif kind == 'digitalocean:index/certificate:Certificate':
                actual = (plan['certificate'] or {}).get('name')
            elif kind == 'cloudflare:index/dnsRecord:DnsRecord':
                settings = plan.get('cloudflare', {})
                record = settings.get('recordId')
                actual = settings.get('zoneId', '') + '/' + record if record else None
                if record and item.get('id') == record:
                    actual = record
            elif kind == 'digitalocean:index/domain:Domain':
                actual = None if plan['create_domain'] else plan['domain']
            else:
                continue
            if item.get('id') and actual != item['id']:
                raise ui.InputError('Resource discovery differs from Pulumi state. '
                                 'Inspect the selected stack and DO account; refusing to adopt/recreate resources.')

    def apply(self, spec, directory=LOCAL):
        directory.mkdir(parents=True, exist_ok=True)
        self.run('config', 'set', PROJECT + ':infrastructure', json.dumps(spec))
        ui.say('Pulumi desired configuration (no credentials):')
        ui.say(json.dumps(spec, indent=2))
        cluster_urn = (f'urn:pulumi:{self.stack.split("/")[-1]}::{PROJECT}::'
                       'digitalocean:index/kubernetesCluster:KubernetesCluster::cluster')
        if spec.get('tlsMode') == 'cloudflare' and cluster_urn not in self.managed_urns:
            # An unknown kubeconfig prevents Helm input normalization during the
            # first preview. Materialize only the cluster/dependencies, then
            # preview the complete program against the real provider config.
            kinds = [('digitalocean:index/kubernetesCluster:KubernetesCluster', 'cluster')]
            if spec.get('domain'):
                kinds.append(('digitalocean:index/domain:Domain', 'domain'))
            if spec.get('certificate') and spec['certificate']['mode'] != 'external':
                kinds.append(('digitalocean:index/certificate:Certificate', 'certificate'))
            if spec.get('projectId'):
                kinds.append(('digitalocean:index/projectResources:ProjectResources', 'cluster-project'))
            targets = tuple(value for kind, name in kinds for value in (
                '--target', f'urn:pulumi:{self.stack.split("/")[-1]}::{PROJECT}::{kind}::{name}'))
            ui.say('First create/import the cluster with a reviewed plan. Helm and DNS get a second preview after the cluster is ready.')
            if not self.apply_plan(directory, targets, 'cluster bootstrap plan'):
                return None
            state = json.loads(self.run('stack', 'export', '--stack', self.stack))
            resources = state.get('deployment', {}).get('resources', [])
            self.managed_urns.update(r['urn'] for r in resources
                                    if r.get('custom') and not r.get('external') and r.get('id') and r.get('urn'))
            if cluster_urn not in self.managed_urns:
                raise ui.SetupError('Cluster bootstrap did not record a cluster ID; refusing the next stage.')
        if not self.apply_plan(directory, (), 'infrastructure plan'):
            return None
        outputs = json.loads(self.run('stack', 'output', '--json', '--stack', self.stack))
        values = {'DOKS_CLUSTER_ID': outputs['clusterId'], 'REPOSILITE_HOSTNAME': outputs['hostname']}
        if outputs.get('tlsMode') == 'cloudflare':
            values['TLS_MODE'] = 'cloudflare'
        else:
            values['DO_CERTIFICATE_NAME'] = outputs['certificateName']
        return configuration(values)

    def apply_plan(self, directory, targets, label):
        # Unique ignored plan file; never reuse a stale plan after failure/cancellation.
        with tempfile.TemporaryDirectory(prefix='preview-', dir=directory) as temp:
            saved = str(Path(temp) / 'plan.json')
            ui.stage('preview')
            ui.say('Running Pulumi preview ...', flush=True)
            summary = preview_changes(self.run('preview', *targets, '--json', '--refresh',
                                                '--save-plan', saved, '--suppress-outputs'), self.managed_urns)
            ui.say('Changes: ' + ', '.join(f'{op}={count}' for op, count in sorted(summary.items())))
            approved = (ui.confirm_action('Apply ' + label,
                        'Changes: ' + ', '.join(f'{op}={count}' for op, count in sorted(summary.items())) +
                        '\nThis applies the reviewed ' + label + ', and may create billable resources.', 'Apply')
                        if ui.supports_forms() else
                        ui.ask('Apply ' + label + '? yes/no', 'no').lower() == 'yes')
            if not approved:
                ui.cancelled('Cancelled. Pulumi configuration is saved locally; this plan was not applied; earlier completed stages are retained.')
                return False
            ui.stage('provision')
            ui.say('Applying reviewed plan; certificate/cluster creation can take several minutes ...', flush=True)
            self.run('up', *targets, '--yes', '--skip-preview', '--plan', saved, '--suppress-outputs')
        return True



def prepare(token, ask, backend=None):
    executable = shutil.which('pulumi')
    if not executable:
        raise ui.SetupError('Install Pulumi CLI from https://www.pulumi.com/docs/install/ and rerun setup; login is guided.')
    if any(importlib.util.find_spec(name) is None for name in ('pulumi', 'pulumi_digitalocean', 'pulumi_kubernetes', 'pulumi_cloudflare')):
        raise ui.SetupError('Run setup with uv run python scripts/setup_environment.py to install the project dependencies.')
    backend = backend or ask('Pulumi state backend', os.environ.get('PULUMI_BACKEND_URL', 'https://api.pulumi.com'))
    client = Pulumi(executable, token, backend)
    client.login()
    previous = client.select(ask)
    return client, previous
