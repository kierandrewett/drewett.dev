import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from archive_references import Snapshot, ImportantLinks, check, changed, enrich, fingerprint, public_url, run


def page(words='reference evidence reliable methods results conclusions ' * 40):
    return '<html><head><title>Research reference</title></head><body><main>' + words + '</main></body></html>'


class ArchiveTests(unittest.TestCase):
    def test_freeze_failure_change_recovery_and_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = page()
            def healthy(url):
                return source.encode(), 'text/html', 'utf-8', url
            url = 'https://example.org/article'
            first = check(url, None, root, healthy)
            self.assertIn('accessed_at', first)
            path = root / 'pages' / first['id'] / 'index.html'
            original = path.read_bytes()
            def dead(url):
                raise urllib.error.HTTPError(url, 404, 'Not found', {}, None)
            once = check(url, first, root, dead)
            self.assertFalse(once['use_archive'])
            twice = check(url, json.loads(json.dumps(once)), root, dead)
            self.assertTrue(twice['use_archive'])
            self.assertEqual(path.read_bytes(), original)
            def replaced(url):
                return page('casino gambling offers bonus free money ' * 40).encode(), 'text/html', 'utf-8', url
            replaced_once = check(url, first, root, replaced)
            self.assertFalse(replaced_once['use_archive'])
            replaced_twice = check(url, replaced_once, root, replaced)
            self.assertTrue(replaced_twice['use_archive'])
            recovered = check(url, replaced_twice, root, healthy)
            self.assertFalse(recovered['use_archive'])
            self.assertEqual(recovered['accessed_at'], first['accessed_at'])
            self.assertEqual(path.read_bytes(), original)

    def test_recovers_capture_before_manifest_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def loader(url):
                return page().encode(), 'text/html', 'utf-8', url
            url = 'https://example.org/article'
            first = check(url, None, root, loader)
            def offline(url):
                raise OSError('offline')
            recovered = check(url, None, root, offline)
            self.assertEqual(recovered['accessed_at'], first['accessed_at'])
            self.assertEqual(recovered['baseline'], first['baseline'])

    def test_full_text_links_and_pdfs_are_saved_with_their_assets(self):
        source = page() + '<a href="/paper">Read full text</a><a href="/contact">Contact</a>'
        fulltext = page() + '<a href="/paper.pdf">Download PDF</a><img src="/figure.png">'
        resources = {
            'https://example.org/paper': (fulltext.encode(), 'text/html'),
            'https://example.org/paper.pdf': (b'%PDF-1.7 paper', 'application/pdf'),
            'https://example.org/figure.png': (b'figure', 'image/png'),
        }
        def loader(url):
            data, mime = resources[url]
            return data, mime, 'utf-8', url
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            snapshot = Snapshot(source, 'https://example.org/abstract', folder, loader)
            documents = snapshot.documents
            self.assertEqual(len(documents), 2)
            self.assertFalse(snapshot.context['document_errors'])
            pdf = documents['https://example.org/paper.pdf']['file']
            self.assertEqual((folder / pdf).read_bytes(), b'%PDF-1.7 paper')
            full = documents['https://example.org/paper']['file']
            self.assertIn('href="' + full + '"', ''.join(snapshot.output))
            self.assertIn('href="' + pdf + '"', (folder / full).read_text())
            self.assertNotIn('https://example.org/contact', snapshot.assets)

    def test_enrichment_preserves_original_page_and_document_timestamp(self):
        source = page() + '<a href="/paper.pdf">PDF</a>'
        def loader(url):
            if url.endswith('.pdf'):
                return b'%PDF-1.7 paper', 'application/pdf', 'utf-8', url
            return source.encode(), 'text/html', 'utf-8', url
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = 'https://example.org/abstract'
            record = check(url, None, root, loader)
            path = root / 'pages' / record['id'] / 'index.html'
            before = path.read_bytes()
            def no_fetch(url):
                raise AssertionError('Existing documents must not be fetched again')
            result = enrich(url, record, root, no_fetch)
            self.assertEqual(result['documents'], record['documents'])
            self.assertEqual(result['accessed_at'], record['accessed_at'])
            self.assertEqual(path.read_bytes(), before)

    def test_failed_paper_download_is_reported(self):
        def loader(url):
            raise OSError('HTTP 403')
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Snapshot('<a href="/paper.pdf">PDF</a>', 'https://example.org/', Path(temporary), loader)
            self.assertEqual(snapshot.context['document_errors'][0]['url'], 'https://example.org/paper.pdf')
            self.assertNotIn('file', snapshot.documents['https://example.org/paper.pdf'])

    def test_pdf_metadata_is_discovered(self):
        links = ImportantLinks('<meta name="citation_pdf_url" content="https://example.org/download?id=1">')
        self.assertIn('https://example.org/download?id=1', links.links)

    def test_recursive_assets_and_base_urls(self):
        resources = {
            'https://example.org/base/main.css': (b'@import "more.css"; body{background:url(../bg.png)}', 'text/css'),
            'https://example.org/base/more.css': (b'@font-face{src:url(font.woff2)}', 'text/css'),
            'https://example.org/base/font.woff2': (b'font', 'font/woff2'),
            'https://example.org/bg.png': (b'image', 'image/png'),
            'https://example.org/base/a.png': (b'a', 'image/png'),
            'https://example.org/base/b.png': (b'b', 'image/png'),
            'https://example.org/base/code.js': (b'alert(1)', 'text/javascript'),
        }
        def loader(url):
            data, mime = resources[url]
            return data, mime, 'utf-8', url
        source = '<link rel="stylesheet" href="main.css"><base href="/base/"><img src="a.png" srcset="a.png 1x, b.png 2x" onload="bad()"><script src="code.js"></script><meta http-equiv="refresh" content="0;url=https://bad.example">'
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            snapshot = Snapshot(source, 'https://example.org/article', folder, loader)
            self.assertFalse(snapshot.errors)
            self.assertEqual(len(snapshot.assets), len(resources))
            rendered = ''.join(snapshot.output)
            self.assertNotIn('<base', rendered)
            self.assertNotIn('onload', rendered)
            self.assertNotIn('refresh', rendered)
            for filename in snapshot.assets.values():
                self.assertTrue((folder / filename).exists())
            css = (folder / snapshot.assets['https://example.org/base/main.css']).read_text()
            self.assertNotIn('../bg.png', css)
            self.assertIn(snapshot.assets['https://example.org/bg.png'], css)

    def test_missing_asset_is_explicit_and_never_uses_live_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            def loader(url):
                raise OSError('offline')
            snapshot = Snapshot('<img src="https://example.org/missing.png">', 'https://example.org', Path(temporary), loader)
            self.assertEqual(len(snapshot.errors), 1)
            self.assertIn('src="missing-resource"', ''.join(snapshot.output))

    def test_minor_edits_and_navigation_do_not_switch(self):
        original = page(' '.join('word' + str(i) for i in range(200)))
        minor = original.replace('word60', 'correction')
        navigation = original.replace('<main>', '<nav>buy new things 2026</nav><main>')
        self.assertFalse(changed(fingerprint(original), fingerprint(minor)))
        self.assertFalse(changed(fingerprint(original), fingerprint(navigation)))

    def test_challenge_is_not_an_initial_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            def loader(url):
                return page().replace('Research reference', 'Just a moment').encode(), 'text/html', 'utf-8', url
            result = check('https://example.org', None, Path(temporary), loader)
            self.assertNotIn('accessed_at', result)
            self.assertFalse(result['use_archive'])

    def test_daily_schedule_survives_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('archive_references.fetch', return_value=(page().encode(), 'text/html', 'utf-8', 'https://example.org')):
                # Patch check because the default loader is bound at definition time.
                with patch('archive_references.rendered_check', return_value={'next_check': 9999999999, 'accessed_at': '2026-09-06', 'linked_documents_version': 1}) as checker:
                    run(root, ['https://example.org', 'https://example.org'])
                    run(root, ['https://example.org'])
                    self.assertEqual(checker.call_count, 1)

    def test_rejects_private_urls(self):
        for url in ('file:///etc/passwd', 'http://127.0.0.1/', 'http://[::1]/', 'http://user:password@example.org/'):
            with self.assertRaises(ValueError):
                public_url(url)

    def test_pdf_captures_and_detects_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def loader(url):
                return b'%PDF-1.7 original', 'application/pdf', 'utf-8', url
            url = 'https://example.org/document.pdf'
            original = check(url, None, root, loader)
            self.assertEqual(original['entry'], 'document.pdf')
            healthy = check(url, original, root, loader)
            self.assertEqual(healthy['failures'], 0)
            def replacement(url):
                return b'%PDF-1.7 changed', 'application/pdf', 'utf-8', url
            failed = check(url, original, root, replacement)
            self.assertTrue(check(url, failed, root, replacement)['use_archive'])


if __name__ == '__main__':
    unittest.main()
