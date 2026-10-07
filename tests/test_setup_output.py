import io
from pathlib import Path
import subprocess
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import setup_ui as ui


class OutputTests(unittest.TestCase):
    def test_stdout_stderr_log_and_redaction(self):
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stdout', new_callable=io.StringIO) as screen:
            def flow():
                ui.register_secrets(['test-sensitive-token'])
                print('ordinary stdout')
                print('stderr test-sensitive-token', file=sys.stderr)
                ui.say('wizard message')
            with patch('builtins.input', return_value='no'):
                ui.logged(flow, directory=Path(tmp))
            files = list(Path(tmp).glob('*.log'))
            self.assertEqual(len(files), 1)
            text = files[0].read_text()
            for output in (text, screen.getvalue()):
                self.assertIn('ordinary stdout', output)
                self.assertIn('stderr [REDACTED]', output)
                self.assertIn('wizard message', output)
                self.assertNotIn('test-sensitive-token', output)

    def test_plain_prompts_are_visible_before_input_and_not_logged(self):
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stdout', new_callable=io.StringIO) as screen:
            def input_prompt(prompt):
                sys.stdout.write(prompt)
                sys.stdout.flush()
                self.assertIn(prompt, screen.getvalue())
                return 'no'
            with patch('builtins.input', side_effect=input_prompt):
                ui.logged(lambda: ui.ask('Visible prompt'), directory=Path(tmp))
            self.assertNotIn('Visible prompt', next(Path(tmp).glob('*.log')).read_text())

    def test_success_delete_and_default_keep(self):
        for answer, remaining in [('yes', 0), ('no', 1), ('', 1)]:
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as tmp:
                with patch('builtins.input', return_value=answer), patch('sys.stdout', new_callable=io.StringIO):
                    ui.logged(lambda: ui.say('done'), directory=Path(tmp))
                self.assertEqual(len(list(Path(tmp).glob('*.log'))), remaining)

    def test_failure_and_cancel_keep_log_without_delete_prompt(self):
        for error in [ui.SetupError('intentional failure'), KeyboardInterrupt()]:
            with tempfile.TemporaryDirectory() as tmp, patch('builtins.input') as prompt:
                with patch('sys.stdout', new_callable=io.StringIO):
                    with self.assertRaises(type(error)):
                        ui.logged(lambda: (_ for _ in ()).throw(error), directory=Path(tmp))
                prompt.assert_not_called()
                self.assertEqual(len(list(Path(tmp).glob('*.log'))), 1)
        with tempfile.TemporaryDirectory() as tmp, patch('builtins.input') as prompt:
            with patch('sys.stdout', new_callable=io.StringIO):
                ui.logged(lambda: ui.cancelled('declined'), directory=Path(tmp))
            prompt.assert_not_called()
            self.assertIn('cancelled', next(Path(tmp).glob('*.log')).read_text().lower())

    def test_command_streams_before_exit_and_preserves_return_data(self):
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stdout', new_callable=io.StringIO):
            marker = Path(tmp) / 'received'
            def flow():
                original = ui._backend.say
                def receive(*values, **kwargs):
                    if 'first line' in str(values):
                        marker.touch()
                    original(*values, **kwargs)
                script = ('import pathlib,sys,time; print("first line", flush=True); '
                          'p=pathlib.Path(sys.argv[1]); end=time.monotonic()+3; '
                          '\nwhile not p.exists() and time.monotonic()<end: time.sleep(.01)\n'
                          'print("error detail",file=sys.stderr); sys.exit(0 if p.exists() else 2)')
                with patch.object(ui._backend, 'say', side_effect=receive):
                    result = ui.run_command([sys.executable, '-u', '-c', script, str(marker)], timeout=5)
                self.assertEqual(result.returncode, 0)
                self.assertIn('first line', result.stdout)
                self.assertIn('error detail', result.stderr)
            with patch('builtins.input', return_value='no'):
                ui.logged(flow, directory=Path(tmp))
            text = next(Path(tmp).glob('*.log')).read_text()
            self.assertIn('first line', text)
            self.assertIn('error detail', text)
            self.assertIn('exit 0', text)

    def test_command_redacts_stdin_and_environment_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stdout', new_callable=io.StringIO) as screen:
            def flow():
                script = ('import os,sys; print(os.environ["TEST_TOKEN"]); '
                          'print(sys.stdin.read(),file=sys.stderr); sys.exit(7)')
                result = ui.run_command([sys.executable, '-c', script],
                                        env=dict(os.environ, TEST_TOKEN='env-credential'),
                                        input='stdin-credential', timeout=5)
                self.assertEqual(result.returncode, 7)
                self.assertIn('env-credential', result.stdout)
                raise ui.SetupError('Command failed')
            with self.assertRaises(ui.SetupError):
                ui.logged(flow, directory=Path(tmp))
            for text in (screen.getvalue(), next(Path(tmp).glob('*.log')).read_text()):
                self.assertIn('exit 7', text)
                self.assertNotIn('env-credential', text)
                self.assertNotIn('stdin-credential', text)
                self.assertIn('[REDACTED]', text)

    def test_sensitive_stdout_withheld_and_timeout_retained(self):
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stdout', new_callable=io.StringIO):
            def flow():
                result = ui.run_command([sys.executable, '-c', 'print("private-state")'],
                                        timeout=5, private_stdout=True)
                self.assertIn('private-state', result.stdout)
                ui.run_command([sys.executable, '-u', '-c',
                                'import time; print("before timeout"); time.sleep(10)'], timeout=.2)
            with self.assertRaises(subprocess.TimeoutExpired):
                ui.logged(flow, directory=Path(tmp))
            text = next(Path(tmp).glob('*.log')).read_text()
            self.assertNotIn('private-state', text)
            self.assertIn('before timeout', text)
            self.assertIn('timed out', text)
