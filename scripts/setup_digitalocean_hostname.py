"""Configure hostname routing and public HTTPS inside DOKS; never changes DNS."""
import json
import socket
import uuid

import setup_ui as ui
from independent_setup import (Api, approve, cluster_access, credentials, fields, hostname, ipv4,
                               launch, load_settings, require, save_settings, command)
from digitalocean_stages import (CERT_MANAGER_VERSION, gateway, gateway_ip,
                                 apply_objects, ingress, ready_for_public, check_http)


def validate(values):
    errors = {}
    for key, check in [('cluster', uuid.UUID), ('hostname', hostname), ('ip', ipv4)]:
        try:
            check(values[key])
        except ValueError:
            errors[key] = 'Enter a valid ' + key + '.'
    if '@' not in values['email'] or any(c.isspace() for c in values['email']):
        errors['email'] = 'Enter a monitored email address.'
    return errors


def execute(args):
    ui.stage('credentials')
    require('doctl', 'kubectl', 'helm')
    token = credentials(args, 'digitalocean')
    saved = load_settings()
    values = fields('DigitalOcean hostname and HTTPS', [
        ui.Field('cluster', 'DOKS cluster UUID', saved.get('cluster_id', '')),
        ui.Field('hostname', 'Maven hostname', saved.get('hostname', 'maven.apexfission.com')),
        ui.Field('ip', 'Gateway public IPv4', saved.get('ip', '')),
        ui.Field('email', 'Certificate renewal email', 'hostmaster@apexfission.com')], validate=validate,
        description='DNS must already point to the gateway. This stage uses HTTP-01 validation and needs no Cloudflare token.')
    ui.stage('discovery')
    try:
        addresses = {r[4][0] for r in socket.getaddrinfo(values['hostname'], 80, type=socket.SOCK_STREAM)}
    except OSError:
        raise ui.InputError('Hostname is not resolving yet. Run the Cloudflare stage and wait for DNS.') from None
    if addresses != {values['ip']}:
        raise ui.InputError('DNS must resolve only to the expected gateway IPv4 (no proxy or conflicting AAAA).')
    if not approve('Configure HTTPS on DigitalOcean', f'Cluster: {values["cluster"]}\nHostname: {values["hostname"]}\n'
                   f'Gateway: {values["ip"]}\nInstall cert-manager and an HTTP-01 issuer, request TLS, then remove the IP-only route.\n'
                   'No DNS or GitHub changes. Existing application data is retained.'):
        return
    ui.stage('apply')
    with cluster_access(token, values['cluster']) as (kubectl, env):
        ready_for_public(kubectl)
        current_ip = gateway_ip(kubectl, Api('digitalocean', token))
        if current_ip != values['ip']:
            raise ui.InputError('This cluster has a different gateway IP; correct DNS/inputs before proceeding.')
        gateway(env)
        kubectl('-n', 'reposilite-edge', 'annotate', 'service', 'reposilite-edge',
                'service.beta.kubernetes.io/do-loadbalancer-hostname=' + values['hostname'], '--overwrite')
        command(['helm', 'upgrade', '--install', 'cert-manager', 'oci://quay.io/jetstack/charts/cert-manager',
                 '--version', CERT_MANAGER_VERSION, '--kube-context', 'reposilite-independent', '--namespace', 'cert-manager', '--create-namespace',
                 '--set', 'crds.enabled=true', '--wait', '--timeout', '20m'], env=env, timeout=1300)
        issuer = {'apiVersion': 'cert-manager.io/v1', 'kind': 'ClusterIssuer',
                  'metadata': {'name': 'reposilite-http01'}, 'spec': {'acme': {
                      'email': values['email'], 'server': 'https://acme-v02.api.letsencrypt.org/directory',
                      'privateKeySecretRef': {'name': 'reposilite-http01-account'},
                      'solvers': [{'http01': {'ingress': {'ingressClassName': 'reposilite-edge'}}}]}}}
        apply_objects(kubectl, [issuer])
        apply_objects(kubectl, [ingress('reposilite', values['hostname'], tls=True)])
        kubectl('-n', 'reposilite', 'wait', '--for=create', 'certificate/reposilite-tls', '--timeout=2m')
        kubectl('-n', 'reposilite', 'wait', '--for=condition=Ready', 'certificate/reposilite-tls', '--timeout=10m')
        middleware = {'apiVersion': 'traefik.io/v1alpha1', 'kind': 'Middleware',
                      'metadata': {'name': 'https-redirect', 'namespace': 'reposilite'},
                      'spec': {'redirectScheme': {'scheme': 'https', 'permanent': True}}}
        redirect = ingress('reposilite-http', values['hostname'])
        redirect['metadata']['annotations']['traefik.ingress.kubernetes.io/router.middlewares'] = 'reposilite-https-redirect@kubernetescrd'
        apply_objects(kubectl, [middleware, redirect])
        kubectl('-n', 'reposilite', 'delete', 'ingress', 'reposilite-ip', '--ignore-not-found')
    ui.stage('verify')
    check_http('https://' + values['hostname'])
    save_settings(cluster_id=values['cluster'], ip=values['ip'], hostname=values['hostname'], tls_mode='http01')
    ui.say('HTTPS is ready. Next, run setup_github.py to save deployment settings.')


if __name__ == '__main__':
    raise SystemExit(launch('digitalocean-hostname', execute))
