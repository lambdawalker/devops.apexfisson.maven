from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cleanup_environment as cleanup


class CleanupTests(unittest.TestCase):
    def resource(self, kind, ident, **extras):
        return dict(type=kind, id=ident, custom=True, inputs={}, outputs={}, **extras)

    def test_known_inventory_and_shared_zone_preservation(self):
        resources = [self.resource(cleanup.CLUSTER, 'cluster-1'),
                     self.resource(cleanup.DOMAIN, 'example.com'),
                     self.resource(cleanup.DNS, 'record-1')]
        resources[-1]['inputs'] = {'zoneId': 'zone-1', 'name': 'maven.example.com'}
        plan = cleanup.inventory({'deployment': {'resources': resources}})
        self.assertEqual(plan['clusters'], ['cluster-1'])
        self.assertEqual(plan['dns'], [{'zone': 'zone-1', 'id': 'record-1'}])
        self.assertEqual(plan['preserved'], ['digitalocean:index/domain:Domain example.com'])

    def test_unknown_or_pending_resources_block_forgetting_state(self):
        for state in ({'deployment': {'pending_operations': [{}]}},
                      {'deployment': {'resources': [self.resource('aws:s3:Bucket', 'bucket')]}}):
            with self.assertRaises(cleanup.CleanupError):
                cleanup.inventory(state)

    def test_external_certificate_is_preserved(self):
        plan = cleanup.inventory({'deployment': {'resources': [
            self.resource(cleanup.CERT, 'cert-1', external=True)]}})
        self.assertEqual(plan['certificates'], [])

    def test_only_404_is_absent(self):
        client = cleanup.API('do', 'sensitive')
        for status in (401, 403, 429, 500):
            client.opener.open = Mock(side_effect=HTTPError('url', status, '', {}, None))
            with self.assertRaises(cleanup.CleanupError):
                client.request('GET', '/volumes/id')
        client.opener.open = Mock(side_effect=HTTPError('url', 404, '', {}, None))
        self.assertIsNone(client.request('GET', '/volumes/id'))
        self.assertIsNone(client.request('DELETE', '/volumes/id'))

    def test_api_refuses_arbitrary_host_and_malformed_success(self):
        client = cleanup.API('do', 'sensitive')
        with self.assertRaises(cleanup.CleanupError):
            client.request('GET', 'https://evil.test/')
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b''
        client.opener.open = Mock(return_value=response)
        with self.assertRaises(cleanup.CleanupError):
            client.request('GET', '/volumes/id')

    def test_associations_survive_cluster_disappearance(self):
        plan = cleanup.inventory({})
        plan['clusters'] = ['cluster-1']
        api = Mock()
        api.request.return_value = {'load_balancers': [{'id': 'lb-1'}],
                                    'volumes': [{'id': 'vol-1'}], 'volume_snapshots': []}
        cleanup.discover_associations(plan, api)
        api.request.return_value = None
        cleanup.discover_associations(plan, api)
        self.assertEqual(plan['volumes'], ['vol-1'])
        self.assertEqual(plan['load_balancers'], ['lb-1'])

    def test_failed_deletion_is_not_success(self):
        plan = cleanup.inventory({})
        plan['volumes'] = ['vol-1']
        api = Mock()
        api.request.side_effect = cleanup.CleanupError('HTTP 403')
        with self.assertRaises(cleanup.CleanupError):
            cleanup.delete_resources(plan, api, None, lambda message: None, timeout=0)

    def test_already_absent_cleanup_succeeds(self):
        plan = cleanup.inventory({})
        plan.update(clusters=['cluster-1'], volumes=['vol-1'])
        api = Mock()
        api.request.return_value = None
        cleanup.delete_resources(plan, api, None, lambda message: None, timeout=0)
        self.assertFalse(any(call.args[0] == 'DELETE' for call in api.request.call_args_list))


    def test_kubernetes_requires_owned_cluster_provider(self):
        resource = self.resource('kubernetes:core/v1:Namespace', 'namespace', provider='provider::id')
        cluster = self.resource(cleanup.CLUSTER, 'cluster-1', urn='cluster-urn')
        provider = self.resource('pulumi:providers:kubernetes', 'id', urn='provider',
                                 dependencies=['cluster-urn'])
        with self.assertRaises(cleanup.CleanupError):
            cleanup.inventory({'deployment': {'resources': [resource]}})
        with self.assertRaises(cleanup.CleanupError):
            cleanup.inventory({'deployment': {'resources': [resource, cluster]}})
        self.assertEqual(cleanup.inventory({'deployment': {'resources': [
            resource, cluster, provider]}})['clusters'], ['cluster-1'])

    def test_partial_retry_omits_missing_associated_ids(self):
        plan = cleanup.inventory({})
        plan.update(clusters=['cluster-1'], volumes=['gone', 'present'])
        api = Mock()
        calls = []
        def request(method, path, body=None):
            calls.append((method, path, body))
            if method == 'DELETE':
                return {}
            if path == '/kubernetes/clusters/cluster-1':
                return {} if len([c for c in calls if c[1] == path]) == 1 else None
            if path == '/volumes/present':
                return {} if len([c for c in calls if c[1] == path]) == 1 else None
            return None
        api.request.side_effect = request
        cleanup.delete_resources(plan, api, None, lambda message: None, timeout=0)
        deletion = next(c for c in calls if c[0] == 'DELETE')
        self.assertEqual(deletion[2]['volumes'], ['present'])

    def run_main(self, execute, api_failure=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stack = Mock()
            stack.exists.return_value = True
            stack.export.return_value = {'deployment': {'resources': [
                self.resource(cleanup.CLUSTER, 'cluster-1')]}}
            api = Mock()
            api.request.return_value = {'load_balancers': [], 'volumes': [], 'volume_snapshots': []}
            log = Mock()
            with patch.object(cleanup, 'ROOT', root), patch.object(cleanup, 'Stack', return_value=stack), \
                    patch.object(cleanup, 'API', return_value=api), \
                    patch.object(cleanup, 'Transcript', return_value=log), \
                    patch.dict('os.environ', {'DIGITALOCEAN_TOKEN': 'test-token'}), \
                    patch('builtins.input', return_value='DELETE isdavid/apexfission-maven/production'), \
                    patch.object(cleanup, 'delete_resources') as delete:
                if api_failure:
                    delete.side_effect = cleanup.CleanupError('permission denied')
                result = cleanup.main(['--execute'] if execute else [])
                return result, stack, delete, list(root.rglob('resources.json'))

    def test_preview_never_deletes_or_removes_stack(self):
        result, stack, delete, journals = self.run_main(False)
        self.assertEqual(result, 0)
        delete.assert_not_called()
        stack.run.assert_not_called()
        self.assertEqual(journals, [])

    def test_failed_cleanup_retains_state_and_journal(self):
        result, stack, delete, journals = self.run_main(True, api_failure=True)
        self.assertEqual(result, 1)
        delete.assert_called_once()
        stack.run.assert_not_called()
        self.assertEqual(len(journals), 1)

    def test_verified_cleanup_removes_only_selected_stack(self):
        result, stack, delete, journals = self.run_main(True)
        self.assertEqual(result, 0)
        delete.assert_called_once()
        stack.run.assert_called_once_with('stack', 'rm',
            'isdavid/apexfission-maven/production', '--yes', '--force', '--preserve-config')

if __name__ == '__main__':
    unittest.main()
