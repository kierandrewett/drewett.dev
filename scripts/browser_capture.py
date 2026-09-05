"""Render references in an isolated Chromium session before freezing their DOM."""
import os
import re
import shutil
import time
from urllib.parse import urldefrag

from archive_references import MAX_ASSETS, MAX_FILE, MAX_TOTAL, fetch, public_url


class BrowserCapture:
    def __init__(self, validator=public_url, adblock_engine=None, filter_directory=None):
        self.adblock = adblock_engine
        self.filter_directory = filter_directory or os.path.join(os.environ.get("REFERENCE_ARCHIVE_DIR", ".reference-archive"), "filters")
        self.blocked = set()
        self.validator = validator
        self.cache = {}
        self.raw_documents = {}
        self.info = {}
        self.total = 0
        self.driver = None
        self.browser = None
        self.context = None

    def __enter__(self):
        from archive_adblock import load_engine
        if self.adblock is None:
            self.adblock = load_engine(self.filter_directory)
        from playwright.sync_api import sync_playwright
        self.driver = sync_playwright().start()
        executable = os.environ.get('ARCHIVE_CHROMIUM_PATH') or shutil.which('chromium') or shutil.which('google-chrome')
        try:
            self.browser = self.driver.chromium.launch(
                headless=True, executable_path=executable,
                chromium_sandbox=True, args=['--disable-extensions'],
            )
            self.context = self.browser.new_context(
                viewport={'width': 1280, 'height': 900}, locale='en-GB',
                reduced_motion='reduce', service_workers='block', accept_downloads=False,
            )
            self.context.route('**/*', self._route)
            self.context.on('requestfinished', self._remember)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.browser:
            self.browser.close()
        if self.driver:
            self.driver.stop()

    def _route(self, route):
        try:
            self.validator(route.request.url)
            # Each redirect is routed too. Never give page scripts local network access.
            if route.request.method not in ('GET', 'HEAD', 'POST', 'OPTIONS'):
                route.abort()
            elif route.request.is_navigation_request() and route.request.frame != self.page.main_frame:
                route.abort()
            elif not route.request.is_navigation_request() and self.adblock.check_network_urls(
                route.request.url, route.request.frame.url,
                {"fetch": "xmlhttprequest", "xhr": "xmlhttprequest"}.get(route.request.resource_type, route.request.resource_type),
            ).matched:
                self.blocked.add(urldefrag(route.request.url)[0])
                route.abort()
            else:
                route.continue_()
        except Exception:
            route.abort()

    def _remember(self, request):
        if len(self.cache) >= MAX_ASSETS or self.total >= MAX_TOTAL:
            return
        try:
            response = request.response()
            if not response or not response.ok:
                return
            length = int(response.headers.get('content-length', '0'))
            if length > MAX_FILE:
                return
            data = response.body()
            if len(data) > MAX_FILE or self.total + len(data) > MAX_TOTAL:
                return
            content_type = response.headers.get('content-type', 'application/octet-stream')
            mime = content_type.split(';')[0].strip().lower()
            match = re.search(r'charset=["\']?([^\s;"\']+)', content_type, re.I)
            charset = match[1] if match else 'utf-8'
            url = urldefrag(response.url)[0]
            if url not in self.cache:
                self.total += len(data)
                self.cache[url] = data, mime, charset, response.url
        except Exception:
            # Failed assets remain eligible for the existing fetch/retry path.
            pass

    def __call__(self, url):
        """Prefer bytes fetched by this browser, including session-dependent assets."""
        if urldefrag(url)[0] in self.blocked:
            raise ValueError("Intentionally blocked advertising/tracking resource")
        return self.cache.get(urldefrag(url)[0]) or fetch(url)

    def document(self, url):
        from playwright.sync_api import TimeoutError as BrowserTimeout
        self.validator(url)
        raw = None
        try:
            raw = self(url)
            if raw[1] not in ('text/html', 'application/xhtml+xml'):
                self.info[url] = {'capture_method': 'http'}
                return raw
        except Exception:
            # Chromium can render some sources that reject simple HTTP clients.
            pass
        self.page = self.context.new_page()
        self.page.on('dialog', lambda dialog: dialog.dismiss())
        try:
            response = self.page.goto(url, wait_until='domcontentloaded', timeout=30000)
            if response is None or not response.ok:
                raise ValueError('Browser navigation failed: ' + str(response.status if response else 'no response'))
            self.validator(self.page.url)
            try:
                self.page.wait_for_load_state('load', timeout=10000)
            except BrowserTimeout:
                pass
            from archive_adblock import clean_page
            removed = clean_page(self.page, self.adblock)
            # Bounded scrolling triggers lazy images and article sections.
            for step in range(8):
                self.page.evaluate('step => window.scrollTo(0, step * innerHeight)', step)
                self.page.wait_for_timeout(200)
            self.page.evaluate('window.scrollTo(0, 0)')
            started = time.monotonic()
            previous = None
            stable_since = started
            settled = False
            while time.monotonic() - started < 15:
                text = self.page.evaluate('document.body?.innerText || ""')
                if text != previous:
                    previous = text
                    stable_since = time.monotonic()
                if time.monotonic() - started >= 5 and time.monotonic() - stable_since >= 2:
                    settled = True
                    break
                self.page.wait_for_timeout(250)
            removed += clean_page(self.page, self.adblock)
            # Remove blocked resource elements so the asset collector cannot retry them.
            self.page.evaluate('''blocked => {
                for (const el of document.querySelectorAll("[src], link[href]")) {
                    if (blocked.includes((el.src || el.href || "").split("#")[0])) el.remove();
                }
            }''', sorted(self.blocked))
            # Preserve CSS generated through CSSOM, canvas images, and open shadow DOM.
            rendered = self.page.evaluate('''() => {
                const clone = document.documentElement.cloneNode(true);
                function materialise(source, target) {
                    const originals = Array.from(source.childNodes);
                    const copies = Array.from(target.childNodes);
                    originals.forEach((node, i) => {
                        const copy = copies[i];
                        if (!copy || node.nodeType !== Node.ELEMENT_NODE) return;
                        if (node.tagName === 'STYLE') {
                            try { copy.textContent = Array.from(node.sheet.cssRules, r => r.cssText).join('\\n'); } catch {}
                        }
                        if (node.tagName === 'CANVAS') {
                            try {
                                const image = document.createElement('img');
                                image.src = node.toDataURL(); image.width = node.width; image.height = node.height;
                                copy.replaceWith(image); return;
                            } catch {}
                        }
                        materialise(node, copy);
                        if (node.shadowRoot) {
                            const shadow = document.createDocumentFragment();
                            for (const child of node.shadowRoot.childNodes) shadow.append(child.cloneNode(true));
                            materialise(node.shadowRoot, shadow);
                            for (const slot of shadow.querySelectorAll('slot')) {
                                const original = node.shadowRoot.querySelector(`slot[name="${slot.name}"]`) || node.shadowRoot.querySelector('slot:not([name])');
                                const assigned = original?.assignedNodes({flatten: true}) || [];
                                if (assigned.length) slot.replaceWith(...assigned.map(n => n.cloneNode(true)));
                            }
                            copy.replaceChildren(shadow);
                        }
                    });
                }
                materialise(document.documentElement, clone);
                return '<!doctype html>\\n' + clone.outerHTML;
            }''')
            data = rendered.encode('utf-8')
            if len(data) > MAX_FILE:
                raise ValueError('Rendered document exceeds 16 MiB')
            final = self.page.url
            original = self.cache.get(urldefrag(response.url)[0]) or raw
            if original:
                self.raw_documents[url] = original[0]
            self.info[url] = {'capture_method': 'chromium', 'render_settled': settled, 'rendered_url': final, 'adblock_version': 1, 'adblock_removed': removed, 'adblock_requests': len(self.blocked)}
            return data, 'text/html', 'utf-8', final
        finally:
            self.page.close()
