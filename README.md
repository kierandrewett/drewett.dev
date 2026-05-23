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
docker run --rm -p 4173:4173 kierand-dev
```
