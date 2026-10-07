"""Synchronous setup prompts with an optional, lazily imported Textual interface."""
from __future__ import annotations

import builtins
from dataclasses import dataclass
import getpass
import threading
import time
import warnings

STEPS = ('tokens', 'github', 'pulumi', 'discovery', 'review', 'preview', 'provision', 'save')


class SafeError(Exception):
    """An intentional diagnostic written without secrets or provider output."""


class SetupError(SafeError, RuntimeError):
    pass


class InputError(SafeError, ValueError):
    pass


class SetupCancelled(Exception):
    """A cooperative request to stop before the next operation."""


class PlainBackend:
    def check(self):
        pass

    def ask(self, label, default=''):
        answer = builtins.input(f'{label} [{default}]: ' if default else label)
        return answer or default

    def hidden(self, prompt):
        # Never permit getpass's echoing fallback for secrets.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            try:
                return getpass.getpass(prompt)
            except getpass.GetPassWarning:
                raise SetupError('Hidden input requires a terminal.') from None

    def say(self, *objects, **kwargs):
        builtins.print(*objects, **kwargs)

    def stage(self, key):
        pass

    def skip(self, key):
        pass

    def cancelled(self, message):
        self.say(message)

    def terminal(self, callback):
        return callback()


_backend = PlainBackend()
_transcript = None


def check_cancelled():
    return _backend.check()


def _prompt(callback, *args):
    # Plain input/getpass prompts have no newline and must reach the terminal
    # immediately. Never capture entered values or hidden-input prompts.
    if _transcript is None:
        return callback(*args)
    previous = _transcript.forwarding
    _transcript.forwarding = True
    try:
        return callback(*args)
    finally:
        _transcript.forwarding = previous


def ask(label, default=''):
    return _prompt(_backend.ask, label, default)


def hidden(prompt):
    value = _prompt(_backend.hidden, prompt)
    register_secrets([value])
    return value


def say(*objects, sep=' ', end='\n', flush=False, file=None):
    if _transcript is not None:
        return _transcript.emit(sep.join(str(o) for o in objects) + end)
    return _backend.say(*objects, sep=sep, end=end, flush=flush, file=file)


def stage(key):
    if _transcript is not None:
        _transcript.emit(f'\n--- {key.title()} ---\n')
    return _backend.stage(key)


def skip(key):
    return _backend.skip(key)


def cancelled(message):
    if _transcript is not None:
        _transcript.cancelled = True
        _transcript.emit(message + '\n')
        return _backend.cancelled('Setup cancelled.')
    return _backend.cancelled(message)


def terminal(callback):
    return _backend.terminal(callback)


def register_secrets(values):
    if _transcript is not None:
        _transcript.register(values)


def logged(callback, **kwargs):
    import sys
    import setup_output
    return setup_output.logged(callback, sys.modules[__name__], **kwargs)


def run_command(command, **kwargs):
    import sys
    import setup_output
    return setup_output.run_command(command, sys.modules[__name__], **kwargs)


@dataclass
class Field:
    key: str
    label: str
    default: str = ''
    secret: bool = False
    choices: tuple = ()
    help: str = ''
    required: bool = True
    enabled_when: object = None


def supports_forms():
    return hasattr(_backend, 'form')


def validate_fields(fields, values, validate=None):
    errors = {}
    for field in fields:
        if field.enabled_when and not field.enabled_when(values):
            continue
        value = values[field.key]
        if field.required and not value.strip():
            errors[field.key] = 'This field is required.'
        elif field.choices and value not in field.choices:
            errors[field.key] = 'Choose one of the available values.'
    if validate:
        errors.update(validate(values))
    return errors


def form(title, fields, *, description='', validate=None, errors=None, proceed='Proceed'):
    values = _backend.form(title, fields, description, validate, errors or {}, proceed)
    register_secrets(values[f.key] for f in fields if f.secret)
    return values


def confirm_action(title, description='', proceed='Apply'):
    form(title, [], description=description, proceed=proceed)
    return True


def create_app(callback, steps=None):
    """Construct the UI only when requested, keeping --plain dependency free."""
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Button, Footer, Header, Input, ProgressBar, RichLog, Static, Select

    class Prompt(ModalScreen):
        BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

        def __init__(self, label, default, secret, answer):
            super().__init__()
            self.label, self.default, self.secret, self.answer = label, default, secret, answer

        def compose(self) -> ComposeResult:
            with Vertical(id='prompt-box'):
                yield Static(self.label, markup=False)
                yield Input(value=self.default, password=self.secret, id='answer')
                yield Button('Continue', variant='primary', id='submit')

        def on_mount(self):
            self.query_one(Input).focus()

        def submit(self):
            value = self.query_one(Input).value or self.default
            self.answer(value)
            self.dismiss()

        def on_input_submitted(self):
            self.submit()

        def on_button_pressed(self):
            self.submit()

        def action_cancel(self):
            self.app.action_cancel()

    class SetupForm(Vertical):
        def __init__(self, title, fields, description, validate, errors, proceed, answer):
            super().__init__(id='setup-form')
            self.title, self.fields, self.description = title, fields, description
            self.validate, self.errors, self.proceed, self.answer = validate, errors, proceed, answer
            self.submitted = False

        def compose(self):
            yield Static(self.title, id='form-title', markup=False)
            with VerticalScroll(id='form-fields'):
                if self.description:
                    yield Static(self.description, id='form-description', markup=False)
                for field in self.fields:
                    yield Static(field.label, classes='field-label', markup=False)
                    if field.choices:
                        options = [(v, v) for v in field.choices]
                        if field.default and field.default not in field.choices:
                            options.insert(0, (field.default + ' (unavailable)', field.default))
                        yield Select(options, value=field.default or Select.NULL,
                                     allow_blank=not field.required, id='field-' + field.key)
                    else:
                        yield Input(value=field.default, password=field.secret, id='field-' + field.key)
                    if field.help:
                        yield Static(field.help, classes='field-help', markup=False)
                    yield Static('', id='error-' + field.key, classes='field-error', markup=False)
                yield Static('', id='form-error', classes='field-error', markup=False)
            with Horizontal(id='form-actions'):
                yield Button('Cancel', id='form-cancel')
                yield Button(self.proceed, variant='primary', id='form-proceed')

        def values(self):
            result = {}
            for field in self.fields:
                widget = self.query_one('#field-' + field.key)
                result[field.key] = '' if widget.value is Select.NULL else widget.value
            return result

        def on_mount(self):
            self.show_errors(self.errors)
            self.update_enabled()
            if self.fields:
                self.query_one('#field-' + self.fields[0].key).focus()
            else:
                # Review screens do not focus Apply automatically.
                self.query_one('#form-cancel').focus()

        def show_errors(self, errors):
            for field in self.fields:
                widget = self.query_one('#error-' + field.key, Static)
                widget.update(errors.get(field.key, ''))
                widget.display = field.key in errors
            widget = self.query_one('#form-error', Static)
            widget.update(errors.get('__form__', ''))
            widget.display = '__form__' in errors

        def update_enabled(self):
            values = self.values()
            for field in self.fields:
                if field.enabled_when:
                    self.query_one('#field-' + field.key).disabled = not field.enabled_when(values)

        def on_input_changed(self):
            if self.is_mounted:
                self.update_enabled()

        def on_select_changed(self):
            if self.is_mounted:
                self.update_enabled()

        def on_input_submitted(self, event):
            event.stop()
            self.app.action_focus_next()

        def on_button_pressed(self, event):
            event.stop()
            if event.button.id == 'form-cancel':
                self.app.action_cancel()
                return
            if self.submitted:
                return
            values = self.values()
            errors = validate_fields(self.fields, values, self.validate)
            self.show_errors(errors)
            if errors:
                first = next((f for f in self.fields if f.key in errors), None)
                if first:
                    self.query_one('#field-' + first.key).focus()
                return
            self.submitted = True
            self.query_one('#form-proceed', Button).disabled = True
            self.answer(values)

    class SetupApp(App):
        TITLE = 'Cloud setup'
        SUB_TITLE = 'Guided configuration'
        ENABLE_COMMAND_PALETTE = False
        BINDINGS = [Binding('ctrl+c', 'cancel', 'Cancel', priority=True),
                    Binding('ctrl+q', 'cancel', 'Cancel', priority=True),
                    Binding('q', 'cancel', 'Cancel'), Binding('enter', 'close', 'Close result')]
        CSS = '''
        Screen { background: $background; }
        #body { height: 1fr; }
        #steps { width: 28; padding: 1 2; background: $panel; }
        #content { width: 1fr; padding: 1 2; }
        #status { height: 1; color: $accent; }
        #output { height: 1fr; min-height: 6; border: round $primary; }
        #intro { height: auto; margin-bottom: 1; }
        ProgressBar { margin: 0; }
        #form-host { display: none; height: 2fr; min-height: 9; }
        #setup-form { height: 1fr; border: round $accent; padding: 0 1; }
        #form-title { height: 1; text-style: bold; color: $accent; }
        #form-fields { height: 1fr; }
        #form-description { height: auto; margin-bottom: 1; }
        .field-label { height: auto; margin-top: 1; }
        .field-help { height: auto; color: $text-muted; }
        .field-error { height: auto; color: $error; }
        #form-actions { height: 3; margin-top: 1; align-horizontal: right; }
        #form-actions Button { margin-left: 1; }

        Prompt { align: center middle; background: $background 70%; }
        #prompt-box { width: 70; height: auto; padding: 2; border: round $accent; background: $surface; }
        #prompt-box Input { margin: 1 0; }
        #prompt-box Button { width: 100%; }
        '''

        def __init__(self):
            super().__init__()
            self.steps = tuple(steps if steps is not None else STEPS)
            self.statuses = dict.fromkeys(self.steps, 'pending')
            self.active = None
            self.started = time.monotonic()
            self.cancel_event = threading.Event()
            self.done = False
            self.result_code = None
            self.prompt_screen = None
            self.prompt_answer = None
            self.form_answer = None
            self.tick = 0
            self.flow_thread = None
            self.flow_finished = threading.Event()

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id='body'):
                yield Static('', id='steps', markup=False)
                with Vertical(id='content'):
                    yield Static('Progress counts completed setup steps. Cloud operations have no estimated percentage.', id='intro', markup=False)
                    yield ProgressBar(total=len(self.steps), show_eta=False, id='progress')
                    yield Static('Starting…', id='status', markup=False)
                    yield Vertical(id='form-host')
                    yield RichLog(id='output', markup=False, highlight=False, wrap=True)
            yield Footer()

        def on_mount(self):
            self.query_one('#output', RichLog).border_title = 'Output · stdout / stderr'
            self.refresh_steps()
            self.set_interval(0.2, self.activity)
            self.worker = self.run_worker(self.execute, thread=True, exit_on_error=False)

        def check(self):
            if self.cancel_event.is_set():
                raise SetupCancelled()

        def refresh_steps(self):
            symbols = {'pending': '○', 'active': '●', 'complete': '✓', 'skipped': '−', 'failed': '!', 'cancelled': '×'}
            self.query_one('#steps', Static).update('\n\n'.join(f'{symbols[v]}  {k.title()} — {v}' for k, v in self.statuses.items()))
            self.query_one(ProgressBar).update(progress=sum(v == 'complete' for v in self.statuses.values()))

        def activity(self):
            if not self.done:
                self.tick += 1
                text = 'Cancellation requested; waiting for the current operation.' if self.cancel_event.is_set() else f'{"◐◓◑◒"[self.tick % 4]}  {(self.active or "Starting").title()} · {int(time.monotonic() - self.started)}s elapsed'
                self.query_one('#status', Static).update(text)

        def stage(self, key):
            self.check()
            self.call_from_thread(self.set_stage, key)

        def set_stage(self, key):
            if self.active and self.statuses.get(self.active) == 'active':
                self.statuses[self.active] = 'complete'
            self.active = key
            if key in self.statuses:
                self.statuses[key] = 'active'
            self.started = time.monotonic()
            self.refresh_steps()

        def skip(self, key):
            self.check()
            def update():
                if key in self.statuses:
                    self.statuses[key] = 'skipped'
                if self.active == key:
                    self.active = None
                self.refresh_steps()
            self.call_from_thread(update)

        def say(self, *objects, sep=' ', end='\n', **kwargs):
            value = sep.join(str(o) for o in objects) + end
            self.call_from_thread(lambda: self.query_one('#output', RichLog).write(value.rstrip('\n')))

        def form(self, title, fields, description, validate, errors, proceed):
            self.check()
            event, result = threading.Event(), []
            def answer(value):
                if not event.is_set():
                    result.append(value)
                    event.set()
            async def show():
                if self.cancel_event.is_set():
                    answer(None)
                    return
                self.form_answer = answer
                host = self.query_one('#form-host', Vertical)
                await host.remove_children()
                host.display = True
                await host.mount(SetupForm(title, fields, description, validate, errors, proceed, answer))
            async def hide():
                self.form_answer = None
                host = self.query_one('#form-host', Vertical)
                await host.remove_children()
                host.display = False
                self.query_one('#output', RichLog).focus()
            self.call_from_thread(show)
            try:
                event.wait()
                self.check()
                return result[0]
            finally:
                self.call_from_thread(hide)

        def ask(self, label, default=''):
            return self.request(label, default, False)

        def hidden(self, prompt):
            return self.request(prompt, '', True)

        def request(self, label, default, secret):
            self.check()
            event = threading.Event()
            result = []
            def answer(value):
                result.append(value)
                event.set()
            def show():
                if self.cancel_event.is_set():
                    event.set()
                    return
                self.prompt_answer = answer
                self.prompt_screen = Prompt(label, default, secret, answer)
                self.push_screen(self.prompt_screen)
            self.call_from_thread(show)
            event.wait()
            self.check()
            return result[0]

        def cancelled(self, message):
            self.call_from_thread(lambda: self.query_one('#output', RichLog).write(message))
            self.cancel_event.set()

        def action_cancel(self):
            if self.done:
                self.exit(self.result_code)
                return
            self.cancel_event.set()
            if self.form_answer is not None:
                self.form_answer(None)
            if self.prompt_screen is not None and self.screen is self.prompt_screen:
                self.prompt_answer('')
                self.prompt_screen.dismiss()
            self.activity()

        def action_quit(self):
            self.action_cancel()

        def action_close(self):
            if self.done:
                self.exit(self.result_code)

        def terminal(self, operation):
            self.check()
            # The complete suspension must stay inside one UI-thread callback.
            # Keeping the event loop running after the driver closes its writer
            # lets timer paints fill that closed queue and deadlock the UI.
            def handoff():
                self.check()
                try:
                    with self.suspend():
                        return operation()
                except KeyboardInterrupt:
                    self.cancel_event.set()
                    raise SetupCancelled() from None
            result = self.call_from_thread(handoff)
            self.check()
            return result

        def execute(self):
            self.flow_thread = threading.current_thread()
            global _backend
            previous, _backend = _backend, self
            code = 0
            try:
                self.check()
                callback()
                self.check()
            except (SetupCancelled, KeyboardInterrupt):
                code = 130
            except SystemExit as error:
                code = 0 if error.code in (None, 0) else 1
            except SafeError as error:
                code = 1
                self.call_from_thread(lambda: self.query_one(RichLog).write(str(error)))
            except Exception:
                # Provider exception text and locals can contain credentials.
                code = 1
                self.call_from_thread(lambda: self.query_one(RichLog).write(
                    'An unexpected error stopped setup. No rollback was attempted; inspect the selected stack before retrying.'))
            finally:
                _backend = previous
                try:
                    self.call_from_thread(self.finish, code)
                except RuntimeError:
                    # The driver may have stopped unexpectedly; run() still
                    # waits for this flow before allowing the process to exit.
                    pass
                finally:
                    self.flow_finished.set()

        def finish(self, code):
            self.done, self.result_code = True, code
            if self.active and self.statuses.get(self.active) == 'active':
                self.statuses[self.active] = {0: 'complete', 1: 'failed', 130: 'cancelled'}[code]
            self.refresh_steps()
            message = {0: 'Setup complete.', 1: 'Setup failed. Review the safe output above and retry.', 130: 'Setup cancelled.'}[code]
            self.query_one('#status', Static).update(message + ' Press Enter or Q to close.')
            self.query_one('#output', RichLog).write(message)

    return SetupApp()


def run(callback, steps=None):
    app = create_app(callback, steps)
    try:
        result = app.run()
        return result if result is not None else 130
    finally:
        # If the terminal/driver exits unexpectedly, unblock a pending prompt
        # and wait for any current operation; never abandon a writing worker.
        app.cancel_event.set()
        if app.form_answer is not None:
            app.form_answer(None)
        if app.prompt_answer is not None:
            app.prompt_answer('')
        if app.flow_thread is not None:
            app.flow_finished.wait()
