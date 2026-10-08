"""Shared UI, retained logs and scoped I/O for independently invoked setup stages."""
import argparse
import base64
from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import setup_ui as ui
from digitalocean import NoRedirect
from token_store import DEFAULT_PATH, load_tokens

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / '.local' / 'independent-setup.json'
ALLOWED = {'cluster_id', 'cluster_name', 'ip', 'hostname', 'zone_id', 'record_id', 'repo', 'tls_mode', 'pending_project'}


def load_settings():
    return json.loads(SETTINGS.read_text(encoding='utf-8')) if SETTINGS.exists() else {}


def save_settings(**values):
    if set(values) - ALLOWED:
        raise ui.InputError('Only nonsecret setup outputs can be saved.')
    data = load_settings()
    data.update(values)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    temp.replace(SETTINGS)


def fields(title, items, validate=None, description=''):
    if ui.supports_forms():
        return ui.form(title, items, description=description, validate=validate)
    ui.say(title + '\n' + description)
    if not items:
        if ui.ask('Completed these steps? yes/no', 'no').lower() != 'yes':
            ui.cancelled('Cancelled; completed steps are retained.')
            raise ui.SetupCancelled()
        return {}
    while True:
        values = {f.key: f.default for f in items}
        for f in items:
            if f.enabled_when and not f.enabled_when(values):
                continue
            values[f.key] = (ui.hidden(f.label + ': ') or f.default if f.secret
                             else ui.ask(f.label, f.default))
        errors = ui.validate_fields(items, values, validate)
        if not errors:
            return values
        ui.say('\n'.join(errors.values()))


def approve(title, description):
    ui.stage('review')
    ui.say(description)
    if ui.supports_forms():
        return ui.confirm_action(title, description, 'Proceed')
    if ui.ask(title + '? yes/no', 'no').lower() == 'yes':
        return True
    ui.cancelled('Cancelled; completed earlier steps, if any, are retained.')
    return False


def credentials(args, key, optional=False):
    variables = {'digitalocean': 'DIGITALOCEAN_TOKEN', 'cloudflare': 'CLOUDFLARE_API_TOKEN', 'github': 'GH_TOKEN'}
    supplied = os.environ.get(variables[key])
    if supplied:
        if any(c.isspace() or ord(c) < 32 for c in supplied):
            raise ui.InputError('Enter a single-line API token.')
        ui.register_secrets([supplied])
        return supplied
    if not hasattr(args, '_tokens'):
        args._tokens = load_tokens(args.tokens_file, ui.ask, ui.hidden, ui.say)
        ui.register_secrets(args._tokens.values())
    value = os.environ.get(variables[key]) or args._tokens.get(key) or ui.hidden(
        f'{key.title()} API token' + (' (blank to keep existing login/secret)' if optional else '') + ': ')
    if (not value and not optional) or any(c.isspace() or ord(c) < 32 for c in value):
        raise ui.InputError('Enter a single-line API token.')
    ui.register_secrets([value])
    return value


def command(args, *, env=None, private=False, stdin=None, timeout=120):
    ui.check_cancelled()
    try:
        result = ui.run_command(list(map(str, args)), env=env, input=stdin, private_stdout=private,
                                timeout=timeout, cwd=ROOT, text=True, capture_output=True, encoding='utf-8')
    except subprocess.TimeoutExpired:
        raise ui.SetupError('Command timed out. Inspect the last logged step and rerun; partial work is retained.') from None
    if result.returncode:
        raise ui.SetupError(f'{Path(str(args[0])).name} failed (exit {result.returncode}); see the log above.')
    return result.stdout


def require(*executables):
    for executable in executables:
        if not shutil.which(executable):
            raise ui.InputError(f'Install {executable} and reopen the terminal before running this stage.')


def ipv4(value):
    address = ipaddress.IPv4Address(value)
    if not address.is_global:
        raise ValueError('Use the public IPv4 assigned to the gateway.')
    return str(address)


def hostname(value):
    if len(value) > 253 or '.' not in value or not all(re.fullmatch(
            r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in value.split('.')):
        raise ValueError('Use a lowercase hostname without scheme, port or path.')
    return value


class Api:
    def __init__(self, provider, token):
        if provider not in ('digitalocean', 'cloudflare'):
            raise ValueError('Unsupported provider')
        self.provider, self.token = provider, token
        self.base = ('https://api.digitalocean.com/v2' if provider == 'digitalocean'
                     else 'https://api.cloudflare.com/client/v4')
        self.opener = build_opener(NoRedirect())

    def error_detail(self, error):
        """Read only a bounded provider message, never dump the response body."""
        if self.provider != 'digitalocean':
            return ''
        try:
            data = json.loads(error.read(8192))
        except (OSError, ValueError):
            return ''
        message = data.get('message') if isinstance(data, dict) else None
        if not isinstance(message, str):
            return ''
        # Sanitize before truncating or raising: the exception can also be shown
        # outside the transcript context. The transcript redacts other secrets.
        if self.token:
            for secret in (self.token, json.dumps(self.token)[1:-1],
                           base64.b64encode(self.token.encode()).decode()):
                message = message.replace(secret, '[REDACTED]')
        message = re.sub(r'(?i)(Bearer\s+)\S+', r'\1[REDACTED]', message)
        message = ''.join(c if c.isprintable() else ' ' for c in message)
        return ' '.join(message.split())[:1000]

    def request(self, method, path, body=None):
        ui.check_cancelled()
        if not path.startswith('/') or path.startswith('//') or '://' in path or '..' in path:
            raise ui.InputError('Refusing an API URL outside the selected provider.')
        req = Request(self.base + path, method=method,
                      headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'},
                      data=None if body is None else json.dumps(body).encode())
        ui.say(f'{self.provider}: {method} {path.split("?")[0]}')
        try:
            with self.opener.open(req, timeout=60) as response:
                raw = response.read()
            result = json.loads(raw) if raw else {}
            if self.provider == 'cloudflare' and not result.get('success'):
                raise ui.SetupError('Cloudflare did not confirm success.')
            return result
        except HTTPError as exc:
            if exc.code == 404 and method == 'GET':
                return None
            detail = self.error_detail(exc)
            message = f'{self.provider} {method} failed (HTTP {exc.code}).'
            if detail:
                message += f' Provider message: {detail}'
            if exc.code in (401, 403):
                message += ' Check the selected account, API token and permissions.'
            if method != 'GET':
                message += ' Inspect the provider before retrying the write.'
            raise ui.SetupError(message) from None
        except (URLError, OSError, ValueError):
            raise ui.SetupError(f'{self.provider} request failed; completion is unknown. Inspect the provider before retrying.') from None


@contextmanager
def cluster_access(token, cluster_id):
    import uuid
    try:
        uuid.UUID(cluster_id)
    except ValueError:
        raise ui.InputError('Use the DigitalOcean cluster UUID.') from None
    require('doctl', 'kubectl')
    with tempfile.TemporaryDirectory(prefix='reposilite-access-') as temp:
        env = dict(os.environ, DIGITALOCEAN_ACCESS_TOKEN=token, KUBECONFIG=str(Path(temp) / 'config'))
        command(['doctl', 'kubernetes', 'cluster', 'kubeconfig', 'save', cluster_id,
                 '--expiry-seconds', '3600', '--alias', 'reposilite-independent'], env=env, private=True)
        def kubectl(*args, private=False, stdin=None, timeout=900):
            return command(['kubectl', '--context', 'reposilite-independent', *args],
                           env=env, private=private, stdin=stdin, timeout=timeout)
        yield kubectl, env


def launch(name, callback):
    parser = argparse.ArgumentParser(description=name + ' independent setup')
    parser.add_argument('--plain', action='store_true')
    parser.add_argument('--tokens-file', type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    def run():
        return ui.logged(lambda: callback(args), directory=ROOT / '.local' / 'logs' / name, keep_log=True)
    try:
        if args.plain:
            run()
            return 0
        return ui.run(run, steps=['credentials', 'discovery', 'review', 'apply', 'verify'])
    except (ui.SafeError, OSError) as exc:
        ui.say(str(exc) if isinstance(exc, ui.SafeError) else 'Local command/file access failed; see the retained log.')
        return 1
    except (ui.SetupCancelled, KeyboardInterrupt, EOFError):
        return 130
