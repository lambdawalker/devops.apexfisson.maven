"""Infrastructure-only HTTPS edge, DNS and renewable DNS-01 issuer."""
import base64
import os
import pulumi
import pulumi_cloudflare as cf
import pulumi_kubernetes as k8s

TRAEFIK_VERSION = '37.4.0'
CERT_MANAGER_VERSION = 'v1.21.1'


def build_edge(spec, cluster):
    token = os.environ.get('CLOUDFLARE_API_TOKEN')
    if not token:
        raise ValueError('CLOUDFLARE_API_TOKEN is required in the environment')
    provider = k8s.Provider('cluster-kubernetes', kubeconfig=pulumi.Output.secret(
        cluster.kube_configs.apply(lambda configs: configs[0].raw_config)))
    opts = pulumi.ResourceOptions(provider=provider, protect=True)
    edge = k8s.core.v1.Namespace('edge-namespace', metadata={'name': 'reposilite-edge'}, opts=opts)
    manager_ns = k8s.core.v1.Namespace('cert-manager-namespace', metadata={'name': 'cert-manager'}, opts=opts)
    gateway = k8s.helm.v3.Release('gateway', name='reposilite-edge', namespace='reposilite-edge',
        chart='traefik', version=TRAEFIK_VERSION,
        repository_opts={'repo': 'https://traefik.github.io/charts'},
        values={'fullnameOverride': 'reposilite-edge',
                'additionalArguments': [
                    '--entrypoints.websecure.transport.respondingtimeouts.readtimeout=300s',
                    '--entrypoints.websecure.transport.respondingtimeouts.writetimeout=300s',
                    '--entrypoints.websecure.transport.respondingtimeouts.idletimeout=300s'],
                'image': {'tag': 'v3.6.2'},
                'ingressClass': {'enabled': True, 'isDefaultClass': False, 'name': 'reposilite-edge'},
                'providers': {'kubernetesIngress': {'ingressClass': 'reposilite-edge'}, 'kubernetesCRD': {'enabled': False}},
                'ingressRoute': {'dashboard': {'enabled': False}},
                'ports': {'web': {'redirections': {'entryPoint': {'to': 'websecure', 'scheme': 'https', 'permanent': True}}},
                          'websecure': {'tls': {'enabled': True}}},
                'service': {'type': 'LoadBalancer', 'annotations': {
                    'service.beta.kubernetes.io/do-loadbalancer-protocol': 'tcp'}}},
        timeout=1200, opts=pulumi.ResourceOptions(provider=provider, protect=True, depends_on=[edge]))
    manager = k8s.helm.v3.Release('cert-manager', name='cert-manager', namespace='cert-manager',
        chart='oci://quay.io/jetstack/charts/cert-manager', version=CERT_MANAGER_VERSION,
        values={'crds': {'enabled': True}},
        timeout=1200, opts=pulumi.ResourceOptions(provider=provider, protect=True, depends_on=[manager_ns]))
    secret = k8s.core.v1.Secret('cloudflare-dns-token', metadata={'name': 'cloudflare-dns-token', 'namespace': 'cert-manager'},
        data=pulumi.Output.secret({'api-token': base64.b64encode(token.encode()).decode()}),
        opts=pulumi.ResourceOptions(provider=provider, protect=True, depends_on=[manager_ns], additional_secret_outputs=['data']))
    settings = spec['cloudflare']
    k8s.apiextensions.CustomResource('cloudflare-letsencrypt', api_version='cert-manager.io/v1', kind='ClusterIssuer',
        metadata={'name': 'cloudflare-letsencrypt'}, spec={'acme': {
            'email': settings['acmeEmail'], 'server': 'https://acme-v02.api.letsencrypt.org/directory',
            'privateKeySecretRef': {'name': 'cloudflare-letsencrypt-account'},
            'solvers': [{'selector': {'dnsZones': [settings['zoneName']]}, 'dns01': {'cloudflare': {
                'apiTokenSecretRef': {'name': 'cloudflare-dns-token', 'key': 'api-token'}}}}]}},
        opts=pulumi.ResourceOptions(provider=provider, protect=True, depends_on=[manager, secret]))
    service = k8s.core.v1.Service.get('gateway-service',
        gateway.status.apply(lambda _: 'reposilite-edge/reposilite-edge'),
        opts=pulumi.ResourceOptions(provider=provider, depends_on=[gateway]))
    def address(status):
        entries = status.load_balancer.ingress
        if not entries or not entries[0].ip:
            raise ValueError('Gateway load balancer has no IPv4 address; inspect its Service')
        return entries[0].ip
    cf.DnsRecord('gateway-dns', zone_id=settings['zoneId'], name=spec['hostname'], type='A',
        content=service.status.apply(address), ttl=300, proxied=False,
        opts=pulumi.ResourceOptions(protect=True))
