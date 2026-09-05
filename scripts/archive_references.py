#!/usr/bin/env python3
"""Capture cited web pages and check immutable baselines. Uses the standard library."""
import concurrent.futures
import datetime
import fcntl
import hashlib
import html
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DAY = 86400
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_ASSETS = 256
CSS_URL = re.compile(r'url\(\s*([\'"]?)(.*?)\1\s*\)', re.I)
CSS_IMPORT = re.compile(r'(@import\s+)([\'"])(.*?)\2', re.I)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def key(url):
    return hashlib.sha256(url.encode()).hexdigest()


def public_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Only public HTTP(S) URLs are supported')
    addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Private or reserved network address')
    return url


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    public_url(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'kierand.dev-reference-archive/1.0', 'Accept-Encoding': 'identity'})
    with urllib.request.build_opener(PublicRedirect()).open(request, timeout=15) as response:
        data = response.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise ValueError('Resource exceeds 16 MiB')
        return data, response.headers.get_content_type(), response.headers.get_content_charset() or 'utf-8', response.url


class Text(HTMLParser):
    """Compare prose, ignoring navigation, scripts, styles and other page chrome."""
    OMIT = {'script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript', 'svg', 'template'}

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts = []
        self.title = []
        self.in_title = False
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag in self.OMIT:
            self.depth += 1
        if tag == 'title':
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in self.OMIT:
            self.depth = max(0, self.depth - 1)
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if not self.depth:
            self.parts.append(data)

    def words(self):
        return re.findall(r'\w+', ' '.join(self.parts).casefold())


def fingerprint(source):
    text = Text(source)
    words = text.words()
    shingles = sorted({key(' '.join(words[i:i + 5]))[:16] for i in range(max(1, len(words) - 4))})
    return {'title': ' '.join(text.title).strip(), 'words': len(words), 'shingles': shingles}


def valid_page(source):
    fp = fingerprint(source)
    title = fp['title'].casefold()
    blocked = ('just a moment', 'access denied', 'verify you are human', 'robot check', 'page not found', '404 not found', 'attention required', 'security check', 'sign in', 'log in', 'checking your browser')
    if fp['words'] < 30 or any(x in title for x in blocked):
        raise ValueError('Page is empty, an error, or an access challenge')
    return fp


def changed(baseline, current):
    a, b = set(baseline['shingles']), set(current['shingles'])
    # Compare with the original capture, never yesterday's version. Ignore small edits.
    similarity = len(a & b) / max(1, len(a | b))
    return similarity < 0.45


class ImportantLinks(HTMLParser):
    """Select full text and document downloads, not navigation or related articles."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.links = {}
        self.href = None
        self.label = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.href = attrs.get('href')
            self.label = [attrs.get('title', ''), attrs.get('aria-label', '')]
        elif tag == 'meta' and attrs.get('name', '').lower() in ('citation_pdf_url', 'citation_fulltext_html_url'):
            value = attrs.get('content')
            if value:
                self.links[value] = 'Paper (PDF)' if attrs.get('name', '').lower() == 'citation_pdf_url' else 'Full text'

    def handle_data(self, data):
        if self.href:
            self.label.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            label = ' '.join(self.label).strip()
            path = urllib.parse.urlsplit(self.href).path.lower()
            if re.search(r'\.(pdf|epub|docx?)(?:$)', path) or re.search(r'/(?:e?pdf|fulltext|full-text)/', path) or re.search(r'\b(?:pdf|full[ -]text|full article|(?:read|view|download) (?:the )?(?:paper|article|document))\b', label, re.I):
                self.links[self.href] = label or path.rsplit('/', 1)[-1]
            self.href = None



def capture_context(documents=()):
    return {
        'assets': {d['url']: d['file'] for d in documents},
        'errors': [], 'documents': {d['url']: d for d in documents},
        'document_errors': [], 'total': 0, 'deadline': time.monotonic() + 180,
    }


class Snapshot(HTMLParser):
    """Rewrite resource URLs into one local asset directory, including nested CSS."""
    def __init__(self, source, base, folder, loader=fetch, context=None, depth=0, documents_only=False):
        super().__init__(convert_charrefs=False)
        self.base = base
        self.folder = folder
        self.loader = loader
        self.output = []
        self.context = context if context is not None else capture_context()
        self.assets = self.context['assets']
        self.errors = self.context['errors']
        self.documents = self.context['documents']
        self.depth = depth
        self.in_style = False
        # The first base URL applies to the entire document, including earlier tags.
        base_match = re.search(r'<base\b[^>]*href\s*=\s*[\'"]([^\'"]+)', source, re.I)
        if base_match:
            self.base = urllib.parse.urljoin(base, html.unescape(base_match[1]))
        # Fetch the paper before decorative assets consume the capture budget.
        if self.depth < 2:
            for href, label in ImportantLinks(source).links.items():
                self.document(href, label)
        if not documents_only:
            self.feed(source)

    def document(self, href, label):
        url = urllib.parse.urldefrag(urllib.parse.urljoin(self.base, href))[0]
        if url in self.documents:
            return
        if len(self.documents) >= 12:
            self.context['document_errors'].append({'url': url, 'error': 'Linked document limit reached'})
            return
        self.documents[url] = {'url': url, 'title': label}
        filename = self.asset(url, document=True)
        if filename == 'missing-resource':
            self.context['document_errors'].append({'url': url, 'error': self.errors[-1]['error'] if self.errors else 'Unavailable'})
        else:
            self.documents[url].update(file=filename, captured_at=now())

    def asset(self, value, base=None, document=False):
        value = value.strip()
        if not value or value.startswith(('data:', '#')):
            return value
        url, fragment = urllib.parse.urldefrag(urllib.parse.urljoin(base or self.base, value))
        if url in self.assets:
            return self.assets[url] + ('#' + fragment if fragment else '')
        try:
            if len(self.assets) >= MAX_ASSETS or self.context['total'] >= MAX_TOTAL or time.monotonic() > self.context['deadline']:
                raise ValueError('Capture resource budget reached')
            data, mime, charset, final = self.loader(url)
            if self.context['total'] + len(data) > MAX_TOTAL:
                raise ValueError('Capture exceeds 64 MiB')
            self.context['total'] += len(data)
            if document:
                if mime in ('text/html', 'application/xhtml+xml'):
                    valid_page(data.decode(charset, errors='replace'))
                elif mime == 'application/pdf':
                    if not data.startswith(b'%PDF-'):
                        raise ValueError('Invalid linked PDF')
                elif mime not in ('application/epub+zip', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'):
                    raise ValueError('Unsupported linked document: ' + mime)
            extension = {'text/html': '.html', 'application/xhtml+xml': '.html', 'application/epub+zip': '.epub', 'application/msword': '.doc', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx', 'text/css': '.css', 'text/javascript': '.js', 'application/javascript': '.js', 'image/svg+xml': '.svg', 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/gif': '.gif', 'font/woff2': '.woff2', 'font/woff': '.woff', 'application/pdf': '.pdf'}.get(mime, '.bin')
            name = key(url) + extension
            self.assets[url] = name
            if mime == 'text/css':
                data = self.css(data.decode(charset, errors='replace'), final).encode()
            elif mime in ('text/html', 'application/xhtml+xml'):
                if self.depth >= 2:
                    raise ValueError('Linked page depth limit reached')
                child = Snapshot(data.decode(charset, errors='replace'), final, self.folder, self.loader, self.context, self.depth + 1)
                data = ''.join(child.output).encode()
            # Enrichment can add documents but must never overwrite saved assets.
            path = self.folder / name
            if not path.exists():
                temporary = path.with_name(path.name + '.tmp')
                temporary.write_bytes(data)
                temporary.replace(path)
            return name + ('#' + fragment if fragment else '')
        except Exception as error:
            self.errors.append({'url': url, 'error': str(error)})
            self.assets[url] = 'missing-resource'
            return 'missing-resource'

    def css(self, source, base=None):
        source = CSS_URL.sub(lambda m: 'url("' + self.asset(m[2], base) + '")', source)
        return CSS_IMPORT.sub(lambda m: m[1] + '"' + self.asset(m[3], base) + '"', source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'base' or (tag == 'meta' and values.get('http-equiv', '').lower() in ('refresh', 'content-security-policy')):
            return
        if tag == 'style':
            self.in_style = True
        output = []
        for name, value in attrs:
            if name.startswith('on') or name in ('integrity', 'crossorigin', 'nonce', 'srcdoc'):
                continue
            if value is None:
                output.append(name)
                continue
            if name == 'style':
                value = self.css(value)
            elif name in ('src', 'poster', 'background') or (name == 'href' and tag == 'link' and any(x in values.get('rel', '').lower().split() for x in ('stylesheet', 'icon', 'preload', 'modulepreload'))) or (name in ('href', 'xlink:href') and tag in ('image', 'use')) or (name == 'data' and tag == 'object'):
                value = self.asset(value)
            elif name == 'srcset':
                # Data URLs can contain commas; retain them unchanged.
                if not value.lstrip().startswith('data:'):
                    value = ', '.join(' '.join([self.asset(p[0]), *p[1:]]) for part in value.split(',') if (p := part.split()))
            elif name in ('href', 'action', 'formaction') and not value.startswith('#'):
                value = urllib.parse.urljoin(self.base, value)
                document = self.documents.get(urllib.parse.urldefrag(value)[0], {})
                if tag == 'a' and document.get('file'):
                    value = document['file']
            output.append(name + '="' + html.escape(value, quote=True) + '"')
        self.output.append('<' + tag + (' ' + ' '.join(output) if output else '') + '>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'style':
            self.in_style = False
        if tag != 'base':
            self.output.append('</' + tag + '>')

    def handle_data(self, data):
        self.output.append(self.css(data) if self.in_style else data)

    def handle_entityref(self, name):
        self.output.append('&' + name + ';')

    def handle_charref(self, name):
        self.output.append('&#' + name + ';')

    def handle_decl(self, decl):
        self.output.append('<!' + decl + '>')


def record_failure(record, reason):
    record['failures'] = record.get('failures', 0) + 1
    record['reason'] = reason
    if record.get('accessed_at') and record['failures'] >= 2:
        record['use_archive'] = True
    return record


def check(url, old, root, loader=fetch):
    record = dict(old or {'id': key(url), 'url': url, 'use_archive': False, 'failures': 0})
    record['checked_at'] = now()
    record['next_check'] = time.time() + DAY
    try:
        folder = root / 'pages' / record['id']
        if not record.get('accessed_at') and folder.exists():
            metadata = json.loads((folder / 'capture.json').read_text())
            record.update(metadata, failures=0, reason='', use_archive=False)
            return record
        if record.get('accessed_at') and record.get('document_errors'):
            record = enrich(url, record, root, loader)
        data, mime, charset, final = loader(url)
        if mime not in ('text/html', 'application/xhtml+xml', 'application/pdf'):
            raise ValueError('Unsupported document type: ' + mime)
        if mime == 'application/pdf':
            if not data.startswith(b'%PDF-'):
                raise ValueError('Invalid PDF response')
            fp = {'digest': key(data.hex()), 'type': 'pdf'}
            source = None
        else:
            source = data.decode(charset, errors='replace')
            fp = valid_page(source)
            fp['type'] = 'html'
        if record.get('accessed_at'):
            baseline = record['baseline']
            different = baseline.get('type') != fp['type'] or (baseline.get('digest') != fp.get('digest') if fp['type'] == 'pdf' else changed(baseline, fp))
            if different:
                return record_failure(record, 'Source content changed substantially')
            record.update(failures=0, reason='', use_archive=False)
            return record
        folder = root / 'pages' / record['id']
        staging = root / ('pending-' + record['id'])
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        try:
            (staging / 'original.bin').write_bytes(data)
            errors = []
            documents = []
            document_errors = []
            if source is None:
                (staging / 'document.pdf').write_bytes(data)
                entry = 'document.pdf'
            else:
                snapshot = Snapshot(source, final, staging, loader)
                (staging / 'index.html').write_text(''.join(snapshot.output), encoding='utf-8')
                errors = snapshot.errors
                documents = [d for d in snapshot.documents.values() if d.get('file')]
                document_errors = snapshot.context['document_errors']
                entry = 'index.html'
            captured = now()
            metadata = {'url': url, 'final_url': final, 'accessed_at': captured, 'entry': entry, 'asset_errors': errors, 'baseline': fp, 'charset': charset, 'documents': documents, 'document_errors': document_errors, 'linked_documents_version': 1}
            (staging / 'capture.json').write_text(json.dumps(metadata, indent=2))
            folder.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(folder)
            record.update(metadata, failures=0, reason='', use_archive=False)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    except Exception as error:
        record_failure(record, str(error))
    return record


def enrich(url, record, root, loader=fetch):
    record = dict(record)
    folder = root / 'pages' / record['id']
    if record.get('entry') == 'index.html':
        source = (folder / 'original.bin').read_bytes().decode(record.get('charset', 'utf-8'), errors='replace')
        snapshot = Snapshot(source, record.get('final_url', url), folder, loader, context=capture_context(record.get('documents', [])), documents_only=True)
        record['documents'] = [d for d in snapshot.documents.values() if d.get('file')]
        record['document_errors'] = snapshot.context['document_errors']
    record['linked_documents_version'] = 1
    # The supplemental record leaves the original page and its timestamp intact.
    temporary = folder / 'documents.tmp'
    temporary.write_text(json.dumps({k: record.get(k, []) for k in ('documents', 'document_errors')}, indent=2))
    temporary.replace(folder / 'documents.json')
    return record


def run(root, urls):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'worker.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        manifest = root / 'manifest.json'
        # Refuse to overwrite damaged state. Existing captures are irreplaceable.
        records = json.loads(manifest.read_text()) if manifest.exists() else {}
        due = [u for u in sorted(set(urls)) if records.get(u, {}).get('next_check', 0) <= time.time() or (records.get(u, {}).get('accessed_at') and not records[u].get('linked_documents_version'))]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {}
            for u in due:
                previous = records.get(u, {})
                action = enrich if previous.get('accessed_at') and not previous.get('linked_documents_version') else check
                jobs[pool.submit(action, u, previous, root)] = u
            for future in concurrent.futures.as_completed(jobs):
                url = jobs[future]
                records[url] = future.result()
                temporary = root / 'manifest.tmp'
                temporary.write_text(json.dumps(records, indent=2), encoding='utf-8')
                temporary.replace(manifest)
                record = records[url]
                status = 'archive' if record.get('use_archive') else ('captured' if record.get('accessed_at') else 'unavailable')
                print('[archive] ' + status + ' ' + url + (' - ' + record['reason'] if record.get('reason') else ''), flush=True)


if __name__ == '__main__':
    socket.setdefaulttimeout(15)
    run(Path(sys.argv[1]), json.load(sys.stdin))
