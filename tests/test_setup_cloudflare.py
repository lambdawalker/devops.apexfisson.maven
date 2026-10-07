"""Offline discovery, migration and edge resource tests."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / p) for p in ('scripts', 'infrastructure')]
from cloudflare import Cloudflare, discover
from test_setup_pulumi import plan
from pulumi_setup import build_spec, Pulumi

SETTINGS = {'zoneId': 'zone-id', 'zoneName': 'example.org', 'recordId': None, 'acmeEmail': 'hostmaster@example.org'}

class DiscoveryTests(unittest.TestCase):
    def client(self, records=()):
        client = Mock()
        client.request.return_value = {'result': {'status': 'active'}}
        client.list.side_effect = [[{'id': 'zone-id', 'name': 'example.org'}], list(records)]
        return client

    def answer(self, label, default):
        return default or 'admin@example.org'

    def test_active_zone_and_email(self):
        self.assertEqual(SETTINGS, discover(self.client(), 'maven.example.org', self.answer))

    def test_no_mutations_or_cross_origin(self):
        client = Cloudflare('private-token')
        with patch.object(client.opener, 'open') as opener:
            with self.assertRaises(ValueError): client.request('POST', '/zones')
            with self.assertRaises(ValueError): client.request('GET', '//other.example/zones')
        opener.assert_not_called()

    def test_conflicts_and_unmanaged_a_record_refused(self):
        for record_type in ('CNAME', 'AAAA', 'TXT'):
            with self.assertRaisesRegex(ValueError, 'conflicting'):
                discover(self.client([{'name': 'maven.example.org', 'type': record_type, 'id': 'record'}]),
                         'maven.example.org', self.answer)
        record = {'name': 'maven.example.org', 'type': 'A', 'id': 'record'}
        with self.assertRaisesRegex(ValueError, 'unmanaged A record'):
            discover(self.client([record]), 'maven.example.org', self.answer)
        previous = dict(SETTINGS, recordId='record')
        result = discover(self.client([record]), 'maven.example.org', self.answer, previous)
        self.assertEqual(previous, result)

    def test_missing_managed_record(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            discover(self.client(), 'maven.example.org', self.answer, dict(SETTINGS, recordId='existing'))

    def test_partial_migration_preserves_cluster_domain_not_missing_certificate(self):
        previous = build_spec(plan())
        current = plan(True)
        current.update(tlsMode='cloudflare', cloudflare=SETTINGS, certificate=None)
        migrated = build_spec(current, previous)
        self.assertEqual(previous['cluster'], migrated['cluster'])
        self.assertEqual(previous['domain'], migrated['domain'])
        self.assertIsNone(migrated['certificate'])
        self.assertEqual('cloudflare', migrated['tlsMode'])

    def test_new_cloudflare_stack_has_no_do_dns_or_certificate(self):
        current = plan()
        current.update(tlsMode='cloudflare', cloudflare=SETTINGS)
        spec = build_spec(current)
        self.assertIsNone(spec['domain'])
        self.assertIsNone(spec['certificate'])

    def test_record_state_is_checked_even_without_config_import_id(self):
        client = Pulumi('pulumi', 'do-private', 'file:///tmp/state')
        state = {'deployment': {'resources': [{'custom': True, 'id': 'record', 'type': 'cloudflare:index/dnsRecord:DnsRecord'}]}}
        with patch.object(client, 'run', return_value=json.dumps(state)):
            with self.assertRaisesRegex(ValueError, 'discovery'):
                client.verify_ownership(dict(plan(), cloudflare=SETTINGS))

    def test_organization_membership_not_username(self):
        client = Pulumi('pulumi', 'private', 'https://api.pulumi.com')
        with patch.object(client, 'run', return_value=json.dumps({'user': 'person', 'organizations': []})):
            with self.assertRaisesRegex(ValueError, 'memberships'):
                client.select(self.answer)

class EdgeTests(unittest.TestCase):
    def test_real_sdk_registration_secret_dns01_and_no_application_objects(self):
        import pulumi
        from pulumi.runtime import Mocks, set_mocks
        import resources
        seen = []
        class Provider(Mocks):
            def new_resource(self, args):
                seen.append(args)
                outputs = dict(args.inputs)
                if args.typ.endswith(':KubernetesCluster'):
                    outputs.update(kubeConfigs=[{'rawConfig': 'private-kubeconfig'}], clusterUrn='do:kubernetes:cluster')
                if args.typ == 'kubernetes:core/v1:Service':
                    outputs['status'] = {'loadBalancer': {'ingress': [{'ip': '192.0.2.10'}]}}
                return args.name + '-id', outputs
            def call(self, args): return args.args
        async def execute():
            set_mocks(Provider(), project='apexfission-maven', stack='test', preview=False)
            current = plan()
            current.update(tlsMode='cloudflare', cloudflare=SETTINGS, projectId='project', projectName='Test')
            outputs = resources.build(build_spec(current))
            self.assertEqual({'clusterId', 'hostname', 'tlsMode'}, set(outputs))
            await outputs['clusterId'].future()
            from pulumi.runtime.stack import wait_for_rpcs
            await wait_for_rpcs()
        with patch.dict(os.environ, CLOUDFLARE_API_TOKEN='private-token'):
            asyncio.run(execute())
        types = {r.typ for r in seen}
        self.assertNotIn('digitalocean:index/domain:Domain', types)
        self.assertNotIn('digitalocean:index/certificate:Certificate', types)
        self.assertNotIn('kubernetes:apps/v1:Deployment', types)
        dns = next(r for r in seen if r.typ == 'cloudflare:index/dnsRecord:DnsRecord')
        self.assertEqual('192.0.2.10', dns.inputs['content'])
        self.assertFalse(dns.inputs['proxied'])
        issuer = next(r for r in seen if r.typ == 'kubernetes:cert-manager.io/v1:ClusterIssuer')
        self.assertIn('dns01', issuer.inputs['spec']['acme']['solvers'][0])
        gateway = next(r for r in seen if r.name == 'gateway')
        self.assertFalse(gateway.inputs['values']['ingressClass']['isDefaultClass'])
        self.assertIn('redirections', gateway.inputs['values']['ports']['web'])

class ProjectTests(unittest.TestCase):
    def test_account_default_and_persisted_project(self):
        from setup_cloud import choose_project
        client = Mock()
        client.list.return_value = [{'id': 'first', 'name': 'First'}, {'id': 'default', 'name': 'Default', 'is_default': True}]
        with patch('setup_cloud.ask', side_effect=lambda label, default: default):
            self.assertEqual('default', choose_project(client)['projectId'])
            self.assertEqual('first', choose_project(client, {'projectId': 'first'})['projectId'])
        with patch('setup_cloud.ask', return_value='Default'):
            with self.assertRaises(ValueError): choose_project(client, {'projectId': 'first'})

    def test_duplicate_project_names_require_id(self):
        from setup_cloud import choose_project
        client = Mock()
        client.list.return_value = [{'id': 'first', 'name': 'Duplicate'}, {'id': 'second', 'name': 'Duplicate', 'is_default': True}]
        with patch('setup_cloud.ask', return_value='Duplicate'):
            with self.assertRaises(ValueError): choose_project(client)

class MoreOrganizationTests(unittest.TestCase):
    def test_stale_default_uses_membership_and_unavailable_command_is_optional(self):
        from tempfile import TemporaryDirectory
        import pulumi_setup
        for default in ('old-org', RuntimeError('unsupported')):
            client = Pulumi('pulumi', 'private', 'https://api.pulumi.com')
            def command(*args):
                if args[0] == 'whoami': return json.dumps({'user': 'person', 'organizations': ['team']})
                if args[0] == 'org':
                    if isinstance(default, Exception): raise default
                    return default
                if args[0] == 'config': return '{}'
                return json.dumps({'deployment': {}})
            with TemporaryDirectory() as tmp, patch.object(pulumi_setup, 'LOCAL', Path(tmp)), patch.object(client, 'run', side_effect=command):
                client.select(lambda label, value: value)
            self.assertEqual('team/apexfission-maven/production', client.stack)

    def test_diy_never_requires_organization(self):
        from tempfile import TemporaryDirectory
        import pulumi_setup
        client = Pulumi('pulumi', 'private', 'file:///tmp/state')
        def command(*args):
            if args[0] == 'whoami': return json.dumps({'user': 'person'})
            if args[0] == 'org': raise AssertionError('DIY must not discover organization')
            if args[0] == 'config': return '{}'
            return '{}'
        with TemporaryDirectory() as tmp, patch.object(pulumi_setup, 'LOCAL', Path(tmp)), patch.object(client, 'run', side_effect=command):
            client.select(lambda label, value: value)
        self.assertEqual('production', client.stack)

class CollectResumeTests(unittest.TestCase):
    def test_collect_preserves_partial_legacy_and_existing_cloudflare_settings(self):
        from setup_cloud import collect
        previous = build_spec(plan())
        previous.update(tlsMode='cloudflare', cloudflare=dict(SETTINGS, recordId='record', acmeEmail='saved@example.org'))
        do_client = Mock()
        inventories = {'/kubernetes/clusters': [plan(True)['cluster']], '/domains': [{'name': 'example.org'}], '/certificates': []}
        do_client.list.side_effect = lambda path, key: inventories[path]
        cf_client = Mock()
        cf_client.request.return_value = {'result': {'status': 'active'}}
        cf_client.list.side_effect = lambda path, **kwargs: ([{'id': 'zone-id', 'name': 'example.org'}] if path == '/zones' else
                                                           [{'id': 'record', 'name': 'maven.example.org', 'type': 'A'}])
        defaults = {'DOKS_CLUSTER_NAME': 'maven', 'REPOSILITE_HOSTNAME': 'maven.example.org'}
        with patch('setup_cloud.ask', side_effect=lambda label, default: default) as ask:
            current = collect(do_client, defaults, cf_client, previous)
        self.assertEqual('example.org', current['domain'])
        self.assertIsNone(current['certificate'])
        self.assertEqual(previous['cloudflare'], current['cloudflare'])
        self.assertFalse(any(c.args[0].startswith('Import') for c in ask.call_args_list))
        state = {'deployment': {'resources': [
            {'custom': True, 'id': plan(True)['cluster']['id'], 'type': 'digitalocean:index/kubernetesCluster:KubernetesCluster'},
            {'custom': True, 'id': 'example.org', 'type': 'digitalocean:index/domain:Domain'},
            {'custom': True, 'id': 'zone-id/record', 'type': 'cloudflare:index/dnsRecord:DnsRecord'}]}}
        pulumi = Pulumi('pulumi', 'private', 'file:///tmp/state')
        with patch.object(pulumi, 'run', return_value=json.dumps(state)):
            pulumi.verify_ownership(current)
        migrated = build_spec(current, previous)
        self.assertEqual(previous['domain'], migrated['domain'])
        self.assertEqual(previous['cluster'], migrated['cluster'])
        self.assertIsNone(migrated['certificate'])

    def test_new_cloudflare_collect_does_not_read_do_domain_or_certificates(self):
        from setup_cloud import collect
        do_client = Mock()
        do_client.list.return_value = [plan(True)['cluster']]
        cf_client = Mock()
        cf_client.request.return_value = {'result': {'status': 'active'}}
        cf_client.list.side_effect = lambda path, **kwargs: [{'id': 'zone-id', 'name': 'example.org'}] if path == '/zones' else []
        with patch('setup_cloud.ask', side_effect=lambda label, default: default):
            current = collect(do_client, {'DOKS_CLUSTER_NAME': 'maven'}, cf_client)
        self.assertEqual('maven.example.org', current['hostname'])
        do_client.list.assert_called_once_with('/kubernetes/clusters', 'kubernetes_clusters')

class FirstPreviewTests(unittest.TestCase):
    def test_unknown_helm_status_defers_gateway_service_read(self):
        import pulumi
        from pulumi.runtime import Mocks, set_mocks
        from pulumi.runtime.stack import wait_for_rpcs
        from pulumi.runtime.rpc import UNKNOWN
        import resources
        seen = []
        class Provider(Mocks):
            def new_resource(self, args):
                seen.append(args)
                outputs = dict(args.inputs)
                if args.typ.endswith(':KubernetesCluster'):
                    outputs['kubeConfigs'] = [{'rawConfig': 'existing-private-kubeconfig'}]
                if args.typ == 'kubernetes:helm.sh/v3:Release':
                    outputs['status'] = UNKNOWN
                if args.typ == 'kubernetes:core/v1:Service':
                    # The SDK sends the engine an unknown ID; the engine cannot
                    # issue a provider read for a concrete Service in preview.
                    if args.resource_id != UNKNOWN:
                        raise AssertionError('First preview must not read a concrete gateway Service')
                    outputs['status'] = UNKNOWN
                return args.name + '-id', outputs
            def call(self, args):
                raise AssertionError('Unknown gateway status must not trigger an invoke')
        async def execute():
            set_mocks(Provider(), project='apexfission-maven', stack='preview', preview=True)
            current = plan(True)
            current.update(tlsMode='cloudflare', cloudflare=SETTINGS)
            outputs = resources.build(build_spec(current))
            await outputs['clusterId'].future()
            await wait_for_rpcs()
        with patch.dict(os.environ, CLOUDFLARE_API_TOKEN='private-token'):
            asyncio.run(execute())
        self.assertTrue(any(r.typ == 'kubernetes:helm.sh/v3:Release' for r in seen))

class MissingManagedResourceTests(unittest.TestCase):
    def test_preview_refuses_recreation_of_managed_namespace_but_allows_initial_create(self):
        from pulumi_setup import preview_changes
        urn = 'urn:pulumi:test::apexfission-maven::kubernetes:core/v1:Namespace::edge-namespace'
        events = '\n'.join(json.dumps(e) for e in [
            {'resourcePreEvent': {'metadata': {'op': 'create', 'urn': urn}}},
            {'summaryEvent': {'resourceChanges': {'create': 1}}}])
        with self.assertRaisesRegex(RuntimeError, 'Previously managed'):
            preview_changes(events, {urn})
        self.assertEqual({'create': 1}, preview_changes(events))

    def test_ownership_records_managed_resources_before_refresh(self):
        current = plan(True)
        state = {'deployment': {'resources': [
            {'custom': True, 'id': 'namespace-id', 'urn': 'namespace', 'type': 'kubernetes:core/v1:Namespace'},
            {'custom': True, 'external': True, 'id': 'read-id', 'urn': 'read', 'type': 'kubernetes:core/v1:Service'}]}}
        client = Pulumi('pulumi', 'private', 'file:///tmp/state')
        with patch.object(client, 'run', return_value=json.dumps(state)):
            client.verify_ownership(current)
        self.assertEqual({'namespace'}, client.managed_urns)

class UnmanagedRecordStateTests(unittest.TestCase):
    def test_config_record_id_does_not_grant_adoption_without_stack_ownership(self):
        current = dict(plan(), cloudflare=dict(SETTINGS, recordId='record'))
        client = Pulumi('pulumi', 'private', 'file:///tmp/state')
        with patch.object(client, 'run', return_value=json.dumps({'deployment': {'resources': []}})):
            with self.assertRaisesRegex(ValueError, 'not owned'):
                client.verify_ownership(current)
