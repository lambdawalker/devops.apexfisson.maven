from contextlib import contextmanager
import threading
import unittest
from unittest.mock import patch

from scripts import setup_ui as ui


class PlainTests(unittest.TestCase):
    def test_default_and_hidden(self):
        with patch('builtins.input', return_value=''):
            self.assertEqual(ui.ask('Name', 'default'), 'default')
        with patch.object(ui.getpass, 'getpass', return_value='secret'):
            self.assertEqual(ui.hidden('Token'), 'secret')

    def test_hidden_refuses_echo(self):
        with patch.object(ui.getpass, 'getpass', side_effect=ui.getpass.GetPassWarning):
            with self.assertRaises(ui.SetupError):
                ui.hidden('Token')


class UITests(unittest.IsolatedAsyncioTestCase):
    async def wait_for(self, pilot, predicate):
        for _ in range(100):
            if predicate():
                return
            await pilot.pause(0.01)
        self.fail('Timed out waiting for UI')

    async def test_worker_status_and_prompt(self):
        answers = []
        def flow():
            ui.stage('tokens')
            answers.append(ui.hidden('Token'))
            answers.append(ui.ask('Default', 'yes'))
            ui.say('[literal] Safe output')
            ui.skip('github')
            ui.stage('pulumi')
        app = ui.create_app(flow, ['tokens', 'github', 'pulumi', 'save'])
        from textual.widgets import Input, RichLog, ProgressBar
        async with app.run_test() as pilot:
            await self.wait_for(pilot, lambda: bool(app.screen.query(Input)))
            self.assertTrue(app.screen.query_one(Input).password)
            await pilot.press('s', 'e', 'c', 'r', 'e', 't', 'enter')
            await self.wait_for(pilot, lambda: bool(app.screen.query(Input)) and not app.screen.query_one(Input).password)
            self.assertEqual(app.screen.query_one(Input).value, 'yes')
            await pilot.press('enter')
            await self.wait_for(pilot, lambda: app.done)
            self.assertEqual(answers, ['secret', 'yes'])
            self.assertEqual(app.result_code, 0)
            self.assertEqual(app.statuses, {'tokens': 'complete', 'github': 'skipped', 'pulumi': 'complete', 'save': 'pending'})
            self.assertEqual(app.query_one(ProgressBar).progress, 2)
            log = '\n'.join(str(line) for line in app.query_one(RichLog).lines)
            self.assertNotIn('secret', log)
            await pilot.press('enter')
        self.assertIsInstance(ui._backend, ui.PlainBackend)

    async def test_logged_stdout_panel_and_delete_prompt(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from textual.widgets import Input, RichLog
        with TemporaryDirectory() as tmp:
            def flow():
                print('Live stdout in panel')
                ui.say('Wizard status')
            app = ui.create_app(lambda: ui.logged(flow, directory=Path(tmp)))
            async with app.run_test() as pilot:
                await self.wait_for(pilot, lambda: bool(app.screen.query(Input)))
                self.assertIn('Delete the debugging log?', app.prompt_screen.label)
                self.assertEqual(app.screen.query_one(Input).value, 'no')
                app.screen.query_one(Input).value = 'yes'
                await pilot.press('enter')
                await self.wait_for(pilot, lambda: app.done)
                output = '\n'.join(str(line) for line in app.query_one(RichLog).lines)
                self.assertIn('Live stdout in panel', output)
                self.assertIn('Debug log deleted.', output)
                self.assertEqual(app.result_code, 0)
            self.assertEqual(list(Path(tmp).glob('*.log')), [])

    async def test_logged_failure_retains_diagnostic(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            def flow():
                ui.stage('pulumi')
                print('Diagnostic before failure')
                raise ui.SetupError('Pulumi failed')
            app = ui.create_app(lambda: ui.logged(flow, directory=Path(tmp)))
            async with app.run_test() as pilot:
                await self.wait_for(pilot, lambda: app.done)
                self.assertEqual(app.result_code, 1)
                self.assertIsNone(app.prompt_screen)
            text = next(Path(tmp).glob('*.log')).read_text()
            self.assertIn('Diagnostic before failure', text)
            self.assertIn('Pulumi failed', text)

    async def test_cancel_pending_input(self):
        proceeded = []
        def flow():
            ui.stage('tokens')
            ui.hidden('Token')
            proceeded.append(True)
        app = ui.create_app(flow)
        from textual.widgets import Input
        async with app.run_test() as pilot:
            await self.wait_for(pilot, lambda: bool(app.screen.query(Input)))
            await pilot.press('ctrl+c')
            await self.wait_for(pilot, lambda: app.done)
            self.assertEqual(app.result_code, 130)
            self.assertEqual(proceeded, [])
            self.assertEqual(app.statuses['tokens'], 'cancelled')

    async def test_cancel_busy_waits_then_stops(self):
        entered, release = threading.Event(), threading.Event()
        proceeded = []
        def flow():
            ui.stage('provision')
            entered.set()
            release.wait(5)
            ui.stage('save')
            proceeded.append(True)
        app = ui.create_app(flow)
        async with app.run_test() as pilot:
            await self.wait_for(pilot, entered.is_set)
            await pilot.press('ctrl+c')
            self.assertFalse(app.done)
            release.set()
            await self.wait_for(pilot, lambda: app.done)
            self.assertEqual(app.result_code, 130)
            self.assertEqual(proceeded, [])

    async def test_failure_redacts_exception(self):
        def flow():
            ui.stage('github')
            raise ValueError('token=TOP_SECRET')
        app = ui.create_app(flow)
        from textual.widgets import RichLog
        async with app.run_test() as pilot:
            await self.wait_for(pilot, lambda: app.done)
            self.assertEqual(app.result_code, 1)
            self.assertEqual(app.statuses['github'], 'failed')
            self.assertNotIn('TOP_SECRET', '\n'.join(str(line) for line in app.query_one(RichLog).lines))

    async def test_terminal_handoff(self):
        result = []
        operation_threads = []
        def operation():
            operation_threads.append(threading.get_ident())
            return 'terminal-result'
        app = ui.create_app(lambda: result.append(ui.terminal(operation)))
        ui_thread = threading.get_ident()
        handoff = []
        @contextmanager
        def suspend():
            handoff.append(('enter', threading.get_ident()))
            yield
            handoff.append(('exit', threading.get_ident()))
        with patch.object(app, 'suspend', suspend):
            async with app.run_test() as pilot:
                await self.wait_for(pilot, lambda: app.done)
                self.assertEqual(app.result_code, 0)
                self.assertEqual(result, ['terminal-result'])
        self.assertEqual(handoff, [('enter', ui_thread), ('exit', ui_thread)])
        self.assertEqual(operation_threads, [ui_thread])

    async def test_terminal_keyboard_interrupt_cancels(self):
        handoff = []
        def interrupt():
            raise KeyboardInterrupt()
        app = ui.create_app(lambda: ui.terminal(interrupt))
        @contextmanager
        def suspend():
            handoff.append('enter')
            try:
                yield
            finally:
                handoff.append('exit')
        with patch.object(app, 'suspend', suspend):
            async with app.run_test() as pilot:
                await self.wait_for(pilot, lambda: app.done)
                self.assertEqual(app.result_code, 130)
        self.assertEqual(handoff, ['enter', 'exit'])

    async def test_safe_failure_shows_intentional_diagnostic(self):
        def flow():
            ui.stage('github')
            raise ui.SetupError('Authenticate with GitHub before continuing.')
        app = ui.create_app(flow)
        from textual.widgets import RichLog
        async with app.run_test() as pilot:
            await self.wait_for(pilot, lambda: app.done)
            self.assertEqual(app.result_code, 1)
            log = '\n'.join(str(line) for line in app.query_one(RichLog).lines)
            self.assertIn('Authenticate with GitHub before continuing.', log)
