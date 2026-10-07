"""Token cache tests use fake credentials and no provider APIs."""
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
TOKENS = {'github': 'fake-gh-token', 'digitalocean': 'fake-do-token', 'cloudflare': 'fake-cf-token'}


class TokenStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = importlib.import_module('token_store')

    def test_missing_file_never_prompts_or_requires_gpg(self):
        with tempfile.TemporaryDirectory() as tmp:
            ask, hidden = Mock(), Mock()
            self.assertEqual({}, self.store.load_tokens(Path(tmp) / 'missing.gpg', ask, hidden, Mock()))
            ask.assert_not_called()
            hidden.assert_not_called()

    def test_wrong_passphrase_three_attempts_then_normal_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tokens.gpg'
            path.write_bytes(b'encrypted')
            with patch.object(self.store, 'decrypt_tokens', side_effect=self.store.TokenStoreError('safe error')) as decrypt:
                ask, hidden, say = Mock(return_value='yes'), Mock(return_value='wrong passphrase'), Mock()
                self.assertEqual({}, self.store.load_tokens(path, ask, hidden, say))
            self.assertEqual(3, decrypt.call_count)
            self.assertEqual(3, hidden.call_count)
            self.assertEqual(2, ask.call_count)
            self.assertNotIn('wrong passphrase', str(say.call_args_list))

    def test_retry_success_and_manual_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tokens.gpg'; path.write_bytes(b'encrypted')
            with patch.object(self.store, 'decrypt_tokens', side_effect=[self.store.TokenStoreError('safe'), TOKENS]):
                self.assertEqual(TOKENS, self.store.load_tokens(path, Mock(return_value='yes'), Mock(return_value='pw'), Mock()))
            with patch.object(self.store, 'decrypt_tokens', side_effect=self.store.TokenStoreError('safe')) as decrypt:
                self.assertEqual({}, self.store.load_tokens(path, Mock(return_value='no'), Mock(return_value='pw'), Mock()))
                self.assertEqual(1, decrypt.call_count)

    def test_schema_rejects_extra_keys_missing_tokens_and_control_characters(self):
        for data in [{'version': True, 'tokens': TOKENS}, {'version': 1, 'tokens': {'github': 'x'}},
                     {'version': 1, 'tokens': {**TOKENS, 'extra': 'x'}},
                     {'version': 1, 'tokens': {**TOKENS, 'github': 'secret\nnewline'}},
                     {'version': 1, 'tokens': TOKENS, 'passphrase': 'never-store'}]:
            with self.subTest(data_keys=list(data)):
                with self.assertRaises(self.store.TokenStoreError):
                    self.store.decode_tokens(json.dumps(data).encode())

    def test_process_arguments_never_include_passphrase_or_tokens_and_failure_preserves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tokens.gpg'; path.write_bytes(b'old-encrypted')
            with patch.object(self.store.shutil, 'which', return_value='gpg'), \
                    patch.object(self.store.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'fake-gh-token')) as run:
                with self.assertRaises(self.store.TokenStoreError) as error:
                    self.store.encrypt_tokens(TOKENS, 'test passphrase only', path, overwrite=True)
            self.assertEqual(b'old-encrypted', path.read_bytes())
            self.assertNotIn('fake-gh-token', str(error.exception))
            self.assertNotIn('fake-gh-token', str(run.call_args.args[0]))
            self.assertNotIn('test passphrase only', str(run.call_args.args[0]))
            self.assertIn('--no-symkey-cache', run.call_args.args[0])
            self.assertFalse(run.call_args.kwargs.get('shell', False))
            self.assertEqual(['tokens.gpg'], [p.name for p in Path(tmp).iterdir()])

    @unittest.skipUnless(shutil.which('gpg'), 'GnuPG is not installed')
    def test_real_gpg_roundtrip_wrong_passphrase_and_no_plaintext_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'gpg-home'; home.mkdir(mode=0o700)
            path = Path(tmp) / 'cache' / 'tokens.gpg'
            with patch.dict(os.environ, GNUPGHOME=str(home)):
                self.store.encrypt_tokens(TOKENS, 'fake test passphrase Ω only', path)
                self.assertEqual(TOKENS, self.store.decrypt_tokens(path, 'fake test passphrase Ω only'))
                with self.assertRaises(self.store.TokenStoreError):
                    self.store.decrypt_tokens(path, 'wrong test passphrase')
            self.assertNotIn(b'fake-gh-token', path.read_bytes())
            self.assertEqual(['tokens.gpg'], [p.name for p in path.parent.iterdir()])
            if os.name != 'nt':
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
            with self.assertRaises(self.store.TokenStoreError):
                self.store.encrypt_tokens(TOKENS, 'fake test passphrase only', path)


class TokenIntegrationTests(unittest.TestCase):
    def test_cancel_during_encryption_keeps_existing_file(self):
        import token_store as store
        import setup_ui as ui
        cancelled = False
        def encrypt(*_):
            nonlocal cancelled
            cancelled = True
            return b'new-ciphertext'
        def check():
            if cancelled:
                raise ui.SetupCancelled()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tokens.gpg'
            path.write_bytes(b'old-ciphertext')
            with patch.object(store, '_gpg', side_effect=encrypt), patch.object(store, 'check_cancelled', side_effect=check):
                with self.assertRaises(ui.SetupCancelled):
                    store.encrypt_tokens(TOKENS, 'fake passphrase', path, overwrite=True)
            self.assertEqual(b'old-ciphertext', path.read_bytes())
            self.assertEqual(['tokens.gpg'], [p.name for p in path.parent.iterdir()])

    def test_cancel_first_github_write_stops_next_operation(self):
        import setup_environment as wizard
        import setup_ui as ui
        cancelled = False
        def command(*_, **__):
            nonlocal cancelled
            cancelled = True
            return subprocess.CompletedProcess([], 0, '', '')
        def check():
            if cancelled:
                raise ui.SetupCancelled()
        with patch.object(wizard.subprocess, 'run', side_effect=command) as run, \
                patch.object(ui, 'check_cancelled', side_effect=check):
            with self.assertRaises(ui.SetupCancelled):
                wizard.save(wizard.GitHub('fake-gh'), 'owner/repo', {'TLS_MODE': 'cloudflare'},
                            'fake-token', existing=False)
        self.assertEqual(1, run.call_count)
        self.assertIn('PUT', run.call_args.args[0])

    def test_saved_tokens_skip_prompts_but_manual_flow_still_prompts(self):
        import setup_cloud as cloud
        with patch.object(cloud, 'hidden', return_value='manual-token') as hidden:
            for label in ['GitHub', 'DigitalOcean', 'Cloudflare']:
                self.assertEqual(TOKENS[label.lower()], cloud.token(label, TOKENS))
            hidden.assert_not_called()
            self.assertEqual('manual-token', cloud.token('GitHub', {}))
            hidden.assert_called_once()

    def test_setup_uses_optional_cache_before_cloud_flow(self):
        from types import SimpleNamespace
        import setup_environment as wizard
        import setup_cloud as cloud
        for values in [TOKENS, {}]:
            args = SimpleNamespace(tokens_file=Path('fake.gpg'), github_only=False)
            with patch.object(wizard, 'load_tokens', return_value=values), patch.object(cloud, 'run') as run:
                wizard.execute(args)
            self.assertEqual(values, run.call_args.args[0].tokens)

    def test_save_mismatched_passphrases_preserves_file(self):
        import save_tokens
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tokens.gpg'; path.write_bytes(b'old-ciphertext')
            with patch.object(save_tokens.shutil, 'which', return_value='gpg'), \
                    patch.object(save_tokens.ui, 'ask', return_value='yes'), \
                    patch.object(save_tokens.ui, 'hidden', side_effect=[*TOKENS.values(), 'fake long passphrase', 'different passphrase']), \
                    patch.object(save_tokens, 'encrypt_tokens') as encrypt:
                with self.assertRaises(save_tokens.TokenStoreError):
                    save_tokens.save(path)
            encrypt.assert_not_called()
            self.assertEqual(b'old-ciphertext', path.read_bytes())


    def test_saved_login_overrides_cached_github_and_no_cloudflare_stops_provision(self):
        from types import SimpleNamespace
        import setup_cloud as cloud
        for saved_login in [False, True]:
            github = Mock()
            github.run.side_effect = ['owner', json.dumps({'full_name': 'owner/repo',
                'permissions': {'admin': True}}), '']
            args = SimpleNamespace(repo='owner/repo', saved_login=saved_login, tokens=TOKENS)
            with patch.object(cloud.shutil, 'which', return_value='gh'), \
                    patch.object(cloud, 'GitHub', return_value=github) as factory, \
                    patch.object(cloud, 'hidden', side_effect=AssertionError('Unexpected token prompt')), \
                    patch.object(cloud, 'prepare', return_value=(Mock(env={}), None)), \
                    patch.object(cloud, 'ask', return_value='no'), \
                    patch.object(cloud, 'collect') as collect, \
                    patch.object(cloud, 'provision') as provision:
                cloud.run(args)
            factory.assert_called_once_with('gh', None if saved_login else TOKENS['github'])
            collect.assert_not_called()
            provision.assert_not_called()


if __name__ == '__main__':
    unittest.main()
