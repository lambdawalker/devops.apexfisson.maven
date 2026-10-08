"""Direct Kubernetes/Helm operations shared by the two DigitalOcean stages."""
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

import setup_ui as ui
from independent_setup import ROOT, command, fields

TRAEFIK_VERSION = '37.4.0'
CERT_MANAGER_VERSION = 'v1.21.1'


def apply_objects(kubectl, objects):
    payload = json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': objects})
    kubectl('apply', '--dry-run=server', '-f', '-', stdin=payload)
    kubectl('apply', '-f', '-', stdin=payload)


def gateway(env):
    values = {'fullnameOverride': 'reposilite-edge', 'image': {'tag': 'v3.6.2'},
              'ingressClass': {'enabled': True, 'isDefaultClass': False, 'name': 'reposilite-edge'},
              'providers': {'kubernetesIngress': {'ingressClass': 'reposilite-edge'},
                            'kubernetesCRD': {'enabled': True}},
              'ingressRoute': {'dashboard': {'enabled': False}},
              'ports': {'web': {'redirections': {}}, 'websecure': {'tls': {'enabled': True}}},
              'service': {'type': 'LoadBalancer', 'annotations': {
                  'service.beta.kubernetes.io/do-loadbalancer-protocol': 'tcp'}}}
    with tempfile.TemporaryDirectory(prefix='reposilite-helm-') as temp:
        path = Path(temp) / 'values.json'
        path.write_text(json.dumps(values), encoding='utf-8')
        command(['helm', 'upgrade', '--install', 'reposilite-edge', 'traefik',
                 '--repo', 'https://traefik.github.io/charts', '--version', TRAEFIK_VERSION,
                 '--namespace', 'reposilite-edge', '--kube-context', 'reposilite-independent', '--create-namespace', '--reset-values',
                 '--values', path, '--wait', '--timeout', '20m'], env=env, timeout=1300)


def gateway_ip(kubectl, api=None):
    kubectl('-n', 'reposilite-edge', 'wait', '--for=jsonpath={.status.loadBalancer.ingress}',
            'service/reposilite-edge', '--timeout=10m')
    service = json.loads(kubectl('-n', 'reposilite-edge', 'get', 'service', 'reposilite-edge', '-o', 'json', private=True))
    from independent_setup import ipv4
    entry = service['status']['loadBalancer']['ingress'][0]
    if entry.get('ip'):
        return ipv4(entry['ip'])
    # DOKS hostname annotation can replace status.ip with status.hostname.
    # Recover the actual address from the LB ID, not from untrusted DNS alone.
    ident = service.get('metadata', {}).get('annotations', {}).get('kubernetes.digitalocean.com/load-balancer-id')
    if api and ident:
        import uuid
        uuid.UUID(ident)
        result = api.request('GET', '/load_balancers/' + ident)
        if result:
            return ipv4(result['load_balancer']['ip'])
    raise ui.SetupError('Gateway IPv4 is unavailable; inspect its DigitalOcean load balancer.')


def ingress(name, hostname=None, tls=False):
    annotations = {'traefik.ingress.kubernetes.io/router.entrypoints': 'websecure' if tls else 'web'}
    rule = {'http': {'paths': [{'path': '/', 'pathType': 'Prefix', 'backend': {
        'service': {'name': 'reposilite', 'port': {'number': 8080}}}}]}}
    if hostname:
        rule['host'] = hostname
    spec = {'ingressClassName': 'reposilite-edge', 'rules': [rule]}
    if tls:
        annotations.update({'cert-manager.io/cluster-issuer': 'reposilite-http01',
                            'traefik.ingress.kubernetes.io/router.tls': 'true'})
        spec['tls'] = [{'hosts': [hostname], 'secretName': 'reposilite-tls'}]
    return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'Ingress',
            'metadata': {'name': name, 'namespace': 'reposilite', 'annotations': annotations}, 'spec': spec}


def ready_for_public(kubectl):
    for kind, name in [('secret', 'reposilite-bootstrap'), ('pod', 'reposilite-maintenance')]:
        if kubectl('-n', 'reposilite', 'get', kind, name, '--ignore-not-found', '-o', 'name').strip():
            raise ui.InputError(f'Complete bootstrap/maintenance and remove {kind}/{name} first.')
    dep = json.loads(kubectl('-n', 'reposilite', 'get', 'deployment', 'reposilite', '-o', 'json', private=True))
    pvc = json.loads(kubectl('-n', 'reposilite', 'get', 'pvc', 'reposilite-data', '-o', 'json', private=True))
    if dep['spec'].get('replicas') != 1 or pvc.get('status', {}).get('phase') != 'Bound':
        raise ui.InputError('Reposilite must have one replica and a Bound data volume before exposure.')
    container = next(c for c in dep['spec']['template']['spec']['containers'] if c['name'] == 'reposilite')
    if container.get('envFrom'):
        raise ui.InputError('Remove temporary bootstrap environment references before exposure.')


def bootstrap_private(kubectl):
    if kubectl('-n', 'reposilite', 'get', 'ingress', '-o', 'name').strip():
        raise ui.InputError('Remove public routes before private bootstrap.')
    if kubectl('-n', 'reposilite', 'get', 'pod', 'reposilite-maintenance', '--ignore-not-found', '-o', 'name').strip():
        raise ui.InputError('Finish maintenance before bootstrap.')
    raw = kubectl('-n', 'reposilite', 'get', 'service', 'reposilite', '--ignore-not-found', '-o', 'json', private=True)
    if raw.strip() and json.loads(raw)['spec'].get('type') != 'ClusterIP':
        raise ui.InputError('Remove public Service exposure before bootstrap.')


def private_bootstrap(kubectl, env):
    existing = kubectl('-n', 'reposilite', 'get', 'deployment', 'reposilite', '--ignore-not-found', '-o', 'name').strip()
    if existing:
        dep = json.loads(kubectl('-n', 'reposilite', 'get', 'deployment', 'reposilite', '-o', 'json', private=True))
        container = next(c for c in dep['spec']['template']['spec']['containers'] if c['name'] == 'reposilite')
        if not container.get('envFrom'):
            ready_for_public(kubectl)
            if dep.get('metadata', {}).get('annotations', {}).get('apexfission.com/bootstrap-verified') != 'true':
                verify_admin(kubectl, env)
            return
        if container['envFrom'] != [{'secretRef': {'name': 'reposilite-bootstrap', 'optional': True}}]:
            raise ui.InputError('Unexpected application credentials configuration; review before bootstrap.')
    else:
        bootstrap_private(kubectl)
        kubectl('apply', '-k', str(ROOT / 'k8s/base'))
    bootstrap_private(kubectl)
    svc = json.loads(kubectl('-n', 'reposilite', 'get', 'service', 'reposilite', '-o', 'json', private=True))
    if svc['spec'].get('type') != 'ClusterIP':
        raise ui.InputError('Bootstrap requires a private ClusterIP Service.')
    has_secret = kubectl('-n', 'reposilite', 'get', 'secret', 'reposilite-bootstrap', '--ignore-not-found', '-o', 'name').strip()
    password = ''
    answer = fields('Private administrator bootstrap', [ui.Field('password',
        'Temporary bootstrap password (save it to sign in locally)', password, secret=True)],
        validate=lambda v: {} if v['password'].isalnum() and v['password'].isascii() and len(v['password']) >= 32
        else {'password': 'Use at least 32 ASCII letters/numbers.'})
    ui.register_secrets([answer['password']])
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'reposilite-bootstrap', 'namespace': 'reposilite'},
              'stringData': {'REPOSILITE_OPTS': '--token bootstrap:' + answer['password']}}
    if not has_secret:
        kubectl('create', '-f', '-', stdin=json.dumps(secret), private=True)
    else:
        ui.say('Existing bootstrap secret retained. Use its original password for the localhost sign-in.')
    kubectl('-n', 'reposilite', 'rollout', 'restart', 'deployment/reposilite')
    kubectl('-n', 'reposilite', 'rollout', 'status', 'deployment/reposilite', '--timeout=10m')
    with local_console(env) as port:
        fields('Create and verify a permanent administrator', [], description=(
            f'Open http://127.0.0.1:{port}. Sign in as bootstrap with the password you saved.\n'
            'In the dashboard Console run: token-generate admin m\n'
            'Save the generated admin secret, log out, and verify that admin can sign in.\n'
            'Select Proceed only after the permanent admin login works. No public route exists yet.'))
    kubectl('-n', 'reposilite', 'delete', 'secret', 'reposilite-bootstrap')
    kubectl('-n', 'reposilite', 'patch', 'deployment', 'reposilite', '--type=strategic', '-p', json.dumps({
        'spec': {'template': {'spec': {'containers': [{'name': 'reposilite', 'envFrom': None}]}}}}))
    kubectl('-n', 'reposilite', 'rollout', 'status', 'deployment/reposilite', '--timeout=10m')
    verify_admin(kubectl, env)
    ready_for_public(kubectl)


def verify_admin(kubectl, env):
    with local_console(env) as port:
        fields('Verify permanent administrator', [], description=(
            f'Open http://127.0.0.1:{port}. Verify the permanent admin signs in and bootstrap fails.\n'
            'Proceed only after both checks pass.'))
    kubectl('-n', 'reposilite', 'annotate', 'deployment', 'reposilite',
            'apexfission.com/bootstrap-verified=true', '--overwrite')


def check_http(url):
    try:
        with urlopen(url, timeout=30) as response:
            if response.status != 200:
                raise ui.SetupError('Endpoint did not return HTTP 200.')
    except OSError:
        raise ui.SetupError('Endpoint is not reachable yet. Keep the resource IDs and rerun verification.') from None
    ui.say('Verified HTTP 200: ' + url)


@contextmanager
def local_console(env):
    # Let kubectl allocate a local port; instead choose a free localhost port and
    # fail safely if another process wins the race before port-forward binds.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(['kubectl', '--context', 'reposilite-independent', '-n', 'reposilite',
        'port-forward', '--address', '127.0.0.1', 'service/reposilite', f'{port}:8080'],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            ui.check_cancelled()
            if process.poll() is not None:
                raise ui.SetupError('Local port-forward failed; keep the deployment private and retry bootstrap.')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=1):
                    break
            except OSError:
                time.sleep(.2)
        else:
            raise ui.SetupError('Local port-forward was not ready.')
        yield port
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
