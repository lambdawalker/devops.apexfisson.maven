"""Offline tests: API transport boundaries are faked; provisioning logic is real."""
import importlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((Path(__file__).resolve().parents[1] / 'scripts/digitalocean.py').exists(),
                        'DigitalOcean provisioning adapter is missing')
        self.do = importlib.import_module('digitalocean')

    def test_api_failure_is_redacted_and_not_retried(self):
        secret = 'private-test-token'
        client = self.do.DigitalOcean(secret)
        with patch.object(client.opener, 'open', side_effect=HTTPError(
                'https://api.digitalocean.com/v2/kubernetes/clusters', 403,
                secret, {}, io.BytesIO(secret.encode()))) as request:
            with self.assertRaises(RuntimeError) as error:
                client.request('GET', '/kubernetes/clusters')
        self.assertNotIn(secret, str(error.exception))
        self.assertIn('403', str(error.exception))
        self.assertEqual(1, request.call_count)
        self.assertEqual('Bearer ' + secret, request.call_args.args[0].get_header('Authorization'))

    def test_pagination_stays_on_digitalocean_origin(self):
        client = self.do.DigitalOcean('private-test-token')
        with patch.object(client, 'request', side_effect=[
            {'kubernetes_clusters': [{'name': 'one'}], 'links': {'pages': {
                'next': 'https://api.digitalocean.com/v2/kubernetes/clusters?page=2'}}},
            {'kubernetes_clusters': [{'name': 'two'}]}]):
            self.assertEqual(['one', 'two'], [x['name'] for x in client.list(
                '/kubernetes/clusters', 'kubernetes_clusters')])
        with self.assertRaises(ValueError):
            client.request('GET', 'https://other.example/steal')

    def test_discovery_client_refuses_cloud_mutations(self):
        client = self.do.DigitalOcean('private-test-token')
        with patch.object(client.opener, 'open') as request:
            for method in ('POST', 'PUT', 'DELETE'):
                with self.assertRaisesRegex(ValueError, 'Pulumi'):
                    client.request(method, '/kubernetes/clusters')
        request.assert_not_called()

    def test_redirects_are_rejected(self):
        with self.assertRaises(HTTPError):
            self.do.NoRedirect().redirect_request(None, None, 302, 'redirect', {},
                                                  'https://other.example/')

    def test_duplicate_names_fail_and_version_is_numeric(self):
        with self.assertRaisesRegex(ValueError, 'multiple'):
            self.do.named([{'name': 'maven'}, {'name': 'maven'}], 'maven')
        self.assertEqual('1.35.2-do.10', self.do.latest_version([
            {'slug': '1.34.9-do.9'}, {'slug': '1.35.2-do.2'}, {'slug': '1.35.2-do.10'}]))

    def test_certificate_requires_matching_hostname_and_future_expiry(self):
        cert = {'dns_names': ['*.example.com'], 'state': 'verified',
                'not_after': '2099-01-01T00:00:00Z'}
        self.do.check_certificate(cert, 'maven.example.com')
        for hostname in ('a.maven.example.com', 'elsewhere.net'):
            with self.assertRaises(ValueError):
                self.do.check_certificate(cert, hostname)
        cert['not_after'] = '2000-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.do.check_certificate(cert, 'maven.example.com')

    def test_wait_reports_error_and_timeout(self):
        client = self.do.DigitalOcean('private-test-token')
        with patch.object(client, 'request', return_value={'kubernetes_cluster': {
                'status': {'state': 'error'}}}):
            with self.assertRaisesRegex(RuntimeError, 'error'):
                client.wait('/kubernetes/clusters/id', 'kubernetes_cluster', timeout=1)
        with patch.object(client, 'request', return_value={'certificate': {'state': 'pending'}}), \
                patch.object(self.do.time, 'monotonic', side_effect=[0, 0, 1000]), \
                patch.object(self.do.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                client.wait('/certificates/id', 'certificate', timeout=1)


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((Path(__file__).resolve().parents[1] / 'scripts/setup_cloud.py').exists(),
                        'Unified wizard is missing')
        self.wizard = importlib.import_module('setup_cloud')
        self.cluster = {'name': 'maven', 'id': '11111111-1111-4111-8111-111111111111',
                        'region': 'nyc1', 'version': '1.35.2-do.0', 'node_pools': [
                            {'size': 's-2vcpu-4gb', 'count': 1}], 'status': {'state': 'running'}}
        self.cert = {'name': 'maven-tls', 'id': 'cert-id', 'dns_names': ['maven.example.org'],
                     'state': 'verified', 'not_after': '2099-01-01T00:00:00Z'}
        self.plan = {'hostname': 'maven.example.org', 'certificate_name': 'maven-tls',
                     'certificate': None, 'domain': 'example.org', 'create_domain': True,
                     'cluster': None, 'cluster_body': {'name': 'maven', 'region': 'nyc1',
                         'version': '1.35.2-do.0', 'node_pools': [
                             {'name': 'maven', 'size': 's-2vcpu-4gb', 'count': 1}],
                         'ha': False, 'auto_upgrade': True, 'surge_upgrade': True}}

    def pulumi_client(self):
        from unittest.mock import Mock
        client = Mock()
        client.apply.return_value = {'DOKS_CLUSTER_ID': self.cluster['id'],
            'REPOSILITE_HOSTNAME': 'maven.example.org', 'DO_CERTIFICATE_NAME': 'maven-tls'}
        return client

    def test_generated_uuid_is_handed_to_github_and_secret_not_printed(self):
        client = self.pulumi_client()
        with patch.object(self.wizard, 'save') as save, patch('sys.stdout', new_callable=io.StringIO) as out:
            self.wizard.provision(None, object(), 'owner/repo', self.plan, 'do-secret', False, client)
        self.assertEqual(self.cluster['id'], save.call_args.args[2]['DOKS_CLUSTER_ID'])
        self.assertEqual('do-secret', save.call_args.args[3])
        self.assertNotIn('do-secret', out.getvalue())
        self.assertNotIn('do-secret', str(client.apply.call_args))

    def test_pulumi_failure_stops_before_github(self):
        client = self.pulumi_client()
        client.apply.side_effect = RuntimeError('Certificate failed')
        with patch.object(self.wizard, 'save') as save:
            with self.assertRaisesRegex(RuntimeError, 'retained'):
                self.wizard.provision(None, object(), 'owner/repo', self.plan, 'do-secret', False, client)
        save.assert_not_called()

    def test_cancelled_pulumi_preview_does_not_save_github(self):
        client = self.pulumi_client()
        client.apply.return_value = None
        with patch.object(self.wizard, 'save') as save:
            self.wizard.provision(None, object(), 'owner/repo', self.plan, 'do-secret', False, client)
        save.assert_not_called()

    def test_github_failure_keeps_cluster_and_reports_uuid(self):
        client = self.pulumi_client()
        with patch.object(self.wizard, 'save', side_effect=RuntimeError('GitHub failed')), \
                patch('sys.stdout', new_callable=io.StringIO) as out:
            with self.assertRaisesRegex(RuntimeError, 'retained'):
                self.wizard.provision(None, object(), 'owner/repo', self.plan, 'do-secret', False, client)
        self.assertIn(self.cluster['id'], out.getvalue())
        self.assertEqual(['verify_ownership', 'apply'], [c[0] for c in client.method_calls])

    def test_new_cluster_prompt_defaults_and_cancel(self):
        from unittest.mock import Mock
        client = Mock()
        options = {'regions': [{'slug': 'nyc1'}], 'sizes': [{'slug': 's-2vcpu-4gb'}],
                   'versions': [{'slug': '1.35.2-do.0'}]}
        client.request.return_value = {'options': options}
        client.list.side_effect = [[], [{'name': 'example.org'}], []]
        prompts = []
        def answer(label, default=''):
            prompts.append((label, default))
            return default
        with patch.object(self.wizard, 'ask', side_effect=answer):
            plan = self.wizard.collect(client, {})
        self.assertTrue(all(default for _, default in prompts))
        self.assertEqual('maven.example.org', plan['hostname'])
        self.assertEqual(1, plan['cluster_body']['node_pools'][0]['count'])
        self.assertFalse(plan['cluster_body']['ha'])
        with patch('builtins.input', return_value=''), patch('sys.stdout', new_callable=io.StringIO):
            self.assertFalse(self.wizard.review('owner/repo', plan))
        self.assertTrue(all(c.args[0] == 'GET' for c in client.request.call_args_list))

    def test_required_tokens_have_no_default(self):
        for value in ('', 'bad token', 'bad\x7ftoken'):
            with patch.object(self.wizard, 'hidden', return_value=value):
                with self.assertRaises(ValueError):
                    self.wizard.token('DigitalOcean')

    def test_rerun_defaults_use_github_cluster_and_hostname(self):
        from unittest.mock import Mock
        client = Mock()
        client.list.side_effect = [[self.cluster], [{'name': 'example.org'}], [self.cert]]
        prompts = []
        def answer(label, default=''):
            prompts.append((label, default))
            return default
        defaults = {'DOKS_CLUSTER_ID': self.cluster['id'],
                    'REPOSILITE_HOSTNAME': 'maven.example.org', 'DO_CERTIFICATE_NAME': 'maven-tls'}
        with patch.object(self.wizard, 'ask', side_effect=answer), \
                patch('sys.stdout', new_callable=io.StringIO):
            plan = self.wizard.collect(client, defaults)
        self.assertEqual(self.cluster, plan['cluster'])
        self.assertFalse(plan['create_domain'])
        self.assertEqual(['maven', 'maven.example.org', 'maven-tls'], [v for _, v in prompts])
        client.request.assert_not_called()

    def test_declining_full_wizard_never_provisions(self):
        from argparse import Namespace
        from unittest.mock import Mock
        github = Mock()
        github.run.side_effect = ['owner', json.dumps({'full_name': 'owner/repo',
                                                       'permissions': {'admin': True}}), '']
        args = Namespace(repo='owner/repo', saved_login=False)
        with patch.object(self.wizard.shutil, 'which', return_value='gh'), \
                patch.object(self.wizard, 'GitHub', return_value=github), \
                patch.object(self.wizard, 'token', side_effect=['gh-secret', 'do-secret', 'cf-secret']), \
                patch.object(self.wizard, 'DigitalOcean') as digitalocean, \
                patch.object(self.wizard, 'Cloudflare'), \
                patch.object(self.wizard, 'ask', return_value='project-id'), \
                patch.object(self.wizard, 'prepare', return_value=(Mock(env={}), None)), \
                patch.object(self.wizard, 'collect', return_value=self.plan), \
                patch.object(self.wizard, 'review', return_value=False), \
                patch.object(self.wizard, 'provision') as provision, \
                patch('sys.stdout', new_callable=io.StringIO):
            digitalocean.return_value.list.return_value = [{'id': 'project-id', 'name': 'Default', 'is_default': True}]
            self.wizard.run(args)
        provision.assert_not_called()
        self.assertFalse(any('--method' in c.args for c in github.run.call_args_list))


if __name__ == '__main__':
    unittest.main()
