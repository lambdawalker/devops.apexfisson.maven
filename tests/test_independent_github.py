"""Independent GitHub setup tests: no provider API or live writes."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import setup_github as wizard


class IndependentGitHubTests(unittest.TestCase):
    def run_wizard(self, existing=True, approved=True, admin=True, token='', settings=None, existing_values=None):
        values = {'DOKS_CLUSTER_ID': '11111111-1111-4111-8111-111111111111',
                  'REPOSILITE_HOSTNAME': 'maven.example.com', 'TLS_MODE': 'http01'}
        remote_values = existing_values or values
        calls = []
        def run(*args, **kwargs):
            calls.append((args, kwargs))
            if args[:2] == ('api', 'user'):
                return 'operator'
            if args == ('api', 'repos/owner/repo'):
                return json.dumps({'permissions': {'admin': admin}, 'full_name': 'owner/repo'})
            if args[:2] == ('api', 'repos/owner/repo/environments'):
                return 'Production\n' if existing else ''
            if args[:2] == ('variable', 'list'):
                return json.dumps([{'name': k, 'value': v} for k, v in (values if any(a[:2] == ('variable', 'set') for a, _ in calls) else remote_values).items()])
            if args[:2] == ('secret', 'list'):
                return json.dumps([{'name': 'DIGITALOCEAN_ACCESS_TOKEN'}])
            return ''
        client = Mock(run=Mock(side_effect=run))
        forms = []
        def fields(title, inputs, **kwargs):
            forms.append(inputs)
            if inputs[0].key == 'repo':
                return {'repo': 'owner/repo'}
            return dict(values, DO_CERTIFICATE_NAME='')
        credentials = Mock(side_effect=['', token])
        with patch.object(wizard.shutil, 'which', return_value='gh'), \
                patch.object(wizard.github, 'GitHub', return_value=client), \
                patch.object(wizard.github, 'confirm', return_value=approved), \
                patch.object(wizard.common, 'credentials', credentials), \
                patch.object(wizard.common, 'Api', side_effect=AssertionError('No provider API permitted')), \
                patch.object(wizard.common, 'cluster_access', side_effect=AssertionError('No cluster access permitted')), \
                patch.object(wizard.common, 'fields', side_effect=fields), \
                patch.object(wizard.common, 'load_settings', return_value=settings or {}), \
                patch.object(wizard.common, 'save_settings') as saved, \
                patch.object(wizard.ui, 'stage') as stage, \
                patch.object(wizard.ui, 'say'), patch.object(wizard.ui, 'cancelled'):
            wizard.execute(SimpleNamespace())
        return calls, credentials, saved, stage, forms

    def test_existing_environment_protection_and_secret_are_preserved(self):
        calls, credentials, saved, stage, forms = self.run_wizard()
        self.assertFalse(any(a[:3] == ('api', '--method', 'PUT') for a, _ in calls))
        self.assertFalse(any(a[:2] == ('secret', 'set') for a, _ in calls))
        self.assertEqual(['github', 'digitalocean'], [c.args[1] for c in credentials.call_args_list])
        self.assertTrue(credentials.call_args_list[-1].kwargs['optional'])
        self.assertEqual(['credentials', 'discovery', 'review', 'apply', 'verify'],
                         [c.args[0] for c in stage.call_args_list])
        saved.assert_called_once()
        self.assertEqual('http01', next(f for f in forms[1] if f.key == 'TLS_MODE').default)

    def test_local_outputs_take_precedence_over_stale_github_settings(self):
        local = {'cluster_id': '22222222-2222-4222-8222-222222222222',
                 'hostname': 'new.example.com', 'tls_mode': 'http01'}
        remote = {'DOKS_CLUSTER_ID': '11111111-1111-4111-8111-111111111111',
                  'REPOSILITE_HOSTNAME': 'old.example.com', 'TLS_MODE': 'cloudflare'}
        _, _, _, _, forms = self.run_wizard(settings=local, existing_values=remote)
        defaults = {f.key: f.default for f in forms[1]}
        self.assertEqual(local['cluster_id'], defaults['DOKS_CLUSTER_ID'])
        self.assertEqual(local['hostname'], defaults['REPOSILITE_HOSTNAME'])
        self.assertEqual('http01', defaults['TLS_MODE'])

    def test_remote_defaults_are_used_when_local_outputs_are_absent(self):
        remote = {'DOKS_CLUSTER_ID': '33333333-3333-4333-8333-333333333333',
                  'REPOSILITE_HOSTNAME': 'remote.example.com', 'TLS_MODE': 'cloudflare'}
        _, _, _, _, forms = self.run_wizard(existing_values=remote)
        defaults = {f.key: f.default for f in forms[1]}
        for key, value in remote.items():
            self.assertEqual(value, defaults[key])

    def test_new_environment_requires_and_stores_secret_using_stdin(self):
        calls, credentials, _, _, _ = self.run_wizard(existing=False, token='secret-example')
        self.assertFalse(credentials.call_args_list[-1].kwargs['optional'])
        puts = [a for a, _ in calls if a[:3] == ('api', '--method', 'PUT')]
        self.assertEqual(1, len(puts))
        writes = [(a, kw) for a, kw in calls if a[:2] == ('secret', 'set')]
        self.assertEqual(1, len(writes))
        self.assertNotIn('secret-example', writes[0][0])
        self.assertEqual('secret-example', writes[0][1]['stdin'])

    def test_declining_review_makes_no_mutations(self):
        calls, _, saved, _, _ = self.run_wizard(approved=False)
        self.assertFalse(any('set' in a or '--method' in a for a, _ in calls))
        saved.assert_not_called()

    def test_missing_admin_access_fails_before_mutations(self):
        with self.assertRaisesRegex(wizard.ui.SetupError, 'administrator'):
            self.run_wizard(admin=False)

    def test_missing_new_secret_fails(self):
        with self.assertRaisesRegex(wizard.ui.InputError, 'DigitalOcean token'):
            self.run_wizard(existing=False)

    def test_local_validation_rejects_bad_repository_and_hostname(self):
        self.assertIn('repo', wizard.validate_repository({'repo': 'https://github.com/owner/repo'}))
        errors = wizard.validate_environment({'DOKS_CLUSTER_ID': '11111111-1111-4111-8111-111111111111',
                                             'REPOSILITE_HOSTNAME': 'https://bad', 'TLS_MODE': 'http01'})
        self.assertIn('REPOSILITE_HOSTNAME', errors)


if __name__ == '__main__':
    unittest.main()
