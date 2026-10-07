"""Redacted setup transcripts and streaming, noninteractive command output."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import base64
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / '.local' / 'logs'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
SENSITIVE = re.compile(r'token|password|passphrase|secret|credential|private.?key|kubeconfig', re.I)


class Transcript:
    def __init__(self, directory, display):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        fd, name = tempfile.mkstemp(prefix=f'setup-{stamp}-', suffix='.log', dir=directory)
        self.path = Path(name)
        self.file = os.fdopen(fd, 'w', encoding='utf-8')
        self.display = display
        self.secrets = set()
        self.cancelled = False
        self.forwarding = False
        self.pem = False
        self.register(value for key, value in os.environ.items() if SENSITIVE.search(key))

    def register(self, values):
        for value in values:
            if value:
                self.secrets.add(value)
                self.secrets.add(json.dumps(value)[1:-1])
                self.secrets.add(base64.b64encode(value.encode()).decode())

    def redact(self, value):
        value = ANSI.sub('', str(value))
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|dop_v1_[A-Za-z0-9]+|pul-[A-Za-z0-9_-]{20,})', '[REDACTED]', value)
        value = re.sub(r'(?i)(Bearer\s+)\S+', r'\1[REDACTED]', value)
        # Withhold complete lines containing credential fields, including YAML
        # certificate/key data. Do not attempt to parse arbitrary provider prose.
        lines = []
        for line in value.splitlines(keepends=True):
            if '-----BEGIN ' in line:
                self.pem = True
            if self.pem:
                if '-----END ' in line:
                    self.pem = False
                lines.append('[REDACTED certificate/key material]\n')
            elif re.search(r'(?i)["\']?(?:[\w-]*(?:token|password|passphrase|secret|credential|private.?key|kubeconfig)[\w-]*|client-key-data|client-certificate-data|certificate-authority-data)["\']?\s*[:=]', line):
                lines.append('[REDACTED credential field]\n')
            else:
                lines.append(line)
        return ''.join(lines)

    def emit(self, value):
        value = self.redact(value)
        if self.file is not None:
            self.file.write(value)
            self.file.flush()
        self.forwarding = True
        try:
            self.display(value, end='')
        finally:
            self.forwarding = False

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None


class ThreadStream:
    """Capture only the setup worker; leave Textual's renderer on its real stream."""
    def __init__(self, original, transcript):
        self.original, self.transcript = original, transcript
        self.owner = threading.get_ident()
        self.pending = ''

    def __getattr__(self, name):
        return getattr(self.original, name)

    def write(self, value):
        if threading.get_ident() != self.owner or self.transcript.forwarding:
            return self.original.write(value)
        self.pending += value
        while '\n' in self.pending:
            line, self.pending = self.pending.split('\n', 1)
            self.transcript.emit(line + '\n')
        return len(value)

    def flush(self):
        # Keep partial lines until newline/close so split secret writes cannot leak.
        self.original.flush()

    def finish(self):
        if self.pending:
            self.transcript.emit(self.pending + '\n')
            self.pending = ''


def logged(callback, ui, directory=DEFAULT_DIRECTORY):
    transcript = Transcript(directory, lambda *args, **kwargs: ui._backend.say(*args, **kwargs))
    previous, ui._transcript = ui._transcript, transcript
    stdout, stderr = ThreadStream(sys.stdout, transcript), ThreadStream(sys.stderr, transcript)
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            transcript.emit(f'Debug log: {transcript.path}\n')
            try:
                callback()
                stdout.finish()
                stderr.finish()
                if transcript.cancelled:
                    transcript.emit('Setup cancelled.\n')
                else:
                    ui.check_cancelled()
                    transcript.emit('Setup completed successfully.\n')
                    # Closing before unlink is required on Windows.
                    answer = ui.ask('Delete the debugging log? yes/no', 'no')
                    if answer.lower() == 'yes':
                        transcript.close()
                        try:
                            transcript.path.unlink()
                        except OSError:
                            transcript.emit(f'Could not delete debug log: {transcript.path}\n')
                        else:
                            transcript.emit('Debug log deleted.\n')
            except BaseException as error:
                stdout.finish()
                stderr.finish()
                if isinstance(error, (ui.SetupCancelled, KeyboardInterrupt, EOFError)):
                    transcript.emit('Setup cancelled/interrupted.\n')
                elif isinstance(error, ui.SafeError):
                    transcript.emit(f'Setup failed: {error}\n')
                else:
                    # Traceback locations help debugging without exposing locals,
                    # exception messages, or source lines containing credentials.
                    transcript.emit(f'Setup failed ({type(error).__name__}).\n')
                    tb = error.__traceback__
                    while tb:
                        transcript.emit(f'  {tb.tb_frame.f_code.co_filename}:{tb.tb_lineno} in {tb.tb_frame.f_code.co_name}\n')
                        tb = tb.tb_next
                raise
            finally:
                if transcript.path.exists():
                    transcript.emit(f'Debug log retained: {transcript.path}\n')
    finally:
        transcript.close()
        ui._transcript = previous


def run_command(command, ui, *, input=None, env=None, cwd=None, timeout=120,
                text=True, encoding='utf-8', capture_output=True,
                private_stdout=False, preview_json=False):
    """Return raw captured data to parsers; only sanitized output reaches the user."""
    transcript = ui._transcript
    if transcript is None:
        return subprocess.run(command, input=input, env=env, cwd=cwd, timeout=timeout,
                              text=text, encoding=encoding, capture_output=capture_output)
    transcript.register(value for key, value in (env or {}).items() if SENSITIVE.search(key))
    if input:
        transcript.register([input])
    # Arguments can contain configuration or credentials: show only the executable.
    name = Path(command[0]).name
    transcript.emit(f'\n[{name}] started\n')
    if private_stdout:
        transcript.emit('[stdout withheld: private state/configuration]\n')
    started = time.monotonic()
    events = queue.Queue()
    captured = {'stdout': [], 'stderr': []}
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', errors='replace', bufsize=1)

    def read(stream, channel):
        try:
            for line in stream:
                events.put((channel, line))
        finally:
            stream.close()
            events.put((channel, None))

    def send_input():
        try:
            process.stdin.write(input)
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    for channel in captured:
        threading.Thread(target=read, args=(getattr(process, channel), channel), daemon=True).start()
    if input is not None:
        threading.Thread(target=send_input, daemon=True).start()
    finished = 0
    try:
        while finished < 2:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                transcript.emit(f'[{name}] timed out after {timeout}s\n')
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                channel, line = events.get(timeout=min(.1, remaining))
            except queue.Empty:
                continue
            if line is None:
                finished += 1
                continue
            captured[channel].append(line)
            if channel == 'stdout' and private_stdout:
                continue
            if channel == 'stdout' and preview_json:
                # Resource events may include kubeconfig/Secret inputs. Only
                # diagnostic messages are safe to display; parsing still gets all events.
                try:
                    event = json.loads(line)
                    line = event.get('diagnosticEvent', {}).get('message', '')
                except (ValueError, AttributeError):
                    line = '[unrecognized preview event withheld]\n'
                if not line:
                    continue
            transcript.emit(f'[{name} {channel}] {line.rstrip()}\n')
        result = process.wait(timeout=max(.001, timeout - (time.monotonic() - started)))
    except BaseException:
        process.kill()
        process.wait()
        raise
    transcript.emit(f'[{name}] exit {result} ({time.monotonic() - started:.1f}s)\n')
    return subprocess.CompletedProcess(command, result, ''.join(captured['stdout']), ''.join(captured['stderr']))
