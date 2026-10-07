"""Pulumi CLI boundary: nonsecret config, explicit stack, reviewed saved plans."""
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

from deploy import configuration

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'apexfission-maven'
LOCAL = ROOT / '.local' / 'pulumi'


def build_spec(plan, previous=None):
    cluster = plan['cluster'] or plan['cluster_body']
    if previous:
        if (previous['cluster']['properties']['name'] != cluster['name']
                or previous['hostname'] != plan['hostname']
                or previous['certificate']['name'] != plan['certificate_name']):
            raise ValueError('Selected resources differ from this Pulumi stack. '
                             'Use its existing names or deliberately edit its configuration first.')
        # Persist declared settings and import policy, not newly observed defaults.
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
    cert = plan['certificate']
    if cert and cert.get('state') != 'verified':
        raise ValueError('Existing certificate must be verified before importing; finish DNS issuance first')
    external = bool(cert and cert.get('type') == 'custom')
    return {
        'hostname': plan['hostname'],
        'domain': ({'name': plan['domain'],
                    'importId': None if plan['create_domain'] else plan['domain'],
                    'preserveImported': not plan['create_domain']} if plan['domain'] else None),
        'certificate': {'name': plan['certificate_name'],
                        'mode': 'external' if external else 'managed',
                        'importId': cert['name'] if cert and not external else None,
                        'preserveImported': bool(cert),
                        'domains': cert['dns_names'] if cert else [plan['hostname']]},
        'cluster': {'importId': cluster['id'] if plan['cluster'] else None,
                    'preserveImported': bool(plan['cluster']), 'properties': properties},
    }


def preview_changes(output):
    summary = None
    allowed = {'same', 'create', 'update', 'import', 'read', 'read-discard', 'refresh'}
    for line in output.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        metadata = event.get('resourcePreEvent', {}).get('metadata', {})
        op = metadata.get('op')
        if op and op not in allowed:
            raise RuntimeError(f'Wizard refuses Pulumi operation: {op}. No update was applied.')
        if op and op != 'same':
            print(f'  {op}: {metadata.get("urn", "resource").split("::")[-1]}')
        if 'summaryEvent' in event:
            summary = event['summaryEvent']['resourceChanges']
    if summary is None:
        raise RuntimeError('Pulumi preview did not return a summary; refusing to apply')
    if any(op not in allowed and count for op, count in summary.items()):
        raise RuntimeError('Pulumi preview contains a destructive operation; refusing to apply')
    return summary


class Pulumi:
    def __init__(self, executable, token, backend):
        parts = urlsplit(backend)
        if parts.scheme not in ('https', 's3', 'gs', 'azblob', 'file') or parts.username or parts.password:
            raise ValueError('Use a Pulumi Cloud HTTPS URL or a supported DIY backend without embedded credentials')
        self.executable = executable
        self.env = dict(os.environ, DIGITALOCEAN_TOKEN=token, PULUMI_BACKEND_URL=backend,
                        PULUMI_PYTHON_CMD=sys.executable, PULUMI_SKIP_UPDATE_CHECK='true',
                        PULUMI_ENABLE_STREAMING_JSON_PREVIEW='true')
        for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_DEBUG', 'TF_LOG', 'TF_LOG_PATH'):
            self.env.pop(key, None)
        self.stack = None
        self.config_file = None

    def run(self, *args):
        command = [self.executable, *args, '--non-interactive', '--color', 'never']
        if self.stack and args[0] in ('config', 'preview', 'up'):
            command += ['--stack', self.stack]
        if self.config_file and args[0] in ('config', 'preview', 'up'):
            command += ['--config-file', str(self.config_file)]
        try:
            result = subprocess.run(command, cwd=ROOT / 'infrastructure', env=self.env,
                                    text=True, encoding='utf-8', capture_output=True, timeout=2700)
        except subprocess.TimeoutExpired:
            raise RuntimeError('Pulumi timed out; inspect the stack for an interrupted update before rerunning') from None
        if result.returncode:
            # Provider diagnostics can contain tokens/config/kubeconfig: never echo them.
            raise RuntimeError(f'Pulumi {args[0]} failed (exit {result.returncode}). '
                               'Check Pulumi login, backend access, token scopes and stack update history. '
                               'Existing resources/state were retained; no rollback was attempted.')
        return result.stdout

    def select(self, ask):
        identity = json.loads(self.run('whoami', '--json'))
        backend = self.env['PULUMI_BACKEND_URL']
        cloud = backend.startswith('https://')
        default = f'{identity["user"]}/{PROJECT}/production' if cloud else 'production'
        self.stack = ask('Pulumi stack', default)
        if not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,2}', self.stack):
            raise ValueError('Invalid Pulumi stack name')
        if cloud and (len(self.stack.split('/')) != 3 or self.stack.split('/')[1] != PROJECT):
            raise ValueError(f'Use OWNER/{PROJECT}/STACK for Pulumi Cloud')
        LOCAL.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256((backend + '/' + self.stack).encode()).hexdigest()[:20]
        self.config_file = LOCAL / (name + '.yaml')
        print(f'Pulumi backend: {backend}\nPulumi stack: {self.stack}')
        self.run('stack', 'select', self.stack, '--create')
        if not self.config_file.exists():
            state = json.loads(self.run('stack', 'export', '--stack', self.stack))
            if state.get('deployment', {}).get('resources'):
                self.run('config', 'refresh', '--force')
        configs = json.loads(self.run('config', '--json'))
        entry = configs.get(PROJECT + ':infrastructure')
        return json.loads(entry['value']) if entry else None

    def verify_ownership(self, plan):
        state = json.loads(self.run('stack', 'export', '--stack', self.stack))
        resources = state.get('deployment', {}).get('resources', [])
        for item in resources:
            if not item.get('custom') or item.get('external'):
                continue
            kind = item['type']
            actual = None
            if kind == 'digitalocean:index/kubernetesCluster:KubernetesCluster':
                actual = (plan['cluster'] or {}).get('id')
            elif kind == 'digitalocean:index/certificate:Certificate':
                actual = (plan['certificate'] or {}).get('name')
            elif kind == 'digitalocean:index/domain:Domain':
                actual = None if plan['create_domain'] else plan['domain']
            else:
                continue
            if item.get('id') and actual != item['id']:
                raise ValueError('Resource discovery differs from Pulumi state. '
                                 'Inspect the selected stack and DO account; refusing to adopt/recreate resources.')

    def apply(self, spec, directory=LOCAL):
        directory.mkdir(parents=True, exist_ok=True)
        self.run('config', 'set', PROJECT + ':infrastructure', json.dumps(spec))
        print('Pulumi desired configuration (no credentials):')
        print(json.dumps(spec, indent=2))
        # Unique ignored plan file; never reuse a stale plan after failure/cancellation.
        with tempfile.TemporaryDirectory(prefix='preview-', dir=directory) as temp:
            saved = str(Path(temp) / 'plan.json')
            print('Running Pulumi preview ...', flush=True)
            summary = preview_changes(self.run('preview', '--json', '--refresh',
                                                '--save-plan', saved, '--suppress-outputs'))
            print('Changes: ' + ', '.join(f'{op}={count}' for op, count in sorted(summary.items())))
            if input('Apply this Pulumi plan and save GitHub settings? [no]: ').strip().lower() != 'yes':
                print('Cancelled. Pulumi configuration is saved locally; no cloud/GitHub update was applied.')
                return None
            print('Applying reviewed plan; certificate/cluster creation can take several minutes ...', flush=True)
            self.run('up', '--yes', '--skip-preview', '--plan', saved, '--suppress-outputs')
        outputs = json.loads(self.run('stack', 'output', '--json', '--stack', self.stack))
        return configuration({'DOKS_CLUSTER_ID': outputs['clusterId'],
                              'REPOSILITE_HOSTNAME': outputs['hostname'],
                              'DO_CERTIFICATE_NAME': outputs['certificateName']})


def prepare(token, ask):
    executable = shutil.which('pulumi')
    if not executable:
        raise RuntimeError('Install Pulumi CLI and run pulumi login first; see docs/pulumi.md')
    if any(importlib.util.find_spec(name) is None for name in ('pulumi', 'pulumi_digitalocean')):
        raise RuntimeError('Install Python dependencies: python -m pip install -r infrastructure/requirements.txt')
    backend = ask('Pulumi state backend', os.environ.get('PULUMI_BACKEND_URL', 'https://api.pulumi.com'))
    client = Pulumi(executable, token, backend)
    previous = client.select(ask)
    return client, previous
