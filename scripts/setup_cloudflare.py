#!/usr/bin/env python3
"""Independently review and configure one Cloudflare DNS-only A record."""
import ipaddress
import re
import sys

from cloudflare import Cloudflare
import independent_setup as setup
import setup_ui as ui


def validate(values, zones):
    errors = {}
    hostname = values['hostname'].lower()
    labels = hostname.split('.')
    if (len(hostname) > 253 or len(labels) < 2 or
            any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                for label in labels)):
        errors['hostname'] = 'Enter a DNS hostname without a scheme, port, path or trailing dot.'
    try:
        ipaddress.IPv4Address(values['ip'])
    except ipaddress.AddressValueError:
        errors['ip'] = 'Enter the gateway IPv4 address.'
    zone = next((z for z in zones if z['name'] == values['zone']), None)
    if not zone or not (hostname == zone['name'] or hostname.endswith('.' + zone['name'])):
        errors['zone'] = 'Choose an active zone containing this hostname.'
    return errors


def records_for(client, zone_id, hostname):
    records = [r for r in client.list('/zones/' + zone_id + '/dns_records', name=hostname)
               if r['name'].rstrip('.').lower() == hostname]
    if len(records) > 1 or any(r['type'] != 'A' for r in records):
        raise ui.InputError('Hostname has conflicting DNS records; resolve them before continuing.')
    return records


def run(args):
    ui.stage('credentials')
    token = setup.credentials(args, 'cloudflare')
    reader = Cloudflare(token)
    writer = setup.Api('cloudflare', token)
    defaults = setup.load_settings()
    ui.stage('discovery')
    zones = reader.list('/zones', status='active')
    if not zones:
        raise ui.InputError('No active Cloudflare zones are visible to this token.')
    zones.sort(key=lambda z: (-len(z['name']), z['name']))
    hostname = defaults.get('hostname') or 'maven.apexfission.com'
    matching = [z for z in zones if hostname == z['name'] or hostname.endswith('.' + z['name'])]
    preferred = next((z for z in matching if z['id'] == defaults.get('zone_id')), None)
    zone = preferred or (matching or zones)[0]
    values = setup.fields('Cloudflare DNS', [
        ui.Field('hostname', 'Maven hostname', hostname),
        ui.Field('ip', 'Gateway IPv4 address', defaults.get('ip', ''),
                 help='Copy the public gateway IP from DigitalOcean setup or your existing gateway.'),
        ui.Field('zone', 'Cloudflare zone', zone['name'], choices=tuple(z['name'] for z in zones)),
    ], validate=lambda v: validate(v, zones),
        description='Configure one DNS-only A record. Keep your existing Cloudflare nameservers.')
    # Validate again so alternate form implementations cannot bypass safety checks.
    errors = validate(values, zones)
    if errors:
        raise ui.InputError(' '.join(errors.values()))
    hostname = values['hostname'].lower()
    address = values['ip']
    zone = next(z for z in zones if z['name'] == values['zone'])
    zone_id = zone['id']
    records = records_for(reader, zone_id, hostname)
    existing = records[0] if records else None
    if existing and existing['content'] == address and existing.get('proxied') is False:
        ui.say('The DNS-only A record already points to ' + address + '.')
        record_id = existing['id']
    else:
        action = 'Update' if existing else 'Create'
        old = (f"Current: A {existing['content']}, proxied={existing.get('proxied')}.\n"
               if existing else 'Current: no record at this exact hostname.\n')
        if not setup.approve(action + ' Cloudflare DNS record',
                             f"Zone: {zone['name']} ({zone_id})\nHostname: {hostname}\n" + old +
                             f'Proposed: A {address}, DNS-only (proxy disabled).'):
            ui.say('Cloudflare DNS setup cancelled before changes.')
            return
        # A change during review invalidates the reviewed operation.
        if records_for(reader, zone_id, hostname) != records:
            raise ui.InputError('DNS records changed during review; rerun to review the current record.')
        payload = {'type': 'A', 'name': hostname, 'content': address, 'proxied': False,
                   'ttl': existing.get('ttl', 1) if existing else 1}
        if existing:
            for key in ('comment', 'tags', 'settings'):
                if key in existing:
                    payload[key] = existing[key]
        path = '/zones/' + zone_id + '/dns_records'
        ui.stage('apply')
        result = writer.request('PUT' if existing else 'POST',
                                path + '/' + existing['id'] if existing else path, payload)
        record_id = (result or {}).get('result', {}).get('id')
        if not record_id or (existing and record_id != existing['id']):
            raise ui.SetupError('Cloudflare did not confirm the expected record ID; inspect DNS before rerunning.')
    ui.stage('verify')
    verified = records_for(reader, zone_id, hostname)
    if (len(verified) != 1 or verified[0]['id'] != record_id or
            verified[0]['content'] != address or verified[0].get('proxied') is not False):
        raise ui.SetupError('Cloudflare DNS verification failed; inspect the record before rerunning.')
    setup.save_settings(hostname=hostname, ip=address, zone_id=zone_id, record_id=record_id)
    ui.say(f'Verified Cloudflare DNS-only A record: {hostname} → {address}.')


if __name__ == '__main__':
    sys.exit(setup.launch('Cloudflare DNS setup', run))
