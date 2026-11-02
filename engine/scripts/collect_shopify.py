#!/usr/bin/env python3
"""Acquires public Shopify site data only; no fixture fallback or hidden network retries."""
import argparse
import hashlib
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from ipaddress import ip_address
from xml.etree import ElementTree

HEADERS = {'User-Agent': 'BuyerMagazineStudio/1.0 (public editorial research)', 'Accept': 'application/json,text/xml,application/xml,text/html;q=0.8'}


def validate_public_https(value):
    parsed = urllib.parse.urlsplit(value)
    host = (parsed.hostname or '').lower().rstrip('.')
    if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Only uncredentialed public HTTPS URLs on standard port are permitted')
    if host in ('localhost', 'metadata.google.internal') or host.endswith(('.localhost', '.local', '.internal', '.test')) or '.' not in host:
        raise ValueError('Not a public hostname')
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('IP literals are not accepted')
    if parsed.query and any(k in parsed.query.lower() for k in ('token=', 'access_key=', 'api_key=', 'password=', 'secret=')):
        raise ValueError('Credential-like query is forbidden')
    return parsed


class PublicOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get_url(url, timeout):
    validate_public_https(url)
    opener = urllib.request.build_opener(PublicOnlyRedirect())
    request = urllib.request.Request(url, headers=HEADERS)
    with opener.open(request, timeout=timeout) as response:
        actual = response.geturl()
        validate_public_https(actual)
        data = response.read(12 * 1024 * 1024 + 1)
        if len(data) > 12 * 1024 * 1024:
            raise ValueError('12MB response bound exceeded')
        return data, actual, response.headers.get('content-type', '')


def site_url(value):
    parts = validate_public_https(value)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, '', '', '')).rstrip('/')


def discover(xml_text):
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    urls = [(e.text or '').strip() for e in root.iter() if e.tag.endswith('loc')]
    return [u for u in urls if u.startswith('https://') and re.search(r'(pages|collections|products|sitemap)', u, re.I)][:1000]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--site', required=True, help='Official public https:// origin')
    parser.add_argument('--out', required=True)
    parser.add_argument('--timeout', type=int, default=15)
    parser.add_argument('--allow-partial', action='store_true', help='Keep source files even if Shopify endpoint is blocked')
    args = parser.parse_args(argv)
    origin = site_url(args.site)
    dest = pathlib.Path(args.out); dest.mkdir(parents=True, exist_ok=True)
    manifest = {'site': origin, 'retrieved_at_utc': datetime.now(timezone.utc).isoformat(), 'files': {}, 'errors': {}, 'status': 'incomplete'}
    resources = [('sitemap.xml', origin + '/sitemap.xml'), ('products.json', origin + '/products.json?limit=250')]
    for filename, url in resources:
        try:
            raw, final_url, content_type = get_url(url, args.timeout)
            if filename.endswith('.json'):
                data = json.loads(raw)
                if not isinstance(data, dict) or not isinstance(data.get('products'), list):
                    raise ValueError('Expected Shopify {"products": [...]} payload')
                manifest['catalog_page_limit'] = 250
                manifest['catalog_completeness'] = 'potentially_truncated' if len(data['products']) >= 250 else 'first_page_below_limit_not_guaranteed_complete'
            else:
                manifest['discovered_urls'] = discover(raw)
            (dest / filename).write_bytes(raw)
            manifest['files'][filename] = {'requested': url, 'final': final_url, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'content_type': content_type}
        except (ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            manifest['errors'][filename] = f'{type(error).__name__}: {error}'[:500]
    manifest['status'] = 'retrieved_with_limits' if 'products.json' in manifest['files'] else 'blocked'
    (dest / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': manifest['status'], 'files': list(manifest['files']), 'errors': manifest['errors']}, ensure_ascii=False))
    if manifest['status'] == 'blocked' and not args.allow_partial:
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
