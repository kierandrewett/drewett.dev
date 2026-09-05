FROM rust:1.88-alpine AS build

WORKDIR /app

RUN apk add --no-cache musl-dev gcc

COPY . .

RUN cargo build --release

FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
COPY scripts/requirements.txt ./scripts/requirements.txt
RUN pip install --no-cache-dir -r scripts/requirements.txt \
    && python3 -m playwright install --with-deps --only-shell chromium \
    && useradd --create-home --uid 10001 archive \
    && mkdir -p /data/reference-archive \
    && chown archive:archive /data/reference-archive

USER archive

COPY --from=build /app/target/release/kierand-dev-rust /usr/local/bin/kierand-dev-rust
COPY static ./static
COPY templates ./templates
COPY content ./content
COPY scripts ./scripts

ENV REFERENCE_ARCHIVE_DIR=/data/reference-archive
VOLUME ["/data/reference-archive"]

EXPOSE 4173

CMD ["kierand-dev-rust"]
