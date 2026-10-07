"""Explicit, resumable teardown of a failed setup, including associated storage.

No mutations without --execute and a typed stack confirmation. This recovery
utility deliberately uses cloud APIs so a broken Kubernetes provider cannot
prevent teardown. Normal setup/discovery remains read-only outside Pulumi.
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener

from digitalocean import NoRedirect
from setup_output import Transcript
from setup_ui import SetupError

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = 'digitalocean:index/kubernetesCluster:KubernetesCluster'
DOMAIN = 'digitalocean:index/domain:Domain'
CERT = 'digitalocean:index/certificate:Certificate'
DNS = 'cloudflare:index/dnsRecord:DnsRecord'
MEMBERSHIP = 'digitalocean:index/projectResources:ProjectResources'
ASSOCIATED = {'load_balancers': 'load_balancers', 'volumes': 'volumes',
              'volume_snapshots': 'snapshots'}


class CleanupError(RuntimeError):
    pass


def segment(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', value):
        raise CleanupError('Invalid resource ID; refusing cleanup.')
    return quote(value, safe='')


class API:
    def __init__(self, provider, token):
        self.provider = provider
        self.base = ('https://api.digitalocean.com/v2' if provider == 'do'
                     else 'https://api.cloudflare.com/client/v4')
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, body=None):
        if (method not in ('GET', 'DELETE') or not path.startswith('/')
                or path.startswith('//') or '://' in path or '..' in path):
            raise CleanupError('Refusing an unsupported cleanup API request.')
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}
        req = Request(self.base + path, method=method, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req, timeout=60) as response:
                raw = response.read()
            result = json.loads(raw) if raw else {}
            if method == 'GET' and not result:
                raise CleanupError(f'{self.provider} returned an empty discovery response.')
            if self.provider == 'cf' and not result.get('success'):
                raise CleanupError('Cloudflare did not confirm success.')
            return result
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise CleanupError(f'{self.provider} {method} failed (HTTP {exc.code}); '
                               'check token scopes and retry. State is retained.') from None
        except (URLError, OSError, ValueError):
            raise CleanupError(f'{self.provider} {method} failed or returned invalid JSON; '
                               'completion is unknown. Rerun to inspect and resume.') from None


def inventory(state):
    deployment = state.get('deployment', {})
    if deployment.get('pending_operations'):
        raise CleanupError('Stack has pending operations. Resolve them in Pulumi before cleanup.')
    plan = dict(clusters=[], dns=[], certificates=[], preserved=[],
                load_balancers=[], volumes=[], volume_snapshots=[])
    for resource in deployment.get('resources', []):
        kind, ident = resource['type'], resource.get('id')
        if not resource.get('custom') or kind.startswith('pulumi:providers:'):
            continue
        if resource.get('external') or kind == DOMAIN:
            plan['preserved'].append(f'{kind} {ident or "(no ID)"}')
            continue
        if kind == CLUSTER:
            if not ident:
                raise CleanupError('Cluster has no recorded ID; resolve its ownership first.')
            plan['clusters'].append(segment(ident))
        elif kind == DNS:
            props = resource.get('outputs') or resource.get('inputs', {})
            zone = props.get('zoneId') or props.get('zone_id')
            if not ident or not zone:
                raise CleanupError('DNS ownership is incomplete; refusing to guess a record.')
            plan['dns'].append({'zone': segment(zone), 'id': segment(ident.split('/')[-1])})
        elif kind == CERT:
            # Certificates can be shared with other load balancers. Keep them.
            plan['preserved'].append(f'{kind} {ident or "(no ID)"}')
        elif kind == MEMBERSHIP or kind.startswith('kubernetes:'):
            pass  # Cluster deletion removes its objects and project membership.
        else:
            raise CleanupError(f'Unsupported stack resource type {kind}; refusing to forget it.')
    kubernetes = [r for r in deployment.get('resources', [])
                  if r.get('custom') and not r.get('external') and r['type'].startswith('kubernetes:')]
    clusters = {r['urn'] for r in deployment.get('resources', [])
                if r['type'] == CLUSTER and r.get('urn') and not r.get('external')}
    providers = {r['urn']: r for r in deployment.get('resources', [])
                 if r['type'] == 'pulumi:providers:kubernetes' and r.get('urn')}
    for resource in kubernetes:
        provider_urn = resource.get('provider', '').rsplit('::', 1)[0]
        provider = providers.get(provider_urn, {})
        if not clusters.intersection(provider.get('dependencies', [])):
            raise CleanupError('Kubernetes provider is not linked to the recorded cluster; refusing to forget state.')
    if len(plan['clusters']) > 1:
        raise CleanupError('Multiple clusters in this stack; review ownership manually.')
    return plan


def discover_associations(plan, api):
    for ident in plan['clusters']:
        result = api.request('GET', f'/kubernetes/clusters/{segment(ident)}/destroy_with_associated_resources')
        if result is None:
            continue  # Keep IDs saved by an earlier, interrupted attempt.
        for key in ASSOCIATED:
            if not isinstance(result.get(key), list):
                raise CleanupError('Incomplete DigitalOcean associated-resource inventory.')
            plan[key] = sorted(set(plan[key]) | {segment(item['id']) for item in result[key]})


def wait_absent(api, path, say, timeout):
    deadline = time.monotonic() + timeout
    while api.request('GET', path) is not None:
        if time.monotonic() >= deadline:
            raise CleanupError(f'Deletion is still pending for {path}; rerun cleanup to resume.')
        say(f'Waiting for deletion: {path}')
        time.sleep(10)


def delete_resources(plan, do, cf, say, timeout=1800):
    # Delete DNS first so new clients do not hit a partially removed deployment.
    for record in plan['dns']:
        path = f'/zones/{segment(record["zone"])}/dns_records/{segment(record["id"])}'
        if cf.request('GET', path) is not None:
            cf.request('DELETE', path)
        wait_absent(cf, path, say, timeout)
        say(f'Absent: DNS record {record["id"]}')
    for ident in plan['clusters']:
        path = '/kubernetes/clusters/' + segment(ident)
        if do.request('GET', path) is not None:
            # IDs are the reviewed, durable inventory; never delete by name or
            # use the account-wide/project-wide resource list as a delete list.
            body = {key: [rid for rid in plan[key]
                          if do.request('GET', f'/{endpoint}/{segment(rid)}') is not None]
                    for key, endpoint in ASSOCIATED.items()}
            do.request('DELETE', path + '/destroy_with_associated_resources/selective', body)
        wait_absent(do, path, say, timeout)
        say(f'Absent: cluster {ident}')
    # Selective deletion is asynchronous and may have partially succeeded.
    # After the cluster is gone, explicitly clean up any recorded leftovers.
    for key, endpoint in ASSOCIATED.items():
        for ident in plan[key]:
            path = f'/{endpoint}/{segment(ident)}'
            if do.request('GET', path) is not None:
                do.request('DELETE', path)
            wait_absent(do, path, say, timeout)
            say(f'Absent: {key} {ident}')


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class Stack:
    def __init__(self, backend, name):
        self.name = name
        self.env = dict(os.environ, PULUMI_BACKEND_URL=backend, PULUMI_SKIP_UPDATE_CHECK='true')
        self.executable = shutil.which('pulumi')
        if not self.executable:
            raise CleanupError('Install Pulumi CLI and log in to the selected backend first.')

    def run(self, *args):
        result = subprocess.run([self.executable, *args, '--non-interactive', '--color', 'never'],
                                cwd=ROOT / 'infrastructure', env=self.env,
                                capture_output=True, text=True, encoding='utf-8', timeout=180)
        if result.returncode:
            # Raw stack export and CLI diagnostics may contain private values.
            raise CleanupError(f'Pulumi {args[0]} {args[1]} failed (exit {result.returncode}). '
                               'Check login/backend access and active updates; no errors are ignored.')
        return result.stdout

    def exists(self):
        items = json.loads(self.run('stack', 'ls', '--json', '--all', '--fully-qualify-stack-names'))
        return any(item['name'] == self.name for item in items)

    def export(self):
        return json.loads(self.run('stack', 'export', '--stack', self.name))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stack', default='isdavid/apexfission-maven/production')
    parser.add_argument('--backend', default='https://api.pulumi.com')
    parser.add_argument('--execute', action='store_true', help='Enable deletion after typed confirmation')
    parser.add_argument('--cluster-id', help='Explicit recovery ID when the stack has no cluster record')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[\w.-]+/apexfission-maven/[\w.-]+', args.stack):
        parser.error('Use OWNER/apexfission-maven/STACK')
    parsed = urlsplit(args.backend)
    if (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        parser.error('This recovery tool requires a Pulumi Cloud HTTPS backend without credentials in its URL')
    transcript = Transcript(ROOT / '.local' / 'logs', print)
    say = lambda message: transcript.emit(str(message) + '\n')
    try:
        say(f'Cleanup log: {transcript.path}')
        say(f'Backend: {args.backend}\nStack: {args.stack}')
        stack = Stack(args.backend, args.stack)
        exists = stack.exists()
        state = stack.export() if exists else {}
        plan = inventory(state)
        name = hashlib.sha256((args.backend + '/' + args.stack).encode()).hexdigest()[:20]
        directory = ROOT / '.local' / 'cleanup' / name
        journal = directory / 'resources.json'
        if journal.exists():
            saved = json.loads(journal.read_text(encoding='utf-8'))
            if saved['backend'] != args.backend or saved['stack'] != args.stack:
                raise CleanupError('Cleanup journal identity does not match.')
            # A new stack after a completed reset starts a new inventory.
            if not saved.get('complete'):
                for key in plan:
                    for value in saved['resources'][key]:
                        if value not in plan[key]:
                            plan[key].append(value)
        if args.cluster_id:
            ident = segment(args.cluster_id)
            if plan['clusters'] and plan['clusters'] != [ident]:
                raise CleanupError('Explicit cluster ID differs from the recorded cluster.')
            plan['clusters'] = [ident]
        if len(plan['clusters']) > 1:
            raise CleanupError('Journal and stack disagree about cluster ownership.')
        tokens = {}
        token_file = ROOT / '.local' / 'tokens.gpg'
        if token_file.exists():
            password = getpass.getpass('Encrypted tokens passphrase (blank to enter tokens manually): ')
            if password:
                from token_store import decrypt_tokens
                tokens = decrypt_tokens(token_file, password)
        def credential(key, variable):
            token = os.environ.get(variable) or tokens.get(key) or getpass.getpass(variable + ': ')
            if not token or any(c.isspace() for c in token):
                raise CleanupError('A nonempty, single-line API token is required.')
            transcript.register([token])
            return token
        do = API('do', credential('digitalocean', 'DIGITALOCEAN_TOKEN')) if (
            plan['clusters'] or any(plan[key] for key in ASSOCIATED)) else None
        cf = API('cf', credential('cloudflare', 'CLOUDFLARE_API_TOKEN')) if plan['dns'] else None
        if do:
            discover_associations(plan, do)
        say('DELETE (including data on listed volumes):')
        for key in ('clusters', 'dns', *ASSOCIATED):
            say(f'  {key}: {json.dumps(plan[key])}')
        say('PRESERVE shared domains/certificates: ' + json.dumps(plan['preserved']))
        say('Preserve DigitalOcean projects/VPCs, Cloudflare zones, GitHub settings, encrypted tokens and logs.')
        say('Remove the selected Pulumi stack and its local wizard configuration after verified deletion.')
        say('Stop setup/deploy runs before continuing. Do not run cleanup concurrently.')
        if not plan['clusters']:
            say('No cluster ID recorded. Untracked cloud resources cannot be discovered; use --cluster-id if needed.')
        if not args.execute:
            say('Preview only. Rerun with --execute to delete this inventory.')
            return 0
        if input(f'Type DELETE {args.stack} to continue: ').strip() != 'DELETE ' + args.stack:
            say('Cancelled; no cloud or state changes.')
            return 0
        # Revalidate before the first destructive operation.
        if exists and stack.export() != state:
            raise CleanupError('Stack changed during review; rerun cleanup.')
        if do:
            before = json.dumps(plan, sort_keys=True)
            discover_associations(plan, do)
            if json.dumps(plan, sort_keys=True) != before:
                raise CleanupError('Associated resources changed during review; rerun cleanup.')
        snapshot = {'backend': args.backend, 'stack': args.stack, 'resources': plan, 'complete': False}
        save_json(journal, snapshot)
        if exists:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            save_json(directory / f'state-{stamp}.json', state)
        delete_resources(plan, do, cf, say)
        if exists:
            if stack.export() != state:
                raise CleanupError('Stack changed during cleanup. Cloud deletion finished; state retained for review.')
            # All owned cloud objects above are confirmed absent. Remaining
            # Kubernetes objects were inside that cluster. Shared resources are
            # intentionally detached, not deleted. Never do this on failure.
            stack.run('stack', 'rm', args.stack, '--yes', '--force', '--preserve-config')
        config = ROOT / '.local' / 'pulumi' / (name + '.yaml')
        if config.exists():
            config.replace(directory / 'wizard-config.yaml')
        snapshot['complete'] = True
        save_json(journal, snapshot)
        say('Cleanup complete for the recorded inventory. Setup can create a fresh stack.')
        return 0
    except (CleanupError, SetupError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        say(str(exc) if isinstance(exc, (CleanupError, SetupError)) else 'Cleanup could not finish; check access and the local recovery files.')
        say('Cleanup incomplete. Keep the recovery files and rerun the same command to resume.')
        return 1
    except (KeyboardInterrupt, EOFError):
        say('Cancelled. Partial deletions may have completed; rerun to inspect and resume.')
        return 130
    finally:
        transcript.close()


if __name__ == '__main__':
    raise SystemExit(main())
