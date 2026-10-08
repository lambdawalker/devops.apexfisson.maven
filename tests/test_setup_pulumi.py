"""Offline Pulumi setup tests; no cloud account or CLI login required."""
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'infrastructure'))
UUID = '11111111-1111-4111-8111-111111111111'


def plan(existing=False):
    cluster = {'id': UUID, 'name': 'maven', 'region': 'nyc1', 'version': '1.35.2-do.0',
               'node_pools': [{'name': 'maven', 'size': 's-2vcpu-4gb', 'count': 1}],
               'ha': False, 'auto_upgrade': True, 'surge_upgrade': True}
    cert = {'id': 'rotating-uuid', 'name': 'maven-tls', 'type': 'lets_encrypt',
            'dns_names': ['maven.example.org'], 'state': 'verified'}
    return {'cluster': cluster if existing else None,
            'cluster_body': None if existing else cluster,
            'certificate': cert if existing else None, 'certificate_name': 'maven-tls',
            'domain': 'example.org', 'create_domain': not existing,
            'hostname': 'maven.example.org'}


class SpecTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / 'scripts/pulumi_setup.py').exists(), 'Pulumi adapter missing')
        self.mod = importlib.import_module('pulumi_setup')

    def test_import_uses_stable_certificate_name_and_preserves_cluster(self):
        spec = self.mod.build_spec(plan(True))
        self.assertEqual(UUID, spec['cluster']['importId'])
        self.assertEqual('maven-tls', spec['certificate']['importId'])
        self.assertEqual('example.org', spec['domain']['importId'])
        self.assertTrue(spec['cluster']['preserveImported'])

    def test_rerun_keeps_declared_configuration_not_cloud_defaults(self):
        previous = self.mod.build_spec(plan())
        previous['cluster']['properties']['nodePool']['nodeCount'] = 2
        self.assertEqual(previous, self.mod.build_spec(plan(True), previous))
        changed = plan(True)
        changed['certificate_name'] = 'other-certificate'
        with self.assertRaisesRegex(ValueError, 'stack'):
            self.mod.build_spec(changed, previous)

    def test_custom_certificate_is_read_only(self):
        current = plan(True)
        current['certificate']['type'] = 'custom'
        current['domain'] = None
        spec = self.mod.build_spec(current)
        self.assertEqual('external', spec['certificate']['mode'])
        self.assertIsNone(spec['domain'])

    def test_previews_reject_destruction_and_missing_summary(self):
        for op in ('delete', 'replace', 'create-replacement', 'delete-replaced', 'import-replacement'):
            events = [{'resourcePreEvent': {'metadata': {'op': op, 'urn': 'resource'}}},
                      {'summaryEvent': {'resourceChanges': {op: 1}}}]
            with self.assertRaises(RuntimeError):
                self.mod.preview_changes('\n'.join(json.dumps(e) for e in events))
        with self.assertRaises(RuntimeError):
            self.mod.preview_changes('{}')
        changes = self.mod.preview_changes(json.dumps({'summaryEvent': {
            'resourceChanges': {'create': 1, 'same': 2, 'import': 1}}}))
        self.assertEqual(1, changes['import'])

    def test_cli_redacts_errors_and_credentials_only_go_in_environment(self):
        client = self.mod.Pulumi('pulumi', 'do-secret', 'https://api.pulumi.com')
        with patch.object(self.mod.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 1, 'do-secret', 'do-secret')) as run:
            with self.assertRaises(RuntimeError) as error:
                client.run('preview')
        self.assertNotIn('do-secret', str(error.exception))
        self.assertNotIn('do-secret', run.call_args.args[0])
        self.assertEqual('do-secret', run.call_args.kwargs['env']['DIGITALOCEAN_TOKEN'])
        self.assertNotIn('GH_TOKEN', run.call_args.kwargs['env'])
        self.assertEqual('true', run.call_args.kwargs['env']['PULUMI_ENABLE_STREAMING_JSON_PREVIEW'])

    def test_cancel_does_not_run_up_or_write_github(self):
        client = self.mod.Pulumi('pulumi', 'do-secret', 'https://api.pulumi.com')
        client.stack = 'owner/apexfission-maven/production'
        with patch.object(client, 'run', return_value=json.dumps({'summaryEvent': {
                'resourceChanges': {'create': 3}}})) as run, \
                patch('builtins.input', return_value=''), \
                patch('sys.stdout', new_callable=io.StringIO), tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(client.apply(self.mod.build_spec(plan()), Path(tmp)))
        self.assertFalse(any(c.args[0] == 'up' for c in run.call_args_list))

    def test_new_stack_does_not_refresh_nonexistent_deployment_config(self):
        client = self.mod.Pulumi('pulumi', 'do-secret', 'https://api.pulumi.com')
        responses = {
            'whoami': json.dumps({'user': 'person', 'organizations': ['owner']}),
            'stack': json.dumps({'deployment': {}}),
            'config': '{}', 'org': 'owner',
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(self.mod, 'LOCAL', Path(tmp)), \
                patch.object(client, 'run', side_effect=lambda *a: responses[a[0]]) as run, \
                patch('sys.stdout', new_callable=io.StringIO):
            self.assertIsNone(client.select(lambda label, default: default))
        self.assertFalse(any(c.args[:2] == ('config', 'refresh') for c in run.call_args_list))

    def test_state_mismatch_refuses_recreating_missing_cluster(self):
        client = self.mod.Pulumi('pulumi', 'do-secret', 'https://api.pulumi.com')
        state = {'deployment': {'resources': [{'custom': True, 'id': UUID,
                 'type': 'digitalocean:index/kubernetesCluster:KubernetesCluster'}]}}
        with patch.object(client, 'run', return_value=json.dumps(state)):
            with self.assertRaisesRegex(ValueError, 'discovery'):
                client.verify_ownership(plan())

    def test_login_is_interactive_and_uses_selected_backend(self):
        client = self.mod.Pulumi('pulumi', 'do-secret', 'https://api.pulumi.com')
        with patch.object(self.mod.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run, \
                patch('sys.stdout', new_callable=io.StringIO):
            client.login()
        self.assertEqual(['pulumi', 'login', 'https://api.pulumi.com'], run.call_args.args[0])
        self.assertNotIn('capture_output', run.call_args.kwargs)
        self.assertNotIn('stdin', run.call_args.kwargs)
        self.assertNotIn('DIGITALOCEAN_TOKEN', run.call_args.kwargs['env'])

    def test_login_failure_stops_before_stack_selection(self):
        with patch.object(self.mod.shutil, 'which', return_value='pulumi'), \
                patch.object(self.mod.importlib.util, 'find_spec', return_value=object()), \
                patch.object(self.mod.Pulumi, 'login', side_effect=RuntimeError('Login failed')), \
                patch.object(self.mod.Pulumi, 'select') as select:
            with self.assertRaisesRegex(RuntimeError, 'Login failed'):
                self.mod.prepare('do-secret', lambda label, default: default)
        select.assert_not_called()

    def test_login_nonzero_reports_failure(self):
        client = self.mod.Pulumi('pulumi', 'do-secret', 'file:///tmp/state')
        with patch.object(self.mod.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), \
                patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaisesRegex(RuntimeError, 'login failed'):
                client.login()

    def test_apply_uses_saved_plan_and_only_safe_outputs(self):
        client = self.mod.Pulumi('pulumi', 'do-secret', 'https://api.pulumi.com')
        client.stack = 'owner/apexfission-maven/production'
        outputs = {'clusterId': UUID, 'hostname': 'maven.example.org', 'certificateName': 'maven-tls'}
        def command(*args, **kwargs):
            if args[0] == 'preview':
                return json.dumps({'summaryEvent': {'resourceChanges': {'create': 3}}})
            if args[:2] == ('stack', 'output'):
                return json.dumps(outputs)
            return ''
        with patch.object(client, 'run', side_effect=command) as run, \
                patch('builtins.input', return_value='yes'), \
                patch('sys.stdout', new_callable=io.StringIO), tempfile.TemporaryDirectory() as tmp:
            values = client.apply(self.mod.build_spec(plan()), Path(tmp))
        self.assertEqual(UUID, values['DOKS_CLUSTER_ID'])
        preview = next(c.args for c in run.call_args_list if c.args[0] == 'preview')
        up = next(c.args for c in run.call_args_list if c.args[0] == 'up')
        self.assertEqual(preview[preview.index('--save-plan') + 1], up[up.index('--plan') + 1])
        self.assertNotIn('--show-secrets', str(run.call_args_list))

    def cloudflare_apply(self, existing=False, answers=('yes', 'yes'), fail_bootstrap=False):
        client = self.mod.Pulumi('pulumi', 'secret', 'https://api.pulumi.com')
        client.stack = 'owner/apexfission-maven/production'
        urn = 'urn:pulumi:production::apexfission-maven::digitalocean:index/kubernetesCluster:KubernetesCluster::cluster'
        if existing:
            client.managed_urns.add(urn)
        spec = self.mod.build_spec(plan())
        spec.update(tlsMode='cloudflare', domain=None, certificate=None, projectId='project')
        def run(*args):
            if args[0] == 'preview':
                return json.dumps({'summaryEvent': {'resourceChanges': {'create': 2}}})
            if args[0] == 'up' and fail_bootstrap:
                raise RuntimeError('bootstrap failed')
            if args[:2] == ('stack', 'export'):
                return json.dumps({'deployment': {'resources': [
                    {'custom': True, 'id': UUID, 'urn': urn}]}})
            if args[:2] == ('stack', 'output'):
                return json.dumps({'clusterId': UUID, 'hostname': 'maven.example.org', 'tlsMode': 'cloudflare'})
            return ''
        with patch.object(client, 'run', side_effect=run) as commands, \
                patch.object(self.mod.ui, 'supports_forms', return_value=False), \
                patch.object(self.mod.ui, 'ask', side_effect=answers), \
                patch('sys.stdout', new_callable=io.StringIO), tempfile.TemporaryDirectory() as tmp:
            if fail_bootstrap:
                with self.assertRaisesRegex(RuntimeError, 'bootstrap failed'):
                    client.apply(spec, Path(tmp))
                result = None
            else:
                result = client.apply(spec, Path(tmp))
        return result, [call.args for call in commands.call_args_list], client, urn

    def test_fresh_cloudflare_uses_two_separately_saved_reviewed_plans(self):
        result, calls, client, urn = self.cloudflare_apply()
        operations = [c for c in calls if c[0] in ('preview', 'up')]
        self.assertEqual(['preview', 'up', 'preview', 'up'], [c[0] for c in operations])
        first, apply_first, second, apply_second = operations
        self.assertIn('--target', first)
        self.assertIn(urn, first)
        self.assertNotIn('--target', second)
        self.assertNotIn('--target-dependents', str(calls))
        self.assertEqual(first[first.index('--save-plan') + 1], apply_first[apply_first.index('--plan') + 1])
        self.assertEqual(second[second.index('--save-plan') + 1], apply_second[apply_second.index('--plan') + 1])
        self.assertNotEqual(first[first.index('--save-plan') + 1], second[second.index('--save-plan') + 1])
        self.assertIn(urn, client.managed_urns)
        self.assertEqual(UUID, result['DOKS_CLUSTER_ID'])

    def test_existing_cluster_skips_bootstrap(self):
        result, calls, _, _ = self.cloudflare_apply(existing=True, answers=('yes',))
        self.assertEqual(1, sum(c[0] == 'up' for c in calls))
        self.assertNotIn('--target', str(calls))

    def test_cancel_second_phase_retains_cluster_without_outputs(self):
        result, calls, _, _ = self.cloudflare_apply(answers=('yes', 'no'))
        self.assertIsNone(result)
        self.assertEqual(1, sum(c[0] == 'up' for c in calls))
        self.assertFalse(any(c[:2] == ('stack', 'output') for c in calls))

    def test_failed_bootstrap_never_runs_second_preview(self):
        _, calls, _, _ = self.cloudflare_apply(fail_bootstrap=True)
        self.assertEqual(1, sum(c[0] == 'preview' for c in calls))
        self.assertFalse(any(c[:2] == ('stack', 'output') for c in calls))


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / 'infrastructure/resources.py').exists(), 'Pulumi project missing')
        self.resources = importlib.import_module('resources')
        self.adapter = importlib.import_module('pulumi_setup')

    def test_import_options_protect_and_freeze_existing_resources(self):
        spec = self.adapter.build_spec(plan(True))
        options = self.resources.options(spec['cluster'])
        self.assertTrue(options.protect)
        self.assertEqual(['kubeConfigs'], self.resources.options(spec['cluster'], credentials=True).additional_secret_outputs)
        self.assertEqual(UUID, options.import_)
        self.assertEqual(['*'], options.ignore_changes)
        spec['cluster']['preserveImported'] = False
        self.assertEqual(['version'], self.resources.options(spec['cluster']).ignore_changes)

    def test_auto_upgrade_does_not_revert_provider_managed_version(self):
        spec = self.adapter.build_spec(plan())
        self.assertEqual(['version'], self.resources.options(spec['cluster']).ignore_changes)

    def test_real_sdk_resource_registration_and_no_credential_exports(self):
        import asyncio
        import pulumi
        from pulumi.runtime import Mocks, set_mocks
        seen = []
        class Provider(Mocks):
            def new_resource(self, args):
                seen.append(args)
                outputs = dict(args.inputs)
                if args.typ.endswith(':KubernetesCluster'):
                    outputs['kubeConfigs'] = [{'rawConfig': 'private-kubeconfig'}]
                return args.name + '-id', outputs
            def call(self, args):
                raise AssertionError('Unexpected cloud invoke')
        async def program():
            exports = self.resources.build(self.adapter.build_spec(plan()))
            self.assertEqual({'clusterId', 'hostname', 'certificateName'}, set(exports))
            self.assertEqual('maven.example.org', exports['hostname'])
            self.assertEqual('cluster-id', await exports['clusterId'].future())
        async def execute():
            set_mocks(Provider(), project='apexfission-maven', stack='test', preview=False)
            await program()
        asyncio.run(execute())
        self.assertEqual({'digitalocean:index/domain:Domain',
                          'digitalocean:index/certificate:Certificate',
                          'digitalocean:index/kubernetesCluster:KubernetesCluster'},
                         {r.typ for r in seen if r.typ.startswith('digitalocean:')})
        cluster = next(r for r in seen if r.typ.endswith(':KubernetesCluster'))
        self.assertFalse(cluster.inputs['destroyAllAssociatedResources'])
        self.assertEqual(1, cluster.inputs['nodePool']['nodeCount'])

    def test_certificate_precedes_cluster_and_custom_certificate_is_reference(self):
        import pulumi
        spec = self.adapter.build_spec(plan())
        with patch.object(self.resources.do, 'Domain', return_value=Mock(spec=pulumi.CustomResource)) as domain, \
                patch.object(self.resources.do, 'Certificate', return_value=Mock(spec=pulumi.CustomResource)) as certificate, \
                patch.object(self.resources.do, 'KubernetesCluster') as cluster:
            certificate.return_value.name = 'maven-tls'
            self.resources.build(spec)
        self.assertEqual([domain.return_value], certificate.call_args.kwargs['opts'].depends_on)
        self.assertEqual([certificate.return_value], cluster.call_args.kwargs['opts'].depends_on)
        current = plan(True)
        current['certificate']['type'] = 'custom'
        spec = self.adapter.build_spec(current)
        with patch.object(self.resources.do, 'Domain', return_value=Mock(spec=pulumi.CustomResource)), \
                patch.object(self.resources.do, 'Certificate', return_value=Mock(spec=pulumi.CustomResource)) as certificate, \
                patch.object(self.resources.do, 'KubernetesCluster'):
            certificate.get.return_value = Mock(spec=pulumi.CustomResource)
            certificate.get.return_value.name = 'maven-tls'
            self.resources.build(spec)
        certificate.assert_not_called()
        certificate.get.assert_called_once()


if __name__ == '__main__':
    unittest.main()
