# kierand.dev

Personal website powered by Rust, Axum, and Tera.

## Stack

- Rust + Axum HTTP server
- Tera templates for SSR
- Static assets from `static/`
- Projects rendered from markdown in `content/projects.md` on every request

## Development

Run locally:

```bash
cargo run
```

Default address is `http://127.0.0.1:4173`.

Set a custom port:

```bash
PORT=4181 cargo run
```

### Note pageviews

`/notes/:slug` can show a pageview count from the separate `pageviews` service.
The count is hidden when the service is not configured or does not respond quickly.

```bash
PAGEVIEWS_BASE_URL=http://localhost:3000 cargo run
```

Optional settings:

- `PAGEVIEWS_SITE_ID`, default `drewett.dev`
- `PAGEVIEWS_PERIOD`, default `all`
- `PAGEVIEWS_TIMEOUT_MS`, default `1200`

## Build

```bash
cargo build --release
```

## Docker

Build and run:

```bash
docker build -t kierand-dev .
docker run --rm --init --shm-size=1g \
  --security-opt seccomp=docker/chromium-seccomp.json \
  -p 4173:4173 kierand-dev
```

### Reference archives

Python 3.10 or newer, Playwright and Chromium are required alongside the Rust
server. Install them locally with:

```bash
python3 -m pip install -r scripts/requirements.txt
python3 -m playwright install --with-deps --only-shell chromium
```

The worker also supports a system Chromium or Google Chrome installation. Set
`ARCHIVE_CHROMIUM_PATH` to use a specific executable. Chromium runs headless with
its sandbox enabled, without extensions or a persistent browser profile.
The container runs as UID 10001; host archive mounts must be writable by that user.
Use the included [Playwright seccomp profile](docker/README.md) to permit the
namespace operations needed by Chromium's sandbox. Browser
startup failures are recorded as capture errors, not as dead references.

 On startup and hourly, the server discovers external links inside
note reference entries (`<a id="ref-N"></a>`). Keep the existing inline
`<sup><a href="#ref-N">[N]</a></sup>` citations and the author, title, publisher
and publication date in each entry. The server adds the actual first successful
capture time as **Accessed at**, plus an **Archived copy** link. It does not invent
historical access dates. References without a successful capture show that the
access date is unavailable.

Captures live in `REFERENCE_ARCHIVE_DIR` (default `.reference-archive`). This
folder contains a durable `manifest.json` and immutable `pages/<sha256>/`
directories. Back up the whole folder. The worker uses a file lock and atomic
writes, and recovers completed captures if a process stops before updating the
manifest. Capture work runs outside request handling, with three workers.

HTML is loaded in a clean browser context before capture. The worker waits for
page load, scrolls up to eight screens to trigger lazy content, then waits at
least five seconds for text to settle (up to 15 seconds). This is a bounded
settling rule, not a guarantee that every site has finished all future work.
The final DOM includes JavaScript-generated text and links, CSSOM styles, readable
canvas images and open shadow content. Assets already fetched by the browser are
reused. PDFs continue to use direct downloads. Replay still removes scripts and
active markup; JavaScript runs only during capture.

The first successful capture keeps the rendered DOM and HTTP response separately,
plus rewritten HTML, styles,
images, fonts, scripts, CSS imports and CSS URL dependencies. PDF references
are also supported. The capture follows important document links: PDF downloads,
full-text pages, paper/article downloads, EPUB/Word documents and publication
metadata such as `citation_pdf_url`. These documents are fetched before decorative
assets. Up to 12 documents and two link levels are followed; navigation, contact
pages and unrelated links are not crawled. Linked HTML pages also save their assets
and PDFs. `/archive/<sha256>` shows the capture time, original URL and direct links
to saved papers. PDF links download the actual saved file. Failed paper downloads
are listed explicitly and retried during daily checks. Supplemental captures keep
their own timestamps and never replace the original page or saved documents.
The replay uses only saved assets. Scripts, forms and external network requests
are disabled by its Content Security Policy. Saved pages render directly, without
iframes. The server strips scripts, event handlers, forms, frames and active embeds
on every replay, including older captures. The archive header uses a closed shadow
root to separate its markup and styles from the captured page. Captures come from
an isolated server browser, so a visitor's extensions cannot contaminate the stored copy.
An extension with permission to modify a visitor's browser can still change that
visitor's display; a website cannot prevent this reliably. See the
[MDN sandbox documentation](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/sandbox).

Each source is checked at most once every 24 hours, including across restarts.
Two consecutive failed checks automatically switch its reference link to the
archive. Failures include HTTP/network errors, access challenges and substantial
text changes. The HTML comparison ignores navigation, headers, footers, scripts
and styles, and compares sets of five-word sequences with the original capture.
Similarity below 45% counts as a substantial change. PDFs use exact byte changes.
This is a heuristic: a major legitimate rewrite can also trigger fallback.
A later check that matches the baseline restores the live link. Rendered captures
are compared with newly rendered pages. Existing HTTP-only captures get a new
rendered version on their next scheduled check. The previous capture, timestamp
and URL remain available. A failed browser upgrade keeps the existing capture.
The original capture and its access date are never replaced.

Capture limits per page: 16 MiB per file, 64 MiB total assets, 256 assets and a
180-second asset budget. Individual reads time out after 15 seconds. Missing or
blocked assets are recorded in `capture.json` and shown as an incomplete capture.
Sites behind logins, paywalls or anti-bot checks, closed shadow roots, and content
requiring user interaction cannot be guaranteed a complete capture. A blocked initial capture
never becomes a fallback. There is no public endpoint that fetches arbitrary URLs.

Persist the archive across container replacements:

```bash
docker run --rm --init --shm-size=1g \
  --security-opt seccomp=docker/chromium-seccomp.json \
  -p 4173:4173 \
  -v reference-archive:/data/reference-archive kierand-dev
```

For Docker Compose, mount a named volume or a host directory at
`/data/reference-archive`. The background worker starts with the web server;
configure `init: true`, `shm_size: 1gb` and
`security_opt: ["seccomp=./docker/chromium-seccomp.json"]` alongside the volume.
There is no separate cron job. The container must remain running for checks.
To run a manual scan, pass a JSON list of reference URLs on standard input:

```bash
printf '["https://sre.google/sre-book/eliminating-toil/"]' | \
  python3 scripts/archive_references.py .reference-archive
```

Add `--upgrade-rendering` to the manual scan command to create rendered versions
immediately while retaining existing snapshots and URLs.

Run archive tests with `python3 -m unittest discover -s scripts -v` and `cargo test`.
