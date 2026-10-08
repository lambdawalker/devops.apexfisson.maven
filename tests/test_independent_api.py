"""Offline provider error diagnostics; no cloud requests."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import independent_setup as common


class ApiErrorTests(unittest.TestCase):
    def failure(self, body, status=422, method='POST'):
        api = common.Api('digitalocean', 'test-private-key')
        stream = io.BytesIO(body)
        api.opener = Mock()
        api.opener.open.side_effect = HTTPError(api.base, status, 'error', {}, stream)
        with patch.object(common.ui, 'say'):
            if status == 404 and method == 'GET':
                return api.request(method, '/kubernetes/clusters')
            with self.assertRaises(common.ui.SetupError) as caught:
                api.request(method, '/kubernetes/clusters')
        api.opener.open.assert_called_once()
        return str(caught.exception)

    def test_validation_reason_is_visible(self):
        message = self.failure(json.dumps({'message': 'Region is unavailable', 'id': 'unprocessable_entity'}).encode())
        self.assertIn('HTTP 422', message)
        self.assertIn('Region is unavailable', message)
        self.assertNotIn('check access', message)

    def test_only_message_is_shown_and_credentials_are_redacted(self):
        message = self.failure(json.dumps({'message': 'Rejected test-private-key and Bearer another-key',
                                          'debug': 'private response details'}).encode())
        self.assertNotIn('test-private-key', message)
        self.assertNotIn('another-key', message)
        self.assertNotIn('private response details', message)
        self.assertIn('[REDACTED]', message)

    def test_malformed_or_unexpected_body_has_safe_fallback(self):
        for body in (b'<html>private body</html>', b'[]', b'{"message": {"secret": "hidden"}}', b'x' * 10000):
            with self.subTest(body=body[:20]):
                message = self.failure(body)
                self.assertIn('HTTP 422', message)
                self.assertNotIn('private body', message)
                self.assertNotIn('hidden', message)

    def test_missing_get_remains_absent(self):
        self.assertIsNone(self.failure(b'{}', 404, 'GET'))

    def test_permission_failure_gives_scope_guidance(self):
        self.assertIn('permissions', self.failure(b'{}', 403))


if __name__ == '__main__':
    unittest.main()
