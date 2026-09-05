FROM rust:1.85-alpine AS build

WORKDIR /app

RUN apk add --no-cache musl-dev gcc

COPY . .

RUN cargo build --release

FROM alpine:3.21

WORKDIR /app

RUN apk add --no-cache python3 ca-certificates

COPY --from=build /app/target/release/kierand-dev-rust /usr/local/bin/kierand-dev-rust
COPY static ./static
COPY templates ./templates
COPY content ./content
COPY scripts ./scripts

ENV REFERENCE_ARCHIVE_DIR=/data/reference-archive
VOLUME ["/data/reference-archive"]

EXPOSE 4173

CMD ["kierand-dev-rust"]
