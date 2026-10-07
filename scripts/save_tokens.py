"""Save GitHub, DigitalOcean and Cloudflare tokens in a GPG-encrypted local file."""
import argparse
from pathlib import Path
import shutil
import sys

import setup_ui as ui
from token_store import DEFAULT_PATH, TOKEN_KEYS, TokenStoreError, encrypt_tokens, validate_tokens


def save(path):
    ui.stage('tokens')
    if not shutil.which('gpg'):
        raise TokenStoreError('Install GnuPG (gpg) before saving tokens. See docs/setup-wizard.md.')
    overwrite = path.exists()
    if overwrite and ui.ask('Replace the existing encrypted token file? yes/no', 'no').lower() != 'yes':
        ui.cancelled('Cancelled. Existing encrypted tokens were retained.')
        return
    ui.say('Tokens are encrypted locally with GPG. The passphrase is never saved.')
    values = {}
    labels = {'github': 'GitHub', 'digitalocean': 'DigitalOcean', 'cloudflare': 'Cloudflare'}
    for key in TOKEN_KEYS:
        value = ui.hidden(f'{labels[key]} access token (required, hidden): ')
        values[key] = value.strip()
    validate_tokens(values)
    ui.stage('encrypt')
    password = ui.hidden('Choose an encryption passphrase (at least 12 characters): ')
    if len(password) < 12:
        raise TokenStoreError('Use a passphrase of at least 12 characters')
    if password != ui.hidden('Confirm encryption passphrase: '):
        raise TokenStoreError('Passphrases do not match. No token file was changed.')
    encrypt_tokens(values, password, path, overwrite=overwrite)
    ui.say(f'Encrypted tokens saved to {path}.')
    ui.say('Setup will ask for this passphrase on the next run. Keep it in your password manager.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tokens-file', type=Path, default=DEFAULT_PATH,
                        help='Encrypted file path (default: .local/tokens.gpg in this repository)')
    parser.add_argument('--plain', action='store_true', help='Use basic terminal prompts instead of Textual')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise TokenStoreError('Run this script in an interactive terminal with hidden input')
    if args.plain:
        save(args.tokens_file)
        return 0
    return ui.run(lambda: save(args.tokens_file), steps=['tokens', 'encrypt'])


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (TokenStoreError, RuntimeError, OSError) as exc:
        print(f'Save stopped: {exc}', file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print('\nCancelled. No plaintext token file was written.', file=sys.stderr)
        sys.exit(130)
