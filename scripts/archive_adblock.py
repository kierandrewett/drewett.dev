"""Network and cosmetic filtering for frozen references (Brave's adblock engine)."""
from pathlib import Path
import threading
import time

from archive_references import DAY, fetch

LISTS = {
    'easylist': 'https://easylist.to/easylist/easylist.txt',
    'easyprivacy': 'https://easylist.to/easylist/easyprivacy.txt',
    'annoyances': 'https://easylist.to/easylist/fanboy-annoyance.txt',
}
_lock = threading.Lock()
_cached = {}


def load_engine(directory):
    import adblock
    directory = Path(directory)
    # Concurrent captures share downloads, but each owns its native engine.
    with _lock:
        directory.mkdir(parents=True, exist_ok=True)
        lists = []
        for name, url in LISTS.items():
            path = directory / (name + '.txt')
            if not path.exists() or time.time() - path.stat().st_mtime > DAY:
                try:
                    data = fetch(url)[0]
                    if not data.startswith(b'[Adblock') or len(data) < 1000:
                        raise ValueError('Invalid adblock filter list: ' + name)
                    temporary = path.with_suffix('.tmp')
                    temporary.write_bytes(data)
                    temporary.replace(path)
                except Exception:
                    if not path.exists():
                        raise  # Do not silently save an unfiltered first capture.
            stamp = (str(path), path.stat().st_mtime_ns)
            if stamp not in _cached:
                _cached[stamp] = path.read_text()
            lists.append(_cached[stamp])
    filters = adblock.FilterSet()
    for rules in lists:
        filters.add_filter_list(rules)
    return adblock.Engine(filters)


def clean_page(page, engine):
    """Remove matched overlays, and release their scroll locks without clicking consent."""
    resources = engine.url_cosmetic_resources(page.url)
    tokens = page.evaluate('''() => {
        const classes = new Set(), ids = new Set();
        function scan(root) {
            for (const el of root.querySelectorAll('*')) {
                for (const name of el.classList) classes.add(name);
                if (el.id) ids.add(el.id);
                if (el.shadowRoot) scan(el.shadowRoot);
            }
        }
        scan(document); return {classes: [...classes], ids: [...ids]};
    }''')
    selectors = set(resources.hide_selectors)
    if not resources.generichide:
        selectors.update(engine.hidden_class_id_selectors(tokens['classes'], tokens['ids'], resources.exceptions))
    return page.evaluate('''selectors => {
        let removed = 0, overlay = false;
        function clean(root) {
            for (const selector of selectors) {
                try {
                    for (const el of root.querySelectorAll(selector)) {
                        if (['HTML', 'BODY'].includes(el.tagName)) continue;
                        const position = getComputedStyle(el).position;
                        overlay ||= position === 'fixed' || position === 'sticky';
                        el.remove(); removed++;
                    }
                } catch {} // Unsupported procedural filters are not CSS selectors.
            }
            for (const el of root.querySelectorAll('*')) if (el.shadowRoot) clean(el.shadowRoot);
        }
        clean(document);
        if (overlay) for (const el of [document.documentElement, document.body]) {
            if (!el) continue;
            el.style.setProperty('overflow', 'auto', 'important');
            el.style.setProperty('height', 'auto', 'important');
            el.style.setProperty('max-height', 'none', 'important');
            if (getComputedStyle(el).position === 'fixed') el.style.setProperty('position', 'static', 'important');
        }
        return removed;
    }''', sorted(selectors))
