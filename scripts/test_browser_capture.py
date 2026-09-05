import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from archive_references import Snapshot, check, public_url, run
from browser_capture import BrowserCapture


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Install scripts/requirements.txt and Chromium')
class BrowserCaptureTests(unittest.TestCase):
    def test_delayed_js_lazy_content_assets_and_shadow_dom(self):
        origin = 'https://archive-fixture.example'
        source = '''<!doctype html><html><head><title>Hydrated research</title><style id="generated"></style></head><body>
        <main id="article">Loading</main><div style="height:2400px"></div><section id="lazy">Waiting</section><custom-paper></custom-paper>
        <script>
        document.querySelector('custom-paper').attachShadow({mode:'open'}).innerHTML='<p>Shadow research evidence</p>';
        document.querySelector('#generated').sheet.insertRule('main { color: rgb(12, 34, 56); }');
        new IntersectionObserver(entries => { if(entries.some(e => e.isIntersecting)) document.querySelector('#lazy').textContent='Lazy section loaded'; }).observe(document.querySelector('#lazy'));
        setTimeout(async () => {
            const text=await (await fetch('/article.json')).json();
            document.querySelector('#article').innerHTML='<p>'+text+'</p><a href="/paper.pdf">Download PDF</a><img src="/figure.svg">';
        }, 1500);
        </script></body></html>'''
        evidence = 'Hydrated research evidence appears only after JavaScript. ' * 12
        pdf = b'%PDF-1.7 fixture paper'
        resources = {
            '/': ('text/html', source), '/article.json': ('application/json', json.dumps(evidence)),
            '/paper.pdf': ('application/pdf', pdf),
            '/figure.svg': ('image/svg+xml', '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20"/></svg>'),
        }
        def validator(url):
            if not url.startswith(origin + '/'):
                public_url(url)
        def fixture(route):
            path = route.request.url.removeprefix(origin)
            mime, body = resources.get(path, ('text/plain', 'missing'))
            route.fulfill(status=200 if path in resources else 404, content_type=mime, body=body)
        with BrowserCapture(validator=validator) as loader:
            loader.context.route(origin + '/**', fixture)
            data, mime, _, final = loader.document(origin + '/')
            rendered = data.decode()
            self.assertIn(evidence, rendered)
            self.assertIn('Lazy section loaded', rendered)
            self.assertIn('Shadow research evidence', rendered)
            self.assertIn('rgb(12, 34, 56)', rendered)
            self.assertIn('/paper.pdf', rendered)
            self.assertTrue(loader.info[origin + '/']['render_settled'])
            self.assertNotIn(evidence, loader.raw_documents[origin + '/'].decode())
            self.assertIn(origin + '/figure.svg', loader.cache)
            # Seed the PDF in the browser cache as if fetched by its download link.
            loader.cache[origin + '/paper.pdf'] = pdf, 'application/pdf', 'utf-8', origin + '/paper.pdf'
            with tempfile.TemporaryDirectory() as temporary:
                snapshot = Snapshot(rendered, final, Path(temporary), loader)
                self.assertFalse(snapshot.errors)
                filename = snapshot.documents[origin + '/paper.pdf']['file']
                self.assertEqual((Path(temporary) / filename).read_bytes(), pdf)


class VersionTests(unittest.TestCase):
    def test_rendered_upgrade_preserves_previous_url_and_baseline(self):
        source = '<title>Reference</title><p>' + 'Original research evidence and results. ' * 20 + '</p>'
        url = 'https://example.org/reference'
        def http_loader(url):
            return source.encode(), 'text/html', 'utf-8', url
        class Rendered:
            def __enter__(self):
                self.info = {url: {'capture_method': 'chromium', 'render_settled': True}}
                self.raw_documents = {url: source.encode()}
                return self
            def __exit__(self, *args):
                pass
            def document(self, url):
                return (source + '<p>Hydrated full text.</p>').encode(), 'text/html', 'utf-8', url
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = check(url, None, root, http_loader)
            original['next_check'] = 0
            (root / 'manifest.json').write_text(json.dumps({url: original}))
            saved = (root / 'pages' / original['id'] / 'index.html').read_bytes()
            with patch('browser_capture.BrowserCapture', Rendered):
                run(root, [url])
            records = json.loads((root / 'manifest.json').read_text())
            upgraded = records[url]
            self.assertNotEqual(upgraded['id'], original['id'])
            self.assertEqual(upgraded['capture_method'], 'chromium')
            self.assertEqual(records['archive:' + original['id']]['baseline'], original['baseline'])
            self.assertEqual((root / 'pages' / original['id'] / 'index.html').read_bytes(), saved)
            self.assertIn(b'Hydrated full text.', (root / 'pages' / upgraded['id'] / 'index.html').read_bytes())

    def test_browser_failure_does_not_mark_a_source_dead(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = 'https://example.org/reference'
            original = {'id': 'original', 'accessed_at': '2026-09-06', 'baseline': {'type': 'html'}, 'use_archive': False, 'failures': 0, 'linked_documents_version': 1}
            (root / 'manifest.json').write_text(json.dumps({url: original}))
            with patch('browser_capture.BrowserCapture', side_effect=RuntimeError('Chromium unavailable')):
                run(root, [url])
            record = json.loads((root / 'manifest.json').read_text())[url]
            self.assertEqual(record['id'], 'original')
            self.assertFalse(record['use_archive'])
            self.assertEqual(record['failures'], 0)
            self.assertIn('Chromium unavailable', record['render_error'])


if __name__ == '__main__':
    unittest.main()
