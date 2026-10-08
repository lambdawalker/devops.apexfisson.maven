"""Offline boundaries and safety checks for independently invoked setup stages."""
from contextlib import nullcontext
import argparse
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import independent_setup as common
import digitalocean_stages as stages
import setup_digitalocean_hostname as host
import setup_ui as ui


class IndependentTests(unittest.TestCase):
    def test_settings_refuse_credentials(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(common, 'SETTINGS', Path(temp) / 'settings.json'):
            common.save_settings(cluster_id='id', ip='8.8.8.8')
            common.save_settings(hostname='maven.example.com')
            self.assertEqual('id', common.load_settings()['cluster_id'])
            with self.assertRaises(ui.InputError):
                common.save_settings(token='secret')

    def test_env_token_does_not_unlock_unrelated_credentials(self):
        with patch.dict('os.environ', {'DIGITALOCEAN_TOKEN': 'selected'}), patch.object(common, 'load_tokens') as load:
            self.assertEqual('selected', common.credentials(argparse.Namespace(), 'digitalocean'))
        load.assert_not_called()

    def test_plain_empty_form_requires_confirmation(self):
        with patch.object(ui, 'supports_forms', return_value=False), patch.object(ui, 'say'), \
                patch.object(ui, 'ask', return_value='no') as ask:
            with self.assertRaises(ui.SetupCancelled):
                common.fields('Verify local admin', [])
        ask.assert_called_once()

    def test_private_bootstrap_never_applies_base_over_public_route(self):
        def run(*args, **kwargs):
            if args[3] == 'deployment':
                return ''
            if args[3] == 'ingress':
                return 'ingress/reposilite-ip'
            return ''
        kubectl = Mock(side_effect=run)
        with self.assertRaises(ui.InputError):
            stages.private_bootstrap(kubectl, {})
        self.assertFalse(any(c.args[0] == 'apply' for c in kubectl.call_args_list))

    def test_private_bootstrap_blocks_public_service_before_base(self):
        def run(*args, **kwargs):
            if args[3] == 'service':
                return json.dumps({'spec': {'type': 'LoadBalancer'}})
            return ''
        kubectl = Mock(side_effect=run)
        with self.assertRaises(ui.InputError):
            stages.private_bootstrap(kubectl, {})
        self.assertFalse(any(c.args[0] == 'apply' for c in kubectl.call_args_list))

    def test_ready_blocks_bootstrap_and_maintenance(self):
        for name in ('reposilite-bootstrap', 'reposilite-maintenance'):
            kubectl = Mock(side_effect=lambda *a, **k: name if name in a else '')
            with self.assertRaises(ui.InputError):
                stages.ready_for_public(kubectl)

    def test_ip_route_has_no_hostname_or_tls(self):
        spec = stages.ingress('reposilite-ip')['spec']
        self.assertNotIn('tls', spec)
        self.assertNotIn('host', spec['rules'][0])

    def test_hostname_route_uses_http01_and_no_cloudflare_credentials(self):
        obj = stages.ingress('reposilite', 'maven.example.com', True)
        self.assertEqual('reposilite-http01', obj['metadata']['annotations']['cert-manager.io/cluster-issuer'])
        self.assertEqual(['maven.example.com'], obj['spec']['tls'][0]['hosts'])

    def test_hostname_bad_dns_causes_no_cluster_mutation(self):
        values = {'cluster': '11111111-1111-4111-8111-111111111111',
                  'hostname': 'maven.example.com', 'ip': '8.8.8.8', 'email': 'admin@example.com'}
        with patch.object(host, 'require'), patch.object(host, 'credentials', return_value='token') as credential, \
                patch.object(host, 'load_settings', return_value={}), patch.object(host, 'fields', return_value=values), \
                patch.object(host.socket, 'getaddrinfo', return_value=[(None, None, None, None, ('1.1.1.1', 80))]), \
                patch.object(host, 'cluster_access') as access:
            with self.assertRaises(ui.InputError):
                host.execute(argparse.Namespace())
        self.assertEqual('digitalocean', credential.call_args.args[1])
        access.assert_not_called()

    def test_gateway_hostname_status_uses_actual_load_balancer_ip(self):
        ident = '11111111-1111-4111-8111-111111111111'
        service = {'metadata': {'annotations': {'kubernetes.digitalocean.com/load-balancer-id': ident}},
                   'status': {'loadBalancer': {'ingress': [{'hostname': 'maven.example.com'}]}}}
        kubectl = Mock(return_value=json.dumps(service))
        api = Mock()
        api.request.return_value = {'load_balancer': {'ip': '8.8.8.8'}}
        self.assertEqual('8.8.8.8', stages.gateway_ip(kubectl, api))
        api.request.assert_called_once_with('GET', '/load_balancers/' + ident)

    def test_cancel_admin_verification_does_not_mark_verified(self):
        kubectl = Mock()
        with patch.object(stages, 'local_console', return_value=nullcontext(8080)), \
                patch.object(stages, 'fields', side_effect=ui.SetupCancelled):
            with self.assertRaises(ui.SetupCancelled):
                stages.verify_admin(kubectl, {})
        kubectl.assert_not_called()

    def test_admin_verification_is_recorded_only_after_confirmation(self):
        kubectl = Mock()
        with patch.object(stages, 'local_console', return_value=nullcontext(8080)), \
                patch.object(stages, 'fields', return_value={}):
            stages.verify_admin(kubectl, {})
        self.assertIn('apexfission.com/bootstrap-verified=true', kubectl.call_args.args)

    def test_retained_log_never_prompts_for_deletion(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ui, 'ask') as ask, \
                patch('sys.stdout', new_callable=io.StringIO):
            ui.logged(lambda: ui.say('done'), directory=Path(temp), keep_log=True)
            self.assertEqual(1, len(list(Path(temp).glob('*.log'))))
        ask.assert_not_called()


if __name__ == '__main__':
    unittest.main()
