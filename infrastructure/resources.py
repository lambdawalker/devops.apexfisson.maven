"""DigitalOcean infrastructure only; Kubernetes application objects have another owner."""
import pulumi
import pulumi_digitalocean as do


def options(spec, dependencies=None, credentials=False):
    ignored = ['*'] if spec.get('preserveImported') else (
        ['version'] if spec.get('properties', {}).get('autoUpgrade') else None)
    return pulumi.ResourceOptions(
        protect=True,
        import_=spec.get('importId'),
        ignore_changes=ignored,
        depends_on=dependencies,
        additional_secret_outputs=['kubeConfigs'] if credentials else None,
        custom_timeouts=pulumi.CustomTimeouts(create='40m', update='40m'),
    )


def build(spec):
    domain_spec = spec.get('domain')
    domain = do.Domain('domain', name=domain_spec['name'], opts=options(domain_spec)) if domain_spec else None
    cert_spec = spec['certificate']
    if cert_spec['mode'] == 'external':
        certificate = do.Certificate.get('certificate', cert_spec['name'])
    else:
        certificate = do.Certificate(
            'certificate', name=cert_spec['name'], type='lets_encrypt',
            domains=cert_spec['domains'],
            opts=options(cert_spec, [domain] if domain is not None else None),
        )
    cluster_spec = spec['cluster']
    props = cluster_spec['properties']
    pool = props['nodePool']
    cluster = do.KubernetesCluster(
        'cluster', name=props['name'], region=props['region'], version=props['version'],
        node_pool=do.KubernetesClusterNodePoolArgs(
            name=pool['name'], size=pool['size'], node_count=pool['nodeCount']),
        ha=props['ha'], auto_upgrade=props['autoUpgrade'], surge_upgrade=props['surgeUpgrade'],
        vpc_uuid=props.get('vpcUuid'),
        maintenance_policy=do.KubernetesClusterMaintenancePolicyArgs(day='saturday', start_time='06:00'),
        destroy_all_associated_resources=False, kubeconfig_expire_seconds=1800,
        opts=options(cluster_spec, [certificate], credentials=True),
    )
    # Never export kubeconfig or other credentials. Consumers only need these values.
    return {'clusterId': cluster.id, 'hostname': spec['hostname'], 'certificateName': certificate.name}
