"""Read-only, credential-safe DigitalOcean API client used by the setup wizard."""
from datetime import datetime, timezone
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

BASE = 'https://api.digitalocean.com/v2'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(newurl, code, 'Redirect refused', headers, fp)


class DigitalOcean:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, body=None):
        if method != 'GET' or body is not None:
            raise ValueError('DigitalOcean mutations must go through Pulumi')
        url = path if path.startswith('https://') else BASE + path
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.netloc != 'api.digitalocean.com'
                or not parts.path.startswith('/v2/') or parts.fragment):
            raise ValueError('Refusing an API URL outside DigitalOcean')
        request = Request(url, method=method, headers={
            'Authorization': 'Bearer ' + self.token,
            'Content-Type': 'application/json', 'Accept': 'application/json',
            'User-Agent': 'apexfission-maven-setup',
        }, data=None if body is None else json.dumps(body).encode('utf-8'))
        try:
            with self.opener.open(request, timeout=60) as response:
                return json.load(response)
        except HTTPError as exc:
            # Never include response body, request headers, or server error text.
            raise RuntimeError(f'DigitalOcean {method} failed (HTTP {exc.code}). '
                               'Check token scopes, resource settings and account limits. '
                               'If creating a resource, inspect the control panel before rerunning.') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError('DigitalOcean request failed or returned invalid JSON. '
                               'Completion is unknown; check the control panel before rerunning.') from None

    def list(self, path, key):
        result = []
        next_page = path + '?per_page=200'
        visited = set()
        while next_page:
            if next_page in visited:
                raise RuntimeError('DigitalOcean returned a repeated pagination link')
            visited.add(next_page)
            page = self.request('GET', next_page)
            result.extend(page[key])
            next_page = page.get('links', {}).get('pages', {}).get('next')
        return result

    def wait(self, path, key, timeout=1800):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.request('GET', path)[key]
            state = (item.get('status', {}).get('state') if key == 'kubernetes_cluster'
                     else item.get('state'))
            if state == ('running' if key == 'kubernetes_cluster' else 'verified'):
                return item
            if state in ('error', 'deleted', 'deleting'):
                raise RuntimeError(f'DigitalOcean resource entered {state}; inspect the control panel')
            print(f'Waiting for {key}: {state or "unknown"} ...', flush=True)
            time.sleep(15)
        raise RuntimeError(f'Waiting for {key} timed out. Resource is retained; rerun to resume.')


def named(items, name):
    matches = [item for item in items if item['name'] == name]
    if len(matches) > 1:
        raise ValueError(f'There are multiple resources named {name}; use a unique name in DigitalOcean')
    return matches[0] if matches else None


def latest_version(versions):
    slugs = [item['slug'] for item in versions
             if re.fullmatch(r'\d+\.\d+\.\d+-do\.\d+', item['slug'])]
    if not slugs:
        raise RuntimeError('DigitalOcean returned no stable Kubernetes versions')
    return max(slugs, key=lambda slug: tuple(map(int, re.findall(r'\d+', slug))))


def check_certificate(cert, hostname):
    def covers(name):
        name = name.lower()
        return name == hostname or (name.startswith('*.')
            and hostname.count('.') == name.count('.') and hostname.endswith(name[1:]))
    if not any(covers(name) for name in cert.get('dns_names', [])):
        raise ValueError('Certificate does not cover the Maven hostname; choose a different certificate name')
    if cert.get('state') == 'error':
        raise ValueError('Certificate is in error state; repair it in DigitalOcean before rerunning')
    if cert.get('state') == 'verified':
        expiry = cert.get('not_after')
        if not expiry:
            raise ValueError('Certificate has no expiry metadata; inspect it in DigitalOcean')
        if datetime.fromisoformat(expiry.replace('Z', '+00:00')) <= datetime.now(timezone.utc):
            raise ValueError('Certificate has expired; renew it before continuing')
