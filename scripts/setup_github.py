"""Configure GitHub production deployment independently of cloud provisioning."""
import json
import re
import shutil

import independent_setup as common
import setup_environment as github
import setup_ui as ui
from deploy import configuration


def validate_repository(values):
    if not re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', values['repo']):
        return {'repo': 'Use owner/repository format on github.com.'}
    return {}


def validate_environment(values):
    try:
        configuration(values)
    except ValueError as exc:
        message = str(exc)
        key = next((k for k in values if k in message), 'REPOSILITE_HOSTNAME')
        return {key: message}
    return {}


def execute(args):
    ui.stage('credentials')
    executable = shutil.which('gh')
    if not executable:
        raise ui.SetupError('Install GitHub CLI from https://cli.github.com/ and reopen your terminal')
    token = common.credentials(args, 'github', optional=True)
    client = github.GitHub(executable, token or None)
    identity = github.authenticate(client)
    settings = common.load_settings()
    repo = common.fields('GitHub repository', [
        ui.Field('repo', 'GitHub repository', settings.get('repo', github.DEFAULT_REPO)),
    ], validate=validate_repository)['repo']

    ui.stage('discovery')
    repository = json.loads(client.run('api', f'repos/{repo}'))
    if not repository.get('permissions', {}).get('admin'):
        raise ui.SetupError('An account with repository administrator access is required')
    repo = repository['full_name']
    ui.say(f'Signed in as {identity}. Configuring {repo} / {github.ENVIRONMENT}.')
    names = client.run('api', f'repos/{repo}/environments', '--paginate',
                       '--jq', '.environments[].name').splitlines()
    existing = any(name.casefold() == github.ENVIRONMENT.casefold() for name in names)
    defaults = {}
    secret_exists = False
    if existing:
        defaults = {item['name']: item['value'] for item in json.loads(client.run(
            'variable', 'list', '--repo', repo, '--env', github.ENVIRONMENT, '--json', 'name,value'))}
        secret_exists = github.SECRET in {item['name'] for item in json.loads(client.run(
            'secret', 'list', '--repo', repo, '--env', github.ENVIRONMENT, '--json', 'name'))}
    values = configuration(common.fields('GitHub production settings', [
        ui.Field('DOKS_CLUSTER_ID', 'DOKS cluster UUID',
                 settings.get('cluster_id') or defaults.get('DOKS_CLUSTER_ID', '')),
        ui.Field('REPOSILITE_HOSTNAME', 'Maven hostname',
                 settings.get('hostname') or defaults.get('REPOSILITE_HOSTNAME', '')),
        ui.Field('TLS_MODE', 'TLS mode', settings.get('tls_mode') or defaults.get('TLS_MODE', 'http01'),
                 choices=('http01', 'cloudflare', 'digitalocean')),
        ui.Field('DO_CERTIFICATE_NAME', 'Legacy DO certificate name',
                 defaults.get('DO_CERTIFICATE_NAME', ''),
                 enabled_when=lambda values: values['TLS_MODE'] == 'digitalocean'),
    ], validate=validate_environment,
        description='HTTP01 uses the independently configured gateway and issuer. '
                    'This step does not contact DigitalOcean or Cloudflare.'))
    ui.say('The DigitalOcean token is only stored as a GitHub deployment secret; '
           'it is not used to call DigitalOcean here.')
    token = common.credentials(args, 'digitalocean', optional=secret_exists)
    if not token and not secret_exists:
        raise ui.InputError('A DigitalOcean token is required for a new setup')
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in token):
        raise ui.InputError('The token must be a single value without whitespace')

    ui.stage('review')
    if not github.confirm(repo, values, replacing=bool(token)):
        ui.cancelled('Cancelled. No settings were changed.')
        return
    ui.stage('apply')
    # save preserves existing environment protection and verifies variables and secret metadata.
    github.save(client, repo, values, token, existing)
    ui.stage('verify')
    common.save_settings(repo=repo, cluster_id=values['DOKS_CLUSTER_ID'],
                         hostname=values['REPOSILITE_HOSTNAME'])
    ui.say('GitHub production variables verified and deployment secret metadata found.')
    ui.say('Secret contents cannot be read back; DigitalOcean authentication was not tested.')
    ui.say(f'Environment: https://github.com/{repo}/settings/environments')
    ui.say('No cluster was created and no deployment was triggered.')


def main():
    return common.launch('github', execute)


if __name__ == '__main__':
    raise SystemExit(main())
