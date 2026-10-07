"""Form behavior is exercised through Textual's real headless driver."""
import sys
from contextlib import asynccontextmanager
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import setup_ui as ui


class FormTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def run_app(self, app):
        async with app.run_test(size=(100, 40)) as pilot:
            try:
                yield pilot
            finally:
                if not app.done:
                    app.action_cancel()
                    await self.wait_for(pilot, lambda: app.done)

    async def wait_for(self, pilot, predicate):
        for _ in range(100):
            if predicate():
                return
            await pilot.pause(.01)
        self.fail('Timed out waiting for form')

    async def test_defaults_validation_preserves_edits_and_output_stays_visible(self):
        from textual.widgets import Input, RichLog
        values = []
        def flow():
            ui.say('Output stays visible')
            values.append(ui.form('Configuration', [
                ui.Field('name', 'Name', 'default-name'),
                ui.Field('token', 'Token', 'private-value', secret=True),
            ]))
        app = ui.create_app(flow)
        async with self.run_app(app) as pilot:
            await self.wait_for(pilot, lambda: bool(app.query('#field-name')))
            self.assertEqual(app.query_one('#field-name', Input).value, 'default-name')
            self.assertTrue(app.query_one('#field-token', Input).password)
            self.assertTrue(app.query_one(RichLog).visible)
            app.query_one('#field-name', Input).value = ''
            await pilot.pause()
            await pilot.click('#form-proceed')
            await pilot.pause()
            self.assertFalse(app.done)
            self.assertEqual(app.query_one('#field-token', Input).value, 'private-value')
            self.assertTrue(app.query_one('#error-name').display)
            app.query_one('#field-name', Input).value = 'edited'
            await pilot.click('#form-proceed')
            await self.wait_for(pilot, lambda: app.done)
            self.assertEqual(values, [{'name': 'edited', 'token': 'private-value'}])
            self.assertEqual(app.result_code, 0)
            self.assertNotIn('private-value', '\n'.join(str(line) for line in app.query_one(RichLog).lines))

    async def test_unavailable_saved_choice_is_visible_and_requires_correction(self):
        from textual.widgets import Select
        selected = []
        app = ui.create_app(lambda: selected.append(ui.form('Saved choice', [
            ui.Field('project', 'Project', 'unavailable', choices=('available',)),
        ])))
        async with self.run_app(app) as pilot:
            await self.wait_for(pilot, lambda: bool(app.query('#field-project')) or app.done)
            self.assertFalse(app.done, 'Stale defaults must not abort the wizard')
            await pilot.pause()
            self.assertEqual(app.query_one('#field-project', Select).value, 'unavailable')
            await pilot.click('#form-proceed')
            await pilot.pause()
            self.assertTrue(app.query_one('#error-project').display)
            app.query_one('#field-project', Select).value = 'available'
            await pilot.click('#form-proceed')
            await self.wait_for(pilot, lambda: app.done)
            self.assertEqual(selected, [{'project': 'available'}])

    async def test_cancel_form_stops_flow(self):
        proceeded = []
        def flow():
            ui.form('Config', [ui.Field('name', 'Name', 'default')])
            proceeded.append(True)
        app = ui.create_app(flow)
        async with self.run_app(app) as pilot:
            await self.wait_for(pilot, lambda: bool(app.query('#form-cancel')))
            await pilot.click('#form-cancel')
            await self.wait_for(pilot, lambda: app.done)
            self.assertEqual(app.result_code, 130)
            self.assertEqual(proceeded, [])

    async def test_enter_moves_focus_and_apply_requires_button(self):
        from textual.widgets import Input
        applied = []
        def flow():
            ui.form('Inputs', [ui.Field('name', 'Name', 'default')])
            ui.confirm_action('Review plan', 'create=1', 'Apply')
            applied.append(True)
        app = ui.create_app(flow)
        async with self.run_app(app) as pilot:
            await self.wait_for(pilot, lambda: bool(app.query('#field-name')))
            await pilot.press('enter')
            await pilot.pause()
            self.assertEqual(applied, [])
            self.assertTrue(app.query('#field-name'))
            await pilot.click('#form-proceed')
            await self.wait_for(pilot, lambda: bool(app.query('#form-proceed')) and str(app.query_one('#form-proceed').label) == 'Apply')
            self.assertEqual(applied, [])
            await pilot.click('#form-proceed')
            await self.wait_for(pilot, lambda: app.done)
            self.assertEqual(applied, [True])


class FormFlowTests(unittest.TestCase):
    def setUp(self):
        import setup_forms
        self.forms = setup_forms

    def test_whitespace_only_required_fields_have_inline_errors(self):
        errors = ui.validate_fields([ui.Field('certificate', 'Certificate')], {'certificate': '   '})
        self.assertIn('certificate', errors)

    def test_credential_errors_are_attached_to_fields(self):
        errors = self.forms.validate_credentials({'repo': 'bad repo', 'github': 'bad token',
                                                  'backend': 'https://token@api.pulumi.com'})
        self.assertEqual(set(errors), {'repo', 'github', 'backend'})
        self.assertNotIn('bad token', str(errors))

    def test_saved_login_and_cloudflare_defaults(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        args = SimpleNamespace(repo='owner/repo', saved_login=True, github_only=False,
                               token_auth=False, tokens={'digitalocean': 'do-token', 'cloudflare': 'cf-token'})
        def form(title, fields, **kwargs):
            values = {f.key: f.default for f in fields}
            self.assertNotIn('github', values)
            self.assertEqual(ui.validate_fields(fields, values, kwargs['validate']), {})
            self.assertTrue(next(f for f in fields if f.key == 'cloudflare').secret)
            self.assertIsNone(next(f for f in fields if f.key == 'backend').enabled_when)
            return values
        with patch.object(ui, 'form', side_effect=form):
            self.assertTrue(self.forms.credentials(args, 'default/repo'))
        self.assertEqual(args.tokens['cloudflare'], 'cf-token')
        self.assertEqual(args.pulumi_backend, 'https://api.pulumi.com')

    def test_git_only_blank_keeps_existing_secret_and_cloudflare_disables_certificate(self):
        from unittest.mock import patch
        defaults = {'TLS_MODE': 'cloudflare', 'DOKS_CLUSTER_ID': '11111111-1111-4111-8111-111111111111',
                    'REPOSILITE_HOSTNAME': 'maven.example.com'}
        def form(title, fields, **kwargs):
            values = {f.key: f.default for f in fields}
            self.assertEqual(ui.validate_fields(fields, values, kwargs['validate']), {})
            return values
        with patch.object(ui, 'form', side_effect=form):
            values, token = self.forms.github_environment(defaults, {}, True)
        self.assertEqual(token, '')
        self.assertEqual(values, defaults)

    def test_infrastructure_form_builds_plan_through_existing_read_only_validation(self):
        from unittest.mock import patch
        class DO:
            def list(self, path, key):
                return [{'id': 'project-id', 'name': 'Default', 'is_default': True}] if path == '/projects' else []
            def request(self, method, path):
                if method != 'GET':
                    raise AssertionError('Unexpected mutation')
                return {'options': {'regions': [{'slug': 'nyc1'}], 'sizes': [{'slug': 's-2vcpu-4gb'}],
                                    'versions': [{'slug': '1.35.2-do.0'}]}}
        class CF:
            def list(self, path, **kwargs):
                return [{'id': 'zone-id', 'name': 'example.com'}] if path == '/zones' else []
            def request(self, method, path):
                if method != 'GET':
                    raise AssertionError('Unexpected mutation')
                return {'result': {'status': 'active'}}
        def form(title, fields, **kwargs):
            values = {f.key: f.default for f in fields}
            self.assertEqual(ui.validate_fields(fields, values, kwargs['validate']), {})
            values['count'] = '0'
            self.assertIn('count', kwargs['validate'](values))
            values['count'] = '2'
            values['zone'] = 'elsewhere.com'
            self.assertIn('zone', kwargs['validate'](values))
            values['zone'] = 'example.com'
            return values
        with patch.object(ui, 'form', side_effect=form):
            plan = self.forms.infrastructure(DO(), {}, CF())
        self.assertEqual(plan['cluster_body']['node_pools'][0]['count'], 2)
        self.assertEqual(plan['projectId'], 'project-id')
        self.assertEqual(plan['cloudflare']['acmeEmail'], 'hostmaster@example.com')
        self.assertEqual(plan['hostname'], 'maven.example.com')

    def test_existing_stack_identity_cannot_be_changed_in_form(self):
        values = {'cluster': 'other', 'project': 'new', 'region': 'nyc1', 'size': 'small',
                  'version': '1', 'count': '1', 'hostname': 'other.example.com', 'zone': 'example.com', 'email': 'a@example.com'}
        previous = {'cluster': {'properties': {'name': 'original'}}, 'projectId': 'old',
                    'hostname': 'maven.example.com', 'cloudflare': {'zoneId': 'old-zone'}}
        errors = self.forms.validate_infrastructure(values, clusters=[], projects=[{'id': 'new'}],
                    zones=[{'id': 'new-zone', 'name': 'example.com'}], regions=['nyc1'], sizes=['small'],
                    versions=['1'], previous=previous)
        self.assertEqual(set(errors), {'cluster', 'project', 'hostname', 'zone'})
