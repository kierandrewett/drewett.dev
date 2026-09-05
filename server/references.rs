//! Persistent reference captures. Only URLs in note reference lists enter the worker.
use crate::{get_notes, AppState};
use axum::{
    extract::{Path, State},
    http::{header, HeaderValue, StatusCode},
    response::{Html, IntoResponse, Redirect, Response},
};
use lol_html::{element, html_content::ContentType, rewrite_str, RewriteStrSettings};
use regex::Regex;
use serde::{Deserialize, Serialize};
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

#[derive(Default, Deserialize, Serialize)]
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
    #[serde(default)]
    documents: Vec<Document>,
    #[serde(default)]
    document_errors: Vec<serde_json::Value>,
}

#[derive(Deserialize, Serialize)]
struct Document {
    title: String,
    file: String,
    captured_at: String,
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
    if record.entry == "index.html" {
        return Redirect::temporary(&format!("/archive/{id}/files/index.html")).into_response();
    }
    let source = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Archived reference</title></head><body></body></html>";
    match native_page(source, record, &id) {
        Ok(html) => protect(Html(html).into_response(), "index.html"),
        Err(_) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            "Archive rendering failed",
        )
            .into_response(),
    }
}

fn native_page(
    source: &str,
    record: &Record,
    id: &str,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let mut context = tera::Context::new();
    context.insert("record", record);
    context.insert("archive_root", &format!("/archive/{id}/files"));
    let banner = tera::Tera::one_off(
        include_str!("../templates/archive-banner.html"),
        &context,
        true,
    )?;
    // Parse and strip active markup on every replay, including older captures.
    // The saved source bytes and assets are never changed by presentation rules.
    let mut has_body = false;
    let output = rewrite_str(
        source,
        RewriteStrSettings {
            element_content_handlers: vec![
                element!(
                    "script, iframe, frame, frameset, object, embed, applet, base, template",
                    |el| {
                        el.remove();
                        Ok(())
                    }
                ),
                element!("form", |el| {
                    el.remove_and_keep_content();
                    Ok(())
                }),
                element!("input, button, select, textarea", |el| {
                    el.set_attribute("disabled", "")?;
                    Ok(())
                }),
                element!("meta[http-equiv]", |el| {
                    el.remove();
                    Ok(())
                }),
                element!("*", |el| {
                    let attributes: Vec<String> = el
                        .attributes()
                        .iter()
                        .map(|a| a.name())
                        .filter(|name| {
                            name.starts_with("on")
                                || matches!(
                                    name.as_str(),
                                    "srcdoc" | "autofocus" | "formaction" | "action" | "ping"
                                )
                        })
                        .collect();
                    for name in attributes {
                        el.remove_attribute(&name);
                    }
                    for name in ["href", "xlink:href"] {
                        if let Some(value) = el.get_attribute(name) {
                            let normalised: String = html_escape::decode_html_entities(&value)
                                .chars()
                                .filter(|c| !c.is_ascii_whitespace() && !c.is_control())
                                .collect();
                            if normalised.to_ascii_lowercase().starts_with("javascript:")
                                || normalised.to_ascii_lowercase().starts_with("vbscript:")
                            {
                                el.remove_attribute(name);
                            }
                        }
                    }
                    Ok(())
                }),
                element!("body", |el| {
                    has_body = true;
                    el.prepend(&banner, ContentType::Html);
                    Ok(())
                }),
            ],
            ..RewriteStrSettings::default()
        },
    )?;
    if has_body {
        Ok(output)
    } else {
        Ok(format!("{banner}{output}"))
    }
}

pub async fn asset(Path((id, file)): Path<(String, String)>) -> Response {
    if id.len() != 64
        || !id.bytes().all(|b| b.is_ascii_hexdigit())
        || file.is_empty()
        || !file
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
        || file.contains("..")
        || matches!(
            file.as_str(),
            "original.bin" | "response.bin" | "capture.json" | "documents.json" | "documents.tmp"
        )
    {
        return StatusCode::NOT_FOUND.into_response();
    }
    let mut bytes = match tokio::fs::read(directory().join("pages").join(&id).join(&file)).await {
        Ok(bytes) => bytes,
        Err(_) => return StatusCode::NOT_FOUND.into_response(),
    };
    if file.ends_with(".html") {
        let records = records().await;
        let Some(record) = records
            .values()
            .find(|r| r.id == id && r.accessed_at.is_some())
        else {
            return StatusCode::NOT_FOUND.into_response();
        };
        bytes = match native_page(&String::from_utf8_lossy(&bytes), record, &id) {
            Ok(html) => html.into_bytes(),
            Err(_) => {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Archive rendering failed",
                )
                    .into_response()
            }
        };
    }
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
    protect(
        ([(header::CONTENT_TYPE, content_type)], bytes).into_response(),
        &file,
    )
}

fn protect(mut response: Response, file: &str) -> Response {
    let headers = response.headers_mut();
    headers.insert(header::CONTENT_SECURITY_POLICY, HeaderValue::from_static("sandbox allow-downloads; default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; media-src 'self' data:; form-action 'none'; base-uri 'none'; frame-ancestors 'none'"));
    // Sandboxed documents have an opaque origin; saved fonts need CORS.
    headers.insert(
        header::ACCESS_CONTROL_ALLOW_ORIGIN,
        HeaderValue::from_static("*"),
    );
    if matches!(
        file.rsplit('.').next(),
        Some("pdf" | "doc" | "docx" | "epub")
    ) {
        headers.insert(
            header::CONTENT_DISPOSITION,
            HeaderValue::from_str(&format!("attachment; filename=\"{file}\"")).unwrap(),
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
        HeaderValue::from_static(if file.ends_with(".html") {
            "no-cache"
        } else {
            "public, max-age=31536000, immutable"
        }),
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
    fn replay_is_native_and_strips_active_content() {
        let record = Record {
            id: "a".repeat(64),
            url: "https://example.org/paper".into(),
            accessed_at: Some("2026-09-06T12:00:00Z".into()),
            entry: "index.html".into(),
            ..Default::default()
        };
        let source = r#"<!doctype html><html><head><style>p { color: red; }</style><script src="evil.js">alert(1)</script><meta http-equiv="refresh" content="0;url=https://bad.example"></head><body onload="bad()"><h1>Saved paper</h1><iframe src="evil.html"></iframe><form action="https://bad.example"><input autofocus><p>Evidence survives.</p></form><a href="java&#x73;cript:alert(1)" onclick="bad()">Link</a></body></html>"#;
        let result = native_page(source, &record, &record.id).unwrap();
        assert!(result.contains("Saved paper"));
        assert!(result.contains("Evidence survives."));
        assert!(result.contains("<style>p { color: red; }</style>"));
        assert!(result.contains("shadowrootmode=\"closed\""));
        for forbidden in [
            "<iframe",
            "<script",
            "<form",
            "onclick",
            "onload",
            "autofocus",
            "http-equiv",
            "alert(1)",
        ] {
            assert!(!result.contains(forbidden), "Replay retained {forbidden}");
        }
        assert!(result.contains("disabled"));
        let response = protect(Html(result).into_response(), "index.html");
        assert!(response.headers()[header::CONTENT_SECURITY_POLICY]
            .to_str()
            .unwrap()
            .contains("script-src 'none'"));
        assert_eq!(response.headers()[header::CACHE_CONTROL], "no-cache");
    }

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
