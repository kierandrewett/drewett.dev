//! Persistent reference captures. Only URLs in note reference lists enter the worker.
use crate::{get_notes, AppState};
use axum::{
    extract::{Path, State},
    http::{header, HeaderValue, StatusCode},
    response::{Html, IntoResponse, Response},
};
use regex::Regex;
use serde::Deserialize;
use std::{
    collections::HashMap,
    path::PathBuf,
    process::Stdio,
    sync::{Arc, LazyLock},
    time::Duration,
};
use tokio::io::AsyncWriteExt;

static REFERENCE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?s)<li>\s*(?:<p>)?\s*<a id="ref-[^"]+"[^>]*></a>.*?</li>"#).unwrap()
});
static LINK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"<a\b[^>]*href="(https?://[^"]+)"[^>]*>.*?</a>"#).unwrap());

#[derive(Default, Deserialize)]
struct Record {
    id: String,
    url: String,
    accessed_at: Option<String>,
    #[serde(default)]
    use_archive: bool,
    #[serde(default)]
    entry: String,
    #[serde(default)]
    asset_errors: Vec<serde_json::Value>,
}

pub fn directory() -> PathBuf {
    std::env::var_os("REFERENCE_ARCHIVE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(".reference-archive"))
}

async fn records() -> HashMap<String, Record> {
    match tokio::fs::read(directory().join("manifest.json")).await {
        Ok(data) => serde_json::from_slice(&data).unwrap_or_default(),
        Err(_) => HashMap::new(),
    }
}

fn decode(url: &str) -> String {
    html_escape::decode_html_entities(url).into_owned()
}

fn escape(value: &str) -> String {
    html_escape::encode_double_quoted_attribute(value).into_owned()
}

pub fn urls(html: &str) -> Vec<String> {
    REFERENCE
        .find_iter(html)
        .flat_map(|reference| {
            LINK.captures_iter(reference.as_str())
                .map(|link| decode(&link[1]))
                .collect::<Vec<_>>()
        })
        .collect()
}

fn annotate(html: &str, records: &HashMap<String, Record>) -> String {
    REFERENCE.replace_all(html, |reference: &regex::Captures<'_>| {
        LINK.replace_all(&reference[0], |link: &regex::Captures<'_>| {
            let url = decode(&link[1]);
            let Some(record) = records.get(&url).filter(|r| r.accessed_at.is_some()) else {
                return format!("{} <span class=\"reference-meta\">(Access date unavailable; archive pending.)</span>", &link[0]);
            };
            let archive = format!("/archive/{}", record.id);
            let date = record.accessed_at.as_deref().unwrap_or_default();
            let original = if record.use_archive {
                link[0].replacen(&format!("href=\"{}\"", &link[1]), &format!("href=\"{}\"", archive), 1)
            } else { link[0].to_string() };
            let label = if record.use_archive { "Archived source in use" } else if record.asset_errors.is_empty() { "Archived copy" } else { "Archived copy (some assets unavailable)" };
            format!("{} <span class=\"reference-meta\">(Accessed at <time datetime=\"{}\">{}</time>. <a href=\"{}\">{}</a>.)</span>", original, escape(date), escape(&date.replace('T', " ").replace("+00:00", " UTC")), archive, label)
        }).into_owned()
    }).into_owned()
}

pub async fn render(html: &str) -> String {
    annotate(html, &records().await)
}

pub async fn worker(state: Arc<AppState>) {
    loop {
        let notes = get_notes(&state).await;
        let mut references: Vec<String> = notes.notes.iter().flat_map(|n| urls(&n.html)).collect();
        references.sort();
        references.dedup();
        if !references.is_empty() {
            if let Err(error) = scan(&references).await {
                eprintln!("[archive] worker failed: {error}");
            }
        }
        // Discover new notes hourly. The durable manifest makes checks daily,
        // including across process restarts. No requests wait for capture work.
        tokio::time::sleep(Duration::from_secs(3600)).await;
    }
}

async fn scan(urls: &[String]) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let mut child = tokio::process::Command::new("python3")
        .arg("scripts/archive_references.py")
        .arg(directory())
        .stdin(Stdio::piped())
        .kill_on_drop(true)
        .spawn()?;
    let mut stdin = child.stdin.take().ok_or("archive worker stdin missing")?;
    stdin.write_all(&serde_json::to_vec(urls)?).await?;
    drop(stdin);
    let status = child.wait().await?;
    if !status.success() {
        return Err(format!("archive worker exited with {status}").into());
    }
    Ok(())
}

pub async fn page(State(_state): State<Arc<AppState>>, Path(id): Path<String>) -> Response {
    let records = records().await;
    let Some(record) = records
        .values()
        .find(|r| r.id == id && r.accessed_at.is_some())
    else {
        return (StatusCode::NOT_FOUND, "Archived reference not found").into_response();
    };
    let date = escape(record.accessed_at.as_deref().unwrap_or_default());
    let url = escape(&record.url);
    let entry = escape(&record.entry);
    let asset_note = if record.asset_errors.is_empty() {
        ""
    } else {
        " Some assets could not be captured."
    };
    let saved_document = if record.entry == "document.pdf" {
        format!("<p><a href=\"/archive/{id}/files/document.pdf\">Download archived PDF</a></p>")
    } else {
        format!("<iframe title=\"Saved reference page\" sandbox src=\"/archive/{id}/files/{entry}\"></iframe>")
    };
    let html = format!(
        r#"<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>Archived reference</title><style>body{{margin:0;font:16px system-ui;background:#faf8f2;color:#222}}header{{padding:1rem;overflow-wrap:anywhere}}p{{margin:.4rem 0}}iframe{{display:block;width:100%;height:85vh;border:0;background:white}}a{{color:#174e8c}}</style><header><strong>Archived reference</strong><p>Captured at <time>{date}</time>. This is a frozen copy.{asset_note}</p><p><a href="{url}" rel="noreferrer">Original source</a> · <a href="/archive/{id}/files/{entry}">Open saved document</a></p></header>{saved_document}</html>"#
    );
    let mut response = Html(html).into_response();
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    response
}

pub async fn asset(Path((id, file)): Path<(String, String)>) -> Response {
    if id.len() != 64
        || !id.bytes().all(|b| b.is_ascii_hexdigit())
        || file.is_empty()
        || !file
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
        || file.contains("..")
        || matches!(file.as_str(), "original.bin" | "capture.json")
    {
        return StatusCode::NOT_FOUND.into_response();
    }
    let bytes = match tokio::fs::read(directory().join("pages").join(id).join(&file)).await {
        Ok(bytes) => bytes,
        Err(_) => return StatusCode::NOT_FOUND.into_response(),
    };
    let content_type = match file.rsplit('.').next().unwrap_or("") {
        "html" => "text/html; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "js" => "text/javascript",
        "pdf" => "application/pdf",
        "png" => "image/png",
        "jpg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "svg" => "image/svg+xml",
        "woff2" => "font/woff2",
        "woff" => "font/woff",
        _ => "application/octet-stream",
    };
    let mut response = ([(header::CONTENT_TYPE, content_type)], bytes).into_response();
    let headers = response.headers_mut();
    headers.insert(header::CONTENT_SECURITY_POLICY, HeaderValue::from_static("sandbox; default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; media-src 'self' data:; form-action 'none'; base-uri 'none'; frame-ancestors 'self'"));
    // Sandboxed documents have an opaque origin; saved fonts need CORS.
    headers.insert(
        header::ACCESS_CONTROL_ALLOW_ORIGIN,
        HeaderValue::from_static("*"),
    );
    if file.ends_with(".pdf") {
        headers.insert(
            header::CONTENT_DISPOSITION,
            HeaderValue::from_static("attachment; filename=archived-reference.pdf"),
        );
    }
    headers.insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    headers.insert(
        header::REFERRER_POLICY,
        HeaderValue::from_static("no-referrer"),
    );
    headers.insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("public, max-age=31536000, immutable"),
    );
    headers.insert(
        header::HeaderName::from_static("x-robots-tag"),
        HeaderValue::from_static("noindex, nofollow"),
    );
    response
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn references_only_preserve_citations_and_escape_urls() {
        let source = r##"<p><a href="https://uncited.example">not cited</a><sup><a href="#ref-1">[1]</a></sup></p><ol><li><a id="ref-1"></a>Author. <a target="_blank" href="https://example.org/?a=1&amp;b=2">Title</a></li></ol>"##;
        let url = "https://example.org/?a=1&b=2";
        assert_eq!(urls(source), vec![url]);
        let mut records = HashMap::new();
        records.insert(
            url.into(),
            Record {
                id: "abc".into(),
                accessed_at: Some("2026-09-06T12:00:00+00:00".into()),
                use_archive: true,
                ..Default::default()
            },
        );
        let result = annotate(source, &records);
        assert!(result.contains(r##"href="#ref-1">[1]</a>"##));
        assert!(result.contains(r#"href="/archive/abc">Title</a>"#));
        assert!(result.contains("Accessed at"));
        assert!(result.contains("https://uncited.example"));
        records.get_mut(url).unwrap().use_archive = false;
        assert!(annotate(source, &records)
            .contains(r#"href="https://example.org/?a=1&amp;b=2">Title</a>"#));
        assert!(annotate(source, &HashMap::new()).contains("Access date unavailable"));
    }
}
