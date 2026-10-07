"""Passphrase-encrypted local tokens. Only ciphertext is ever written to disk."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from setup_ui import SetupError, check_cancelled

DEFAULT_PATH = Path(__file__).resolve().parents[1] / '.local' / 'tokens.gpg'
TOKEN_KEYS = ('github', 'digitalocean', 'cloudflare')
MAX_BYTES = 65536


class TokenStoreError(SetupError):
    """A safe diagnostic that never contains GPG output or secret values."""


def validate_tokens(tokens):
    if not isinstance(tokens, dict) or set(tokens) != set(TOKEN_KEYS):
        raise TokenStoreError('Token file must contain GitHub, DigitalOcean and Cloudflare tokens')
    if any(not isinstance(v, str) or not 1 <= len(v) <= 8192 or
           any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in v) for v in tokens.values()):
        raise TokenStoreError('Tokens must be nonempty single-line values without whitespace')
    return dict(tokens)


def decode_tokens(data):
    if len(data) > MAX_BYTES:
        raise TokenStoreError('Decrypted token file is too large')
    try:
        value = json.loads(data)
        if not isinstance(value, dict) or set(value) != {'version', 'tokens'} or \
                type(value['version']) is not int or value['version'] != 1:
            raise ValueError()
        return validate_tokens(value['tokens'])
    except (ValueError, UnicodeError, TypeError, KeyError):
        raise TokenStoreError('Token file has an invalid or unsupported format') from None


def _passphrase(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or any(c in value for c in '\r\n\0'):
        raise TokenStoreError('Passphrase must be nonempty and contain no newline or NUL characters')
    return value.encode('utf-8') + b'\n'


def _gpg(arguments, data):
    executable = shutil.which('gpg')
    if not executable:
        raise TokenStoreError('Install GnuPG (gpg) to use encrypted token files')
    command = [executable, '--no-options', '--batch', '--yes', '--pinentry-mode', 'loopback',
               '--passphrase-fd', '0', '--no-symkey-cache', *arguments]
    try:
        result = subprocess.run(command, input=data, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise TokenStoreError('GPG could not complete; check installation and retry') from None
    if result.returncode:
        raise TokenStoreError('GPG could not unlock or encrypt the file; check the passphrase and file')
    return result.stdout


def decrypt_tokens(path, passphrase):
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise TokenStoreError('Token file must be a regular encrypted file smaller than 64 KiB')
        output = _gpg(['--max-output', str(MAX_BYTES), '--decrypt', '--', str(path)], _passphrase(passphrase))
        return decode_tokens(output)
    except OSError:
        raise TokenStoreError('Unable to read encrypted token file') from None


def encrypt_tokens(tokens, passphrase, path=DEFAULT_PATH, *, overwrite=False):
    payload = json.dumps({'version': 1, 'tokens': validate_tokens(tokens)}).encode('utf-8')
    password = _passphrase(passphrase)
    path = Path(path)
    if path.is_symlink() or (path.exists() and not overwrite):
        raise TokenStoreError('Token file already exists; confirm replacement before saving')
    # GPG consumes one line from fd 0 for the passphrase, then the remaining stdin
    # for the payload. This works on Windows without inheriting extra POSIX fds.
    encrypted = _gpg(['--symmetric', '--cipher-algo', 'AES256', '--s2k-digest-algo', 'SHA256',
                      '--s2k-count', '65011712', '--output', '-'], password + payload)
    if not encrypted or len(encrypted) > MAX_BYTES:
        raise TokenStoreError('GPG returned an invalid encrypted result')
    check_cancelled()
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix='.tokens-', suffix='.gpg', dir=path.parent)
        with os.fdopen(fd, 'wb') as stream:
            os.chmod(temporary, 0o600)
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        check_cancelled()
        if overwrite:
            os.replace(temporary, path)
        else:
            # Atomically refuse a concurrent creation; never replace an unconfirmed file.
            os.link(temporary, path)
    except OSError:
        raise TokenStoreError('Unable to save encrypted tokens; existing file was not intentionally removed') from None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return path


def load_tokens(path, ask, hidden, say):
    """Return unlocked tokens or {} to preserve the original manual-entry flow."""
    path = Path(path)
    if not path.exists():
        return {}
    if not shutil.which('gpg'):
        say('Encrypted token file found, but gpg is unavailable. Continuing with manual token entry.')
        return {}
    say('Encrypted token file found. Unlock it, or leave the passphrase blank to enter tokens manually.')
    for attempt in range(1, 4):
        password = hidden(f'Token file passphrase (attempt {attempt}/3): ')
        if not password:
            say('Continuing with manual token entry.')
            return {}
        try:
            values = decrypt_tokens(path, password)
            say('Encrypted tokens unlocked for this run.')
            return values
        except TokenStoreError:
            say(f'Could not unlock the token file ({attempt}/3). Check the passphrase or use manual entry.')
        if attempt < 3 and ask('Retry unlocking? yes/no', 'yes').lower() != 'yes':
            break
    say('Continuing with manual token entry. The encrypted file has not been changed.')
    return {}
