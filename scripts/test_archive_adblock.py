import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import adblock
from archive_adblock import load_engine
from browser_capture import BrowserCapture


class AdblockTests(unittest.TestCase):
    def test_filters_refresh_and_offline_fallback(self):
        rules = b'[Adblock Plus 2.0]\n||ads.example^\n' + b'! padding\n' * 120
        with tempfile.TemporaryDirectory() as temporary:
            with patch('archive_adblock.fetch', return_value=(rules,)) as fetch:
                engine = load_engine(temporary)
                self.assertEqual(fetch.call_count, 3)
            self.assertTrue(engine.check_network_urls('https://ads.example/ad.js', 'https://paper.example', 'script').matched)
            with patch('archive_adblock.fetch', side_effect=OSError('offline')) as fetch:
                load_engine(temporary)
                fetch.assert_not_called()
                import os
                for path in Path(temporary).glob('*.txt'):
                    os.utime(path, (0, 0))
                load_engine(temporary)
                self.assertEqual(fetch.call_count, 3)
        with tempfile.TemporaryDirectory() as temporary, patch('archive_adblock.fetch', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                load_engine(temporary)

    def test_network_and_cosmetic_filtering_preserves_paper(self):
        origin = 'https://paper.example'
        filters = adblock.FilterSet()
        filters.add_filters(['||ads.example^', '##.advertisement', 'paper.example###consent-overlay', '##.keep', 'paper.example#@#.keep'])
        source = '''<!doctype html><html><body style="overflow:hidden;height:100vh">
        <main>Research evidence remains accessible.</main><p class="keep">Excepted content</p>
        <div class="advertisement">Advert slot</div><div id="consent-overlay" style="position:fixed;inset:0">Cookie overlay</div>
        <script src="https://ads.example/ad.js"></script><img src="https://ads.example/ad.png">
        <script>setTimeout(() => { const ad=document.createElement('div'); ad.className='advertisement'; ad.textContent='Late advert'; document.body.append(ad); }, 1500);</script>
        </body></html>'''
        reached = []
        def upstream(route):
            reached.append(route.request.url)
            route.fulfill(content_type='text/html', body=source)
        with BrowserCapture(validator=lambda url: None, adblock_engine=adblock.Engine(filters)) as loader:
            # The interception route runs first, then falls back to this fixture transport.
            loader.context.unroute('**/*')
            loader.context.route('**/*', upstream)
            original_route = loader._route
            class Route:
                def __init__(self, route): self.route, self.request = route, route.request
                def abort(self): self.route.abort()
                def continue_(self): self.route.fallback()
            loader.context.route('**/*', lambda route: original_route(Route(route)))
            with patch('browser_capture.fetch', side_effect=OSError('browser only')):
                data = loader.document(origin + '/')[0].decode()
            self.assertEqual(reached, [origin + '/'])
            self.assertEqual(len(loader.blocked), 2)
            self.assertNotIn('Cookie overlay', data)
            self.assertNotIn('Advert slot', data)
            self.assertNotIn('<div class="advertisement">Late advert', data)
            self.assertIn('Research evidence remains accessible', data)
            self.assertIn('Excepted content', data)
            self.assertIn('overflow: auto !important', data)
            self.assertEqual(loader.info[origin + '/']['adblock_removed'], 3)
            with patch('browser_capture.fetch') as fetch:
                with self.assertRaisesRegex(ValueError, 'Intentionally blocked'):
                    loader('https://ads.example/ad.png')
                fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
