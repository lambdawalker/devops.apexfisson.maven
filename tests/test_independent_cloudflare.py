"""Offline tests for the Cloudflare-only entry point."""
from contextlib import ExitStack
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import setup_cloudflare as cf

ZONE = {'id': 'zone-id', 'name': 'example.org'}
VALUES = {'hostname': 'maven.example.org', 'ip': '192.0.2.10', 'zone': 'example.org'}
RECORD = {'id': 'record-id', 'name': VALUES['hostname'], 'type': 'A',
          'content': VALUES['ip'], 'proxied': False, 'ttl': 300}


class CloudflareOnlyTests(unittest.TestCase):
    def execute(self, inventories, *, approved=True, response=None, values=None):
        self.reader = Mock()
        self.reader.list.side_effect = [[ZONE], *inventories]
        self.writer = Mock()
        self.writer.request.return_value = response if response is not None else {'result': RECORD}
        with ExitStack() as stack:
            self.credentials = stack.enter_context(patch.object(cf.setup, 'credentials', return_value='cf-only-token'))
            stack.enter_context(patch.object(cf, 'Cloudflare', return_value=self.reader))
            self.api = stack.enter_context(patch.object(cf.setup, 'Api', return_value=self.writer))
            stack.enter_context(patch.object(cf.setup, 'load_settings', return_value={}))
            self.fields = stack.enter_context(patch.object(cf.setup, 'fields', return_value=values or VALUES))
            self.approve = stack.enter_context(patch.object(cf.setup, 'approve', return_value=approved))
            self.save = stack.enter_context(patch.object(cf.setup, 'save_settings'))
            stack.enter_context(patch.object(cf.ui, 'say'))
            self.args = SimpleNamespace(plain=True)
            cf.run(self.args)

    def test_create_requires_only_cloudflare_and_verifies_dns_only(self):
        self.execute([[], [], [RECORD]])
        self.credentials.assert_called_once_with(self.args, 'cloudflare')
        self.api.assert_called_once_with('cloudflare', 'cf-only-token')
        self.writer.request.assert_called_once_with('POST', '/zones/zone-id/dns_records',
            {'type': 'A', 'name': VALUES['hostname'], 'content': VALUES['ip'], 'proxied': False, 'ttl': 1})
        self.approve.assert_called_once()
        self.save.assert_called_once_with(hostname=VALUES['hostname'], ip=VALUES['ip'],
                                          zone_id='zone-id', record_id='record-id')
        fields = self.fields.call_args.args[1]
        self.assertEqual('maven.apexfission.com', next(f.default for f in fields if f.key == 'hostname'))

    def test_existing_record_update_is_reviewed_and_metadata_preserved(self):
        old = dict(RECORD, content='192.0.2.20', proxied=True, comment='Keep me', tags=['owner:test'])
        self.execute([[old], [old], [RECORD]])
        method, path, payload = self.writer.request.call_args.args
        self.assertEqual(('PUT', '/zones/zone-id/dns_records/record-id'), (method, path))
        self.assertFalse(payload['proxied'])
        self.assertEqual(300, payload['ttl'])
        self.assertEqual('Keep me', payload['comment'])
        self.assertEqual(['owner:test'], payload['tags'])
        self.assertIn('192.0.2.20', self.approve.call_args.args[1])
        self.assertIn('192.0.2.10', self.approve.call_args.args[1])

    def test_unchanged_record_does_not_write(self):
        self.execute([[RECORD], [RECORD]])
        self.writer.request.assert_not_called()
        self.approve.assert_not_called()
        self.save.assert_called_once()

    def test_conflicting_records_refused(self):
        for records in ([dict(RECORD, type='CNAME')], [dict(RECORD, type='AAAA')],
                        [dict(RECORD, type='TXT')], [RECORD, dict(RECORD, id='other')]):
            with self.subTest(records=records), self.assertRaisesRegex(ValueError, 'conflicting'):
                self.execute([records])
            self.writer.request.assert_not_called()
            self.save.assert_not_called()

    def test_only_exact_hostname_records_count(self):
        unrelated = dict(RECORD, name='unrelated.example.org', type='CNAME')
        self.execute([[unrelated], [unrelated], [RECORD, unrelated]])
        self.assertEqual('POST', self.writer.request.call_args.args[0])

    def test_cancel_makes_no_change(self):
        self.execute([[]], approved=False)
        self.writer.request.assert_not_called()
        self.save.assert_not_called()

    def test_review_race_fails_before_write(self):
        with self.assertRaisesRegex(ValueError, 'changed during review'):
            self.execute([[], [RECORD]])
        self.writer.request.assert_not_called()
        self.save.assert_not_called()

    def test_failed_verification_does_not_save_success(self):
        with self.assertRaisesRegex(RuntimeError, 'verification failed'):
            self.execute([[], [], [dict(RECORD, proxied=True)]])
        self.save.assert_not_called()

    def test_unexpected_updated_record_id_is_not_accepted(self):
        old = dict(RECORD, proxied=True)
        with self.assertRaisesRegex(RuntimeError, 'expected record ID'):
            self.execute([[old], [old]], response={'result': dict(RECORD, id='different')})
        self.save.assert_not_called()

    def test_validation_rejects_wrong_zone_and_ipv6(self):
        values = dict(VALUES, hostname='maven.unrelated.org', ip='2001:db8::1')
        self.assertEqual({'zone', 'ip'}, set(cf.validate(values, [ZONE])))
        for host in ('https://maven.example.org', 'maven.example.org/path', '-bad.example.org',
                     'maven.example.org.', '*.example.org', ' maven.example.org'):
            self.assertIn('hostname', cf.validate(dict(VALUES, hostname=host), [ZONE]))


if __name__ == '__main__':
    unittest.main()
