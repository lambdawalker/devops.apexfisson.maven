"""Read-only Cloudflare discovery; DNS mutations belong to Pulumi."""
import json
import re
import setup_ui as ui
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener
from digitalocean import NoRedirect

BASE = 'https://api.cloudflare.com/client/v4'


class Cloudflare:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request(self, method, path):
        ui.check_cancelled()
        if method != 'GET':
            raise ui.InputError('Cloudflare mutations must go through Pulumi')
        if not path.startswith('/') or path.startswith('//') or '://' in path:
            raise ui.InputError('Refusing an API URL outside Cloudflare')
        url = BASE + path
        parts = urlsplit(url)
        if parts.netloc != 'api.cloudflare.com' or not parts.path.startswith('/client/v4/') or parts.fragment:
            raise ui.InputError('Refusing an API URL outside Cloudflare')
        # Fixed operation names identify failures without echoing tokens, query
        # strings, response bodies, or arbitrary caller-controlled paths.
        if parts.path == '/client/v4/zones':
            operation = 'zone listing'
        elif re.fullmatch(r'/client/v4/zones/[^/]+/dns_records', parts.path):
            operation = 'DNS record listing'
        else:
            operation = 'API request'
        req = Request(url, headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'})
        try:
            with self.opener.open(req, timeout=60) as response:
                result = json.load(response)
            if not result.get('success'):
                raise ui.SetupError(f'Cloudflare {operation} failed; check token scopes and selected zone')
            return result
        except HTTPError as exc:
            raise ui.SetupError(f'Cloudflare {operation} failed (HTTP {exc.code}); check token scopes and zone access') from None
        except (URLError, OSError, ValueError):
            raise ui.SetupError(f'Cloudflare {operation} failed or returned invalid JSON; check connectivity and token scopes') from None

    def list(self, path, **filters):
        items = []
        page = 1
        while True:
            result = self.request('GET', path + '?' + urlencode(dict(filters, page=page, per_page=50)))
            items.extend(result['result'])
            if page >= result.get('result_info', {}).get('total_pages', 1):
                return items
            page += 1


def discover(client, hostname, ask, previous=None, available_zones=None):
    # User- and account-owned tokens both authenticate the zone/DNS APIs.
    # The user-only verification endpoint rejects valid account tokens. The
    # authenticated reads below validate access and still fail closed on denial;
    # successful discovery does not prove DNS write permission.
    zones = [z for z in (available_zones if available_zones is not None else client.list('/zones', status='active'))
             if hostname == z['name'] or hostname.endswith('.' + z['name'])]
    if not zones:
        raise ui.InputError('No active Cloudflare zone containing the hostname is visible to this token')
    zones.sort(key=lambda z: (-len(z['name']), z['name']))
    zone_name = ask('Cloudflare zone (' + ', '.join(z['name'] for z in zones) + ')',
                    previous['zoneName'] if previous else zones[0]['name'])
    zone = next((z for z in zones if z['name'] == zone_name), None)
    if not zone or (previous and zone['id'] != previous['zoneId']):
        raise ui.InputError('Selected Cloudflare zone differs from the stack or is not available')
    records = [r for r in client.list('/zones/' + zone['id'] + '/dns_records', name=hostname)
               if r['name'].rstrip('.').lower() == hostname]
    if any(r['type'] != 'A' for r in records) or len(records) > 1:
        raise ui.InputError('Hostname has conflicting DNS records; refusing to overwrite CNAME or other records')
    record_id = records[0]['id'] if records else None
    if previous:
        # State verification separately covers records created after initial config save.
        if previous.get('recordId') and record_id != previous['recordId']:
            raise ui.InputError('Managed Cloudflare DNS record is missing or changed')
    if record_id and not (previous or {}).get('recordId'):
        raise ui.InputError('Hostname has an existing unmanaged A record. Choose an unused hostname or perform a deliberate separately-reviewed DNS migration before rerunning.')
    ui.say('Choose a monitored email address for ACME account and certificate notices.')
    email = ask('ACME account email', previous['acmeEmail'] if previous else 'hostmaster@' + zone['name'])
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise ui.InputError('Enter a valid ACME account email')
    return {'zoneId': zone['id'], 'zoneName': zone['name'], 'recordId': record_id, 'acmeEmail': email}
