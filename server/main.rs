use axum::{
    extract::State,
    http::{header, HeaderValue, StatusCode},
    response::{Html, IntoResponse, Redirect, Response},
    routing::get,
    Json, Router,
};
use chrono::Datelike;
use pulldown_cmark::{html::push_html, Options, Parser};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{env, net::SocketAddr, sync::Arc, time::{Duration, Instant, UNIX_EPOCH}};
use tera::{Context, Tera};
use tokio::net::TcpListener;
use tokio::sync::RwLock;
use tower_http::services::{ServeDir, ServeFile};

#[derive(Clone)]
struct CachedContent {
    projects: String,
    profiles: String,
    contact: String,
    tagline: String,
    footer: String,
    fetched_at: Instant,
}

#[derive(Clone)]
struct AppState {
    client: Client,
    content_base_url: String,
    content_cache: Arc<RwLock<Option<CachedContent>>>,
    content_cache_ttl: Duration,
    templates: Arc<Tera>,
}

#[derive(Serialize)]
struct LastfmResponse {
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    track_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    artist_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    album_art: Option<String>,
    now_playing: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Serialize)]
struct TagsResponse {
    ok: bool,
    tags: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Serialize)]
struct ActiveResponse {
    ok: bool,
    active: bool,
    status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    since: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Deserialize)]
struct ActiveUpstreamResponse {
    status: String,
    since: Option<String>,
}

#[derive(Serialize)]
struct TemplateTag {
    label: String,
    url: String,
}

#[derive(Serialize)]
struct InitialLastfm {
    ok: bool,
    track_name: String,
    artist_name: String,
    album_art: String,
}

impl InitialLastfm {
    fn connecting() -> Self {
        Self {
            ok: false,
            track_name: String::new(),
            artist_name: String::new(),
            album_art: String::new(),
        }
    }
}

enum LastfmFetchError {
    MissingKey(String),
    Upstream(String),
}

#[tokio::main]
async fn main() {
    let client = Client::builder()
        .user_agent("kierand.dev-rust-server")
        .build()
        .expect("failed to build reqwest client");

    let templates = Tera::new("templates/**/*").expect("failed to initialize tera templates");

    let content_base_url = env::var("CONTENT_BASE_URL").unwrap_or_else(|_| {
        "https://raw.githubusercontent.com/kierandrewett/kierand.dev/main/content".to_string()
    });

    let content_cache_secs: u64 = env::var("CONTENT_CACHE_SECS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(300);

    let state = Arc::new(AppState {
        client,
        content_base_url,
        content_cache: Arc::new(RwLock::new(None)),
        content_cache_ttl: Duration::from_secs(content_cache_secs),
        templates: Arc::new(templates),
    });

    let app = Router::new()
        .route("/", get(home))
        .route("/cv", get(cv_redirect))
        .route("/cv.pdf", get(cv_pdf))
        .route("/favicon.ico", get(favicon))
        .route("/favicon.png", get(favicon))
        .route("/api/active", get(api_active))
        .route("/api/lastfm", get(api_lastfm))
        .route("/api/lastfm/tags", get(api_lastfm_tags))
        .route_service("/app.css", ServeFile::new("static/app.css"))
        .route_service("/app.js", ServeFile::new("static/app.js"))
        .route_service("/robots.txt", ServeFile::new("static/robots.txt"))
        .nest_service("/fonts", ServeDir::new("static/fonts"))
        .nest_service("/images", ServeDir::new("static/images"))
        .nest_service("/.well-known", ServeDir::new("static/.well-known"))
        .fallback(get(catchall_redirect))
        .with_state(state);

    let port = env::var("PORT")
        .ok()
        .and_then(|value| value.parse::<u16>().ok())
        .unwrap_or(4173);
    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    let listener = TcpListener::bind(addr)
        .await
        .expect("failed to bind TCP listener");

    println!("Rust server listening on http://{}", addr);

    axum::serve(listener, app)
        .await
        .expect("server exited unexpectedly");
}

async fn fetch_content(client: &Client, base_url: &str, file: &str) -> String {
    let url = format!("{}/{}", base_url, file);
    let remote = async {
        let response = client.get(&url).send().await.map_err(|e| e.to_string())?;
        if !response.status().is_success() {
            return Err(format!("HTTP {} for {}", response.status(), url));
        }
        response.text().await.map_err(|e| e.to_string())
    };

    match remote.await {
        Ok(content) => content,
        Err(_) => {
            let local_path = format!("content/{}", file);
            tokio::fs::read_to_string(&local_path)
                .await
                .unwrap_or_default()
        }
    }
}

async fn fetch_all_content(state: &AppState) -> CachedContent {
    let base = &state.content_base_url;
    let client = &state.client;

    let (projects, profiles, contact, tagline, footer) = tokio::join!(
        fetch_content(client, base, "projects.md"),
        fetch_content(client, base, "profiles.md"),
        fetch_content(client, base, "contact.md"),
        fetch_content(client, base, "tagline.md"),
        fetch_content(client, base, "footer.md"),
    );

    CachedContent {
        projects,
        profiles,
        contact,
        tagline,
        footer,
        fetched_at: Instant::now(),
    }
}

async fn get_content(state: &AppState) -> CachedContent {
    {
        let cache = state.content_cache.read().await;
        if let Some(cached) = &*cache {
            if cached.fetched_at.elapsed() < state.content_cache_ttl {
                return cached.clone();
            }
        }
    }

    let mut cache = state.content_cache.write().await;
    // Double-check after acquiring write lock
    if let Some(cached) = &*cache {
        if cached.fetched_at.elapsed() < state.content_cache_ttl {
            return cached.clone();
        }
    }

    let content = fetch_all_content(state).await;
    *cache = Some(content.clone());
    content
}

async fn home(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let content = get_content(&state).await;

    let (tags_result, lastfm_result) = tokio::join!(
        fetch_top_tags(&state.client),
        fetch_currently_scrobbling(&state.client),
    );

    let projects_markdown = content.projects;
    let profiles_markdown = content.profiles;
    let contact_markdown = content.contact;
    let tagline_markdown = content.tagline;
    let footer_markdown = content.footer;

    let initial_tags = tags_result.unwrap_or_default();
    let projects_html = render_markdown(&preprocess_markdown(&projects_markdown));
    let profiles_html = render_markdown(&preprocess_markdown(&profiles_markdown));
    let contact_html = render_markdown(&preprocess_markdown(&contact_markdown));
    let tagline_html = render_markdown(&preprocess_markdown(&tagline_markdown));
    let footer_html = render_markdown(&preprocess_markdown(&footer_markdown));
    let app_css_version = asset_version("static/app.css").await;
    let app_js_version = asset_version("static/app.js").await;
    let initial_lastfm = match lastfm_result {
        Ok(payload) => InitialLastfm {
            ok: payload.ok,
            track_name: payload.track_name.unwrap_or_default(),
            artist_name: payload.artist_name.unwrap_or_default(),
            album_art: payload
                .album_art
                .unwrap_or_else(|| "/images/album_art.svg".to_string()),
        },
        Err(_) => InitialLastfm::connecting(),
    };

    let mut context = Context::new();
    context.insert("projects_html", &projects_html);
    context.insert("profiles_html", &profiles_html);
    context.insert("contact_html", &contact_html);
    context.insert("tagline_html", &tagline_html);
    context.insert("footer_html", &footer_html);
    context.insert("initial_tags", &build_template_tags(&initial_tags));
    context.insert("initial_lastfm", &initial_lastfm);
    context.insert("app_css_version", &app_css_version);
    context.insert("app_js_version", &app_js_version);

    match state.templates.render("index.html", &context) {
        Ok(rendered) => Html(rendered).into_response(),
        Err(error) => plain_error(
            StatusCode::INTERNAL_SERVER_ERROR,
            &format!("template render failed: {error}"),
        ),
    }
}

async fn asset_version(path: &str) -> String {
    match tokio::fs::metadata(path).await {
        Ok(metadata) => match metadata.modified() {
            Ok(modified) => match modified.duration_since(UNIX_EPOCH) {
                Ok(duration) => duration.as_secs().to_string(),
                Err(_) => "1".to_string(),
            },
            Err(_) => "1".to_string(),
        },
        Err(_) => "1".to_string(),
    }
}

async fn cv_redirect() -> impl IntoResponse {
    Redirect::temporary("/cv.pdf")
}

async fn catchall_redirect() -> impl IntoResponse {
    Redirect::temporary("/")
}

async fn favicon(State(state): State<Arc<AppState>>) -> Response {
    match state
        .client
        .get("https://github.com/kierandrewett.png")
        .send()
        .await
    {
        Ok(response) => match response.bytes().await {
            Ok(bytes) => bytes_response(
                StatusCode::OK,
                "image/png",
                bytes.to_vec(),
                Some((
                    header::CACHE_CONTROL,
                    HeaderValue::from_static("max-age=604800"),
                )),
            ),
            Err(error) => plain_error(
                StatusCode::BAD_GATEWAY,
                &format!("favicon fetch failed: {error}"),
            ),
        },
        Err(error) => plain_error(
            StatusCode::BAD_GATEWAY,
            &format!("favicon request failed: {error}"),
        ),
    }
}

async fn cv_pdf(State(state): State<Arc<AppState>>) -> Response {
    let release = match state
        .client
        .get("https://api.github.com/repos/EnderDev/cv/releases/latest")
        .send()
        .await
    {
        Ok(response) => response,
        Err(error) => {
            return plain_error(
                StatusCode::BAD_GATEWAY,
                &format!("cv release request failed: {error}"),
            )
        }
    };

    let payload: Value = match release.json().await {
        Ok(payload) => payload,
        Err(error) => {
            return plain_error(
                StatusCode::BAD_GATEWAY,
                &format!("cv release parse failed: {error}"),
            )
        }
    };

    let url = match payload
        .get("assets")
        .and_then(|value| value.as_array())
        .and_then(|assets| assets.first())
        .and_then(|asset| asset.get("browser_download_url"))
        .and_then(|value| value.as_str())
    {
        Some(url) => url,
        None => return plain_error(StatusCode::BAD_GATEWAY, "cv release asset missing"),
    };

    match state.client.get(url).send().await {
        Ok(response) => match response.bytes().await {
            Ok(bytes) => {
                let mut res =
                    bytes_response(StatusCode::OK, "application/pdf", bytes.to_vec(), None);
                res.headers_mut().insert(
                    header::CONTENT_DISPOSITION,
                    HeaderValue::from_static("inline; filename=cv.pdf"),
                );
                res
            }
            Err(error) => plain_error(
                StatusCode::BAD_GATEWAY,
                &format!("cv pdf fetch failed: {error}"),
            ),
        },
        Err(error) => plain_error(
            StatusCode::BAD_GATEWAY,
            &format!("cv pdf request failed: {error}"),
        ),
    }
}

async fn api_lastfm(State(state): State<Arc<AppState>>) -> Response {
    match fetch_currently_scrobbling(&state.client).await {
        Ok(payload) => (StatusCode::OK, Json(payload)).into_response(),
        Err(LastfmFetchError::MissingKey(error)) => (
            StatusCode::BAD_REQUEST,
            Json(LastfmResponse {
                ok: false,
                track_name: None,
                artist_name: None,
                album_art: None,
                now_playing: false,
                error: Some(error),
            }),
        )
            .into_response(),
        Err(LastfmFetchError::Upstream(error)) => (
            StatusCode::BAD_GATEWAY,
            Json(LastfmResponse {
                ok: false,
                track_name: None,
                artist_name: None,
                album_art: None,
                now_playing: false,
                error: Some(error),
            }),
        )
            .into_response(),
    }
}

async fn api_active(State(state): State<Arc<AppState>>) -> Response {
    match fetch_active_status(&state.client).await {
        Ok(payload) => (StatusCode::OK, Json(payload)).into_response(),
        Err(error) => (
            StatusCode::BAD_GATEWAY,
            Json(ActiveResponse {
                ok: false,
                active: false,
                status: "unknown".to_string(),
                since: None,
                error: Some(error),
            }),
        )
            .into_response(),
    }
}

async fn fetch_active_status(client: &Client) -> Result<ActiveResponse, String> {
    let response = client
        .get("https://is-kieran.drewett.dev/active?format=json")
        .timeout(Duration::from_secs(4))
        .send()
        .await
        .map_err(|error| error.to_string())?;

    if !response.status().is_success() {
        return Err(format!("active status returned HTTP {}", response.status()));
    }

    let payload = response
        .json::<ActiveUpstreamResponse>()
        .await
        .map_err(|error| error.to_string())?;

    let (status, active) = normalise_active_status(&payload.status);

    Ok(ActiveResponse {
        ok: true,
        active,
        status: status.to_string(),
        since: payload.since,
        error: None,
    })
}

fn normalise_active_status(status: &str) -> (&'static str, bool) {
    match status.trim().to_ascii_lowercase().as_str() {
        "yes" | "active" | "online" | "true" => ("active", true),
        "no" | "inactive" | "offline" | "false" => ("inactive", false),
        _ => ("unknown", false),
    }
}

async fn fetch_currently_scrobbling(client: &Client) -> Result<LastfmResponse, LastfmFetchError> {
    let api_key = match env::var("LASTFM_API_KEY") {
        Ok(value) if !value.is_empty() => value,
        _ => {
            return Err(LastfmFetchError::MissingKey(
                "No API key provided.".to_string(),
            ))
        }
    };

    let response = client
        .get("https://ws.audioscrobbler.com/2.0")
        .query(&[
            ("method", "user.getrecenttracks"),
            ("user", "EnderDev"),
            ("api_key", api_key.as_str()),
            ("format", "json"),
            ("limit", "1"),
        ])
        .send()
        .await
        .map_err(|error| LastfmFetchError::Upstream(error.to_string()))?;

    let payload: Value = response
        .json()
        .await
        .map_err(|error| LastfmFetchError::Upstream(error.to_string()))?;

    let track = payload
        .get("recenttracks")
        .and_then(|value| value.get("track"))
        .and_then(|value| value.as_array())
        .and_then(|tracks| tracks.first());

    let track_name = track
        .and_then(|value| value.get("name"))
        .and_then(|value| value.as_str())
        .unwrap_or("Track");
    let artist_name = track
        .and_then(|value| value.get("artist"))
        .and_then(|value| value.get("#text"))
        .and_then(|value| value.as_str())
        .unwrap_or("Artist");
    let album_art = track
        .and_then(|value| value.get("image"))
        .and_then(|value| value.as_array())
        .and_then(|images| images.last())
        .and_then(|value| value.get("#text"))
        .and_then(|value| value.as_str())
        .filter(|value| !value.is_empty())
        .unwrap_or("/images/album_art.svg");
    let is_playing = track
        .and_then(|value| value.get("@attr"))
        .and_then(|value| value.get("nowplaying"))
        .and_then(|value| value.as_str())
        .map(|value| value == "true")
        .unwrap_or(false);

    Ok(LastfmResponse {
        ok: true,
        track_name: is_playing.then(|| track_name.to_string()),
        artist_name: is_playing.then(|| artist_name.to_string()),
        album_art: Some(album_art.to_string()),
        now_playing: is_playing,
        error: None,
    })
}

async fn api_lastfm_tags(State(state): State<Arc<AppState>>) -> Response {
    match fetch_top_tags(&state.client).await {
        Ok(tags) => (
            StatusCode::OK,
            Json(TagsResponse {
                ok: true,
                tags,
                error: None,
            }),
        )
            .into_response(),
        Err(error) => (
            StatusCode::BAD_GATEWAY,
            Json(TagsResponse {
                ok: false,
                tags: vec![],
                error: Some(error),
            }),
        )
            .into_response(),
    }
}

async fn fetch_top_tags(client: &Client) -> Result<Vec<String>, String> {
    let response = client
        .get("https://raw.githubusercontent.com/kierandrewett/lastfm-data/main/week_10_tags.json")
        .send()
        .await
        .map_err(|error| error.to_string())?;

    let tags = response
        .json::<Vec<String>>()
        .await
        .map_err(|error| error.to_string())?;

    Ok(tags)
}

fn build_template_tags(tags: &[String]) -> Vec<TemplateTag> {
    tags.iter()
        .take(7)
        .map(|tag| TemplateTag {
            label: title_case_tag(tag),
            url: format!("https://www.last.fm/tag/{}", tag.to_lowercase()),
        })
        .collect()
}

fn build_template_context() -> tera::Context {
    let now = chrono::Local::now();
    let mut ctx = tera::Context::new();
    ctx.insert("current_year", &now.year());
    ctx.insert("current_month", &now.format("%B").to_string());
    ctx.insert("current_date", &now.format("%Y-%m-%d").to_string());
    ctx
}

fn preprocess_markdown(content: &str) -> String {
    let ctx = build_template_context();
    Tera::one_off(content, &ctx, false).unwrap_or_else(|_| content.to_string())
}

fn render_markdown(markdown: &str) -> String {
    let mut options = Options::empty();
    options.insert(Options::ENABLE_STRIKETHROUGH);

    let parser = Parser::new_ext(markdown, options);
    let mut output = String::new();
    push_html(&mut output, parser);
    output.replace(
        "<a href=\"",
        "<a target=\"_blank\" rel=\"noreferrer\" href=\"",
    )
}

fn title_case_tag(tag: &str) -> String {
    tag.split(' ')
        .map(|word| {
            word.split('-')
                .map(|part| {
                    let mut chars = part.chars();
                    match chars.next() {
                        Some(first) => {
                            let mut output = first.to_uppercase().collect::<String>();
                            output.push_str(chars.as_str());
                            output
                        }
                        None => String::new(),
                    }
                })
                .collect::<Vec<_>>()
                .join("-")
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn bytes_response(
    status: StatusCode,
    content_type: &'static str,
    bytes: Vec<u8>,
    extra_header: Option<(header::HeaderName, HeaderValue)>,
) -> Response {
    let mut response = Response::new(axum::body::Body::from(bytes));
    *response.status_mut() = status;
    response
        .headers_mut()
        .insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));

    if let Some((name, value)) = extra_header {
        response.headers_mut().insert(name, value);
    }

    response
}

fn plain_error(status: StatusCode, message: &str) -> Response {
    (status, message.to_string()).into_response()
}
