mod analytics;
mod provider_store;
mod updates;
mod worker_client;
mod library_location;

use provider_store::{ProviderInput, ProviderStatus};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Arc;
use tauri::{Emitter, Manager, State};
use tauri_plugin_opener::OpenerExt;
use worker_client::{WorkerClient, WorkerError};

#[derive(Clone)]
struct WorkerState(Arc<WorkerClient>);

#[tauri::command]
fn release_status(
    app: tauri::AppHandle,
    state: State<'_, Arc<analytics::AnalyticsService>>,
) -> serde_json::Value {
    serde_json::json!({"version": env!("CARGO_PKG_VERSION"), "channel":"stable", "updaterConfigured": updates::configured(&app), "analytics":state.status()})
}
#[tauri::command]
fn analytics_consent(
    state: State<'_, Arc<analytics::AnalyticsService>>,
    enabled: bool,
) -> Result<(), String> {
    let previous = state.status().enabled;
    state.set_enabled(enabled)?;
    if enabled && !previous {
        state.inner().opened();
    }
    Ok(())
}
#[tauri::command]
fn analytics_track(
    state: State<'_, Arc<analytics::AnalyticsService>>,
    event: String,
    properties: serde_json::Value,
) {
    state.inner().track(&event, properties);
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkerPingResult {
    status: String,
    protocol_version: String,
    worker_version: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ImportItem {
    status: String,
    path: String,
    source_id: String,
    filename: String,
    content_hash: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ImportFailure {
    path: String,
    reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    input_kind: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
struct ImportSummary {
    imported: usize,
    skipped: usize,
    failed: usize,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ImportReport {
    job_id: String,
    imported: Vec<ImportItem>,
    skipped: Vec<ImportItem>,
    failed: Vec<ImportFailure>,
    summary: ImportSummary,
    #[serde(default)]
    embedding_index: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct SourceSummary {
    id: String,
    original_path: String,
    filename: String,
    stored_path: String,
    content_hash: String,
    size: u64,
    mime_type: Option<String>,
    imported_at: String,
    status: String,
    error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    source_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    original_url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    final_url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    captured_at: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    body_hash: Option<String>,
    #[serde(default)]
    retrieval: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
struct SourceList {
    sources: Vec<SourceSummary>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct LexicalHit {
    chunk_id: String,
    document_id: String,
    content: String,
    document_title: String,
    filename: String,
    heading_path: Option<String>,
    lexical_score: f64,
    matched_by: Vec<String>,
    source_id: Option<String>,
    source_type: Option<String>,
    page_number: Option<u32>,
    sheet_name: Option<String>,
    slide_number: Option<u32>,
}

#[derive(Debug, Deserialize)]
struct LexicalSearchResult {
    query: String,
    hits: Vec<LexicalHit>,
}

#[tauri::command]
async fn worker_ping(state: State<'_, WorkerState>) -> Result<WorkerPingResult, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.ping())
        .await
        .map_err(|error| format!("Worker 调用任务异常：{error}"))?
        .map_err(|error| error.user_message())
}

#[tauri::command]
async fn worker_info(state: State<'_, WorkerState>) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.request("worker_info", serde_json::json!({})))
        .await.map_err(|_| "读取本地数据位置失败".to_string())?.map_err(|e| e.user_message())
}

#[tauri::command]
async fn library_relocate(app: tauri::AppHandle, state: State<'_, WorkerState>, destination_parent: String) -> Result<serde_json::Value, String> {
    let destination = PathBuf::from(&destination_parent);
    if !destination.is_absolute() || destination_parent.len() > 32768 || destination_parent.contains('\0') {
        return Err("请选择有效的本地文件夹".into());
    }
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.relocate_library(
        serde_json::json!({"destinationParent": destination_parent}),
        move |event| {
            if event.get("event").and_then(|v| v.as_str()) == Some("library_migration_progress") {
                if let Some(data) = event.get("data") { let _ = app.emit("library-migration-progress", data.clone()); }
            }
        },
    )).await.map_err(|_| "迁移任务中断，请重新打开应用确认当前资料库位置；原库仍保留。".to_string())?
      .map_err(|error| error.user_message())
}

#[tauri::command]
async fn index_note(state: State<'_, WorkerState>, note_id: String, revision: String) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.request("index_note", serde_json::json!({"noteId":note_id,"revision":revision})))
        .await.map_err(|_| "更新笔记搜索失败".to_string())?.map_err(|e| e.user_message())
}

#[tauri::command]
async fn import_paths(
    app: tauri::AppHandle,
    state: State<'_, WorkerState>,
    paths: Vec<String>,
) -> Result<ImportReport, String> {
    let analytics = app
        .state::<Arc<analytics::AnalyticsService>>()
        .inner()
        .clone();
    analytics.track("import_started", serde_json::json!({}));
    let started = std::time::Instant::now();
    let client = state.0.clone();
    let event_app = app.clone();
    let result: Result<ImportReport, String> = tauri::async_runtime::spawn_blocking(move || {
        client.request_with_events(
            "import_paths",
            serde_json::json!({ "paths": paths }),
            move |event| {
                if event.get("event").and_then(|value| value.as_str()) == Some("job_progress") {
                    if let Some(data) = event.get("data") {
                        let _ = event_app.emit("import-progress", data.clone());
                    }
                }
            },
        )
    })
    .await
    .map_err(|error| format!("导入任务异常：{error}"))?
    .map_err(|error| error.user_message());
    match &result {
        Ok(report) => {
            let n = report.summary.imported + report.summary.skipped + report.summary.failed;
            let bucket = if n <= 1 {
                "1"
            } else if n <= 10 {
                "2-10"
            } else if n <= 100 {
                "11-100"
            } else {
                "100+"
            };
            let metrics = serde_json::json!({"file_count_bucket": bucket, "success_count": report.summary.imported, "failure_count": report.summary.failed, "duration_ms": started.elapsed().as_millis().min(86_400_000) as u64});
            // A completed all-failure batch is not a successful first import.
            if report.summary.imported > 0 {
                analytics.track("import_completed", metrics.clone());
            }
            if report.summary.failed > 0 {
                analytics.track("import_failed", metrics);
            }
        }
        Err(_) => analytics.track("import_failed", serde_json::json!({"failure_count": 1})),
    }
    result
}

#[tauri::command]
async fn list_sources(state: State<'_, WorkerState>) -> Result<Vec<SourceSummary>, String> {
    let client = state.0.clone();
    let result: SourceList = tauri::async_runtime::spawn_blocking(move || {
        client.request("list_sources", serde_json::json!({}))
    })
    .await
    .map_err(|error| format!("读取资料任务异常：{error}"))?
    .map_err(|error| error.user_message())?;
    Ok(result.sources)
}

#[tauri::command]
async fn import_url(app: tauri::AppHandle, state: State<'_, WorkerState>, url: String) -> Result<ImportReport, String> {
    // Python performs the public-address, DNS pinning and redirect checks. Never
    // attach provider credentials or log user URLs in native diagnostics.
    validate_web_url(&url)?;
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.request_with_events(
        "import_url", serde_json::json!({"url": url}), move |event| {
            if event.get("event").and_then(|v| v.as_str()) == Some("job_progress") {
                if let Some(data) = event.get("data") { let _ = app.emit("import-progress", data.clone()); }
            }
        },
    )).await.map_err(|_| "网页保存任务中断，请重试。".to_string())?.map_err(|e| e.user_message())
}

#[tauri::command]
async fn get_web_snapshot(state: State<'_, WorkerState>, source_id: String) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.request("get_web_snapshot", serde_json::json!({"sourceId": source_id})))
        .await.map_err(|_| "读取网页快照失败。".to_string())?.map_err(|e| e.user_message())
}

fn validate_web_url(value: &str) -> Result<reqwest::Url, String> {
    let invalid = || "请输入有效的 HTTP 或 HTTPS 网页地址，不要包含账号密码。".to_string();
    if value.len() > 8192 || value.chars().any(char::is_control) { return Err(invalid()); }
    let url = reqwest::Url::parse(value).map_err(|_| invalid())?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() || !url.username().is_empty() || url.password().is_some() { return Err(invalid()); }
    Ok(url)
}

#[tauri::command]
async fn open_web_source(app: tauri::AppHandle, state: State<'_, WorkerState>, source_id: String) -> Result<(), String> {
    // Only resolve a stored snapshot identity; renderer-controlled raw URLs are
    // never forwarded to the OS opener, and raw HTML archives stay inert.
    let snapshot = get_web_snapshot(state, source_id).await?;
    let url = snapshot.get("finalUrl").and_then(|v| v.as_str()).ok_or_else(|| "网页快照没有有效网址。".to_string())?;
    let url = validate_web_url(url)?;
    app.opener().open_url(url.as_str(), None::<&str>).map_err(|_| "无法打开原网页，请检查默认浏览器。".to_string())
}

#[tauri::command]
async fn search_lexical(
    state: State<'_, WorkerState>,
    query: String,
    limit: Option<u32>,
    source_type: Option<String>,
) -> Result<Vec<LexicalHit>, String> {
    let client = state.0.clone();
    let result: LexicalSearchResult = tauri::async_runtime::spawn_blocking(move || {
        client.request(
            "search_lexical",
            serde_json::json!({ "query": query, "limit": limit.unwrap_or(20), "sourceType": source_type }),
        )
    })
    .await
    .map_err(|error| format!("搜索任务异常：{error}"))?
    .map_err(|error| error.user_message())?;
    let _ = result.query;
    Ok(result.hits)
}

#[tauri::command]
async fn provider_status(app: tauri::AppHandle) -> Result<ProviderStatus, String> {
    provider_store::status(&app)
}

#[tauri::command]
fn provider_help(app: tauri::AppHandle, provider_id: String) -> Result<(), String> {
    let url = match provider_id.as_str() {
        "deepseek" => "https://api-docs.deepseek.com/",
        "qwen" => "https://help.aliyun.com/zh/model-studio/get-api-key",
        _ => return Err("尚未核实该服务商的帮助地址，请查看官方控制台".into()),
    };
    app.opener().open_url(url, None::<&str>).map_err(|_| "无法打开官方帮助页面".into())
}

#[tauri::command]
async fn provider_test(
    app: tauri::AppHandle,
    state: State<'_, WorkerState>,
    mut config: ProviderInput,
    target: Option<String>,
) -> Result<serde_json::Value, String> {
    let target = target.unwrap_or_else(|| "all".into());
    if !["all", "chat", "embedding"].contains(&target.as_str()) { return Err("无效的测试类型".into()); }
    if target == "chat" { config.public.embedding_mode = "none".into(); }
    let (public, api_key, embedding_key) = if target == "embedding" {
        provider_store::resolve_embedding_test(&app, config)?
    } else { provider_store::resolve_for_request(&app, config)? };
    let mut params = public.worker_params(api_key, embedding_key);
    params["target"] = serde_json::json!(target);
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request(
            "provider_test",
            params,
        )
    })
    .await
    .map_err(|error| format!("模型测试任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn provider_clear_key(app: tauri::AppHandle, state: State<'_, WorkerState>, target: String) -> Result<(), String> {
    if !["chat", "embedding"].contains(&target.as_str()) { return Err("无效的凭据类型".into()); }
    // First revoke the live client. If keyring/config persistence fails, the
    // worker must not keep using a credential the user has asked to remove.
    let clear_client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || clear_client.request::<serde_json::Value>("provider_clear", serde_json::json!({})))
        .await.map_err(|_| "停用当前模型连接失败，凭据未清除".to_string())?.map_err(|e| e.user_message())?;
    provider_store::clear_key(&app, &target)?;
    let params = provider_store::runtime_params(&app)?;
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        if let Some(params) = params { client.request::<serde_json::Value>("provider_configure", params) }
        else { client.request::<serde_json::Value>("provider_clear", serde_json::json!({})) }
    }).await.map_err(|_| "清除模型凭据失败".to_string())?.map_err(|e| e.user_message())?;
    Ok(())
}

#[tauri::command]
async fn provider_save(
    app: tauri::AppHandle,
    state: State<'_, WorkerState>,
    config: ProviderInput,
) -> Result<serde_json::Value, String> {
    provider_store::save(&app, &config)?;
    let params = provider_store::runtime_params(&app)?.ok_or("模型配置保存失败")?;
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.request("provider_configure", params))
        .await
        .map_err(|error| format!("保存模型配置任务异常：{error}"))?
        .map_err(|error| error.user_message())
}

#[tauri::command]
async fn provider_models(
    app: tauri::AppHandle,
    state: State<'_, WorkerState>,
    config: ProviderInput,
    target: String,
) -> Result<serde_json::Value, String> {
    let (public, key, embedding_key) = provider_store::resolve_for_request(&app, config)?;
    let mut params = public.worker_params(key, embedding_key);
    params["target"] = serde_json::json!(target);
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.request("provider_models", params))
        .await
        .map_err(|e| format!("读取模型列表任务异常：{e}"))?
        .map_err(|e| e.user_message())
}

#[tauri::command]
async fn rebuild_embeddings(state: State<'_, WorkerState>) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request("rebuild_embeddings", serde_json::json!({}))
    })
    .await
    .map_err(|error| format!("索引重建任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn index_status(state: State<'_, WorkerState>) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request("index_status", serde_json::json!({}))
    })
    .await
    .map_err(|error| format!("读取索引状态任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn search_hybrid(
    state: State<'_, WorkerState>,
    query: String,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request("search_hybrid", serde_json::json!({ "query": query }))
    })
    .await
    .map_err(|error| format!("混合检索任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn chat_ask(
    app: tauri::AppHandle,
    state: State<'_, WorkerState>,
    query: String,
    conversation_id: Option<String>,
    request_id: String,
) -> Result<serde_json::Value, String> {
    if uuid::Uuid::parse_str(&request_id).is_err() {
        return Err("对话请求标识无效".into());
    }
    let analytics = app
        .state::<Arc<analytics::AnalyticsService>>()
        .inner()
        .clone();
    analytics.track(
        "question_asked",
        serde_json::json!({"conversation_mode":"knowledge_first"}),
    );
    let client = state.0.clone();
    let event_app = app.clone();
    let result: Result<serde_json::Value, String> = tauri::async_runtime::spawn_blocking(move || {
        client.request_with_events(
            "chat",
            serde_json::json!({
                "query": query,
                "conversationId": conversation_id,
                "clientRequestId": request_id,
                "stream": true
            }),
            move |event| {
                if event.get("event").and_then(|value| value.as_str()) == Some("chat_token") {
                    if let Some(data) = event.get("data") {
                        let _ = event_app.emit("chat-token", serde_json::json!({"token": data.get("token"), "requestId": request_id}));
                    }
                }
            },
        )
    })
    .await
    .map_err(|error| format!("回答任务异常：{error}"))?
    .map_err(|error| error.user_message());
    if let Ok(answer) = &result {
        let kind = answer["answerKind"].as_str();
        if kind == Some("knowledge")
            && answer["citations"]
                .as_array()
                .is_some_and(|a| !a.is_empty())
        {
            // Worker has already applied relevance/evidence gates and validated citation IDs.
            analytics.track("retrieval_succeeded", serde_json::json!({}));
        } else if kind == Some("not_found") {
            analytics.track("retrieval_abstained", serde_json::json!({}));
        }
    }
    result
}

#[tauri::command]
async fn chat_cancel(state: State<'_, WorkerState>, request_id: String) -> Result<(), String> {
    if uuid::Uuid::parse_str(&request_id).is_err() {
        return Err("对话请求标识无效".into());
    }
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || client.cancel_chat(&request_id))
        .await.map_err(|_| "停止请求未能送达，请重试".to_string())?
        .map_err(|error| error.user_message())
}

#[tauri::command]
async fn list_conversations(
    state: State<'_, WorkerState>,
    query: Option<String>,
    offset: Option<u32>,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request("list_conversations", serde_json::json!({ "query": query.unwrap_or_default(), "offset": offset.unwrap_or_default() }))
    })
    .await
    .map_err(|error| format!("读取对话任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn get_conversation(
    state: State<'_, WorkerState>,
    conversation_id: String,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request(
            "get_conversation",
            serde_json::json!({ "conversationId": conversation_id }),
        )
    })
    .await
    .map_err(|error| format!("读取对话内容任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn delete_conversation(
    state: State<'_, WorkerState>,
    conversation_id: String,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request(
            "delete_conversation",
            serde_json::json!({ "conversationId": conversation_id }),
        )
    })
    .await
    .map_err(|error| format!("删除对话任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn list_notes(
    state: State<'_, WorkerState>,
    query: Option<String>,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request(
            "list_notes",
            serde_json::json!({ "query": query.unwrap_or_default() }),
        )
    })
    .await
    .map_err(|error| format!("读取笔记任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn get_note(
    state: State<'_, WorkerState>,
    note_id: String,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request("get_note", serde_json::json!({ "noteId": note_id }))
    })
    .await
    .map_err(|error| format!("打开笔记任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn create_note(
    app: tauri::AppHandle,
    state: State<'_, WorkerState>,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        client.request("create_note", serde_json::json!({}))
    })
    .await
    .map_err(|error| format!("新建笔记任务异常：{error}"))?
    .map_err(|error| error.user_message());
    if result.is_ok() {
        app.state::<Arc<analytics::AnalyticsService>>()
            .track("note_created", serde_json::json!({}));
    }
    result
}

#[tauri::command]
async fn update_note(
    state: State<'_, WorkerState>,
    note_id: String,
    title: String,
    content: String,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request(
            "update_note",
            serde_json::json!({ "noteId": note_id, "title": title, "content": content }),
        )
    })
    .await
    .map_err(|error| format!("保存笔记任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn delete_note(
    state: State<'_, WorkerState>,
    note_id: String,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request("delete_note", serde_json::json!({ "noteId": note_id }))
    })
    .await
    .map_err(|error| format!("删除笔记任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
fn open_original(app: tauri::AppHandle, path: String) -> Result<(), String> {
    let target = PathBuf::from(path);
    if !target.is_file() {
        return Err("原始文件已被移动或删除".to_string());
    }
    app.opener()
        .open_path(target.to_string_lossy().into_owned(), None::<&str>)
        .map_err(|error| format!("无法打开原始文件：{error}"))
}

#[tauri::command]
fn reveal_original(app: tauri::AppHandle, path: String) -> Result<(), String> {
    let target = PathBuf::from(path);
    if !target.exists() {
        return Err("原始文件已被移动或删除".to_string());
    }
    app.opener()
        .reveal_item_in_dir(target)
        .map_err(|error| format!("无法在资源管理器中显示：{error}"))
}

#[tauri::command]
async fn delete_source(
    state: State<'_, WorkerState>,
    source_id: String,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request(
            "delete_source",
            serde_json::json!({ "sourceId": source_id }),
        )
    })
    .await
    .map_err(|error| format!("删除资料任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[tauri::command]
async fn reindex_source(
    app: tauri::AppHandle,
    state: State<'_, WorkerState>,
    source_id: String,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request_with_events(
            "reindex_source",
            serde_json::json!({ "sourceId": source_id }),
            move |event| {
                if event.get("event").and_then(|v| v.as_str()) == Some("job_progress") {
                    if let Some(data) = event.get("data") { let _ = app.emit("import-progress", data.clone()); }
                }
            },
        )
    })
    .await
    .map_err(|error| format!("重新理解资料任务异常：{error}"))?
    .map_err(|error| error.user_message())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let worker = Arc::new(WorkerClient::new());
    let startup_worker = worker.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(updates::UpdateState::default())
        .manage(WorkerState(worker))
        .setup(move |app| {
            let location_path = library_location::config_path(app.handle())
                .map_err(std::io::Error::other)?;
            startup_worker.set_location_config(location_path)?;
            // Release metadata is separate from the user's knowledge DB; failures are fail-closed.
            let path = app
                .path()
                .app_local_data_dir()
                .ok()
                .map(|path| path.join("installation.json"));
            app.manage(analytics::initialize(path));
            let previous_hook = std::panic::take_hook();
            std::panic::set_hook(Box::new(move |info| {
                analytics::report_error("rust_panic", "desktop");
                previous_hook(info);
            }));
            if let Err(error) = startup_worker.start() {
                eprintln!("拾微 Worker 启动失败，将在首次调用时重试：{error}");
            }
            if let Ok(Some(params)) = provider_store::runtime_params(app.handle()) {
                if let Err(error) =
                    startup_worker.request::<serde_json::Value>("provider_configure", params)
                {
                    eprintln!("拾微恢复模型配置失败：{error}");
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.state::<WorkerState>().0.is_relocating() { api.prevent_close(); }
            }
        })
        .invoke_handler(tauri::generate_handler![
            release_status,
            analytics_consent,
            analytics_track,
            updates::update_check,
            updates::update_install,
            worker_ping,
            worker_info,
            library_relocate,
            import_paths,
            import_url,
            get_web_snapshot,
            open_web_source,
            list_sources,
            search_lexical,
            search_hybrid,
            chat_ask,
            chat_cancel,
            list_conversations,
            get_conversation,
            delete_conversation,
            list_notes,
            get_note,
            create_note,
            update_note,
            index_note,
            delete_note,
            provider_status,
            provider_help,
            provider_test,
            provider_clear_key,
            provider_models,
            provider_save,
            rebuild_embeddings,
            index_status,
            open_original,
            reveal_original,
            delete_source,
            reindex_source
        ])
        .run(tauri::generate_context!())
        .expect("拾微桌面应用启动失败");
}

impl WorkerError {
    fn user_message(&self) -> String {
        if let WorkerError::Remote { code, message } = self {
            if code == "CHAT_CANCELLED" || code.starts_with("LIBRARY_") {
                return message.clone(); // User-requested stop is not an error statistic.
            }
        }
        let kind = match self {
            WorkerError::Exited => "worker_exited",
            WorkerError::Timeout => "worker_timeout",
            _ => "worker_error",
        };
        analytics::report_error(kind, "worker_client");
        match self {
            WorkerError::ExecutableNotFound => {
                "未找到拾微 AI Worker 运行环境，请重新安装应用".to_string()
            }
            WorkerError::Timeout => "AI Worker 响应超时，请稍后重试".to_string(),
            WorkerError::Exited => "AI Worker 已意外退出，拾微会在下次操作时重启".to_string(),
            WorkerError::Protocol(message) => format!("AI Worker 协议错误：{message}"),
            WorkerError::Remote { code, message } => {
                if code == "PROVIDER_ERROR" {
                    format!("模型服务错误：{message}")
                } else {
                    message.clone()
                }
            }
            WorkerError::Io(_) | WorkerError::Json(_) => {
                "无法连接本地 AI Worker，请查看应用日志".to_string()
            }
        }
    }
}

#[cfg(test)]
mod web_source_tests {
    use super::*;
    #[test]
    fn web_opener_accepts_only_http_urls_without_credentials() {
        for value in ["https://example.com/article?q=中文", "http://example.com/"] { assert!(validate_web_url(value).is_ok()); }
        for value in ["file:///C:/secret", "javascript:alert(1)", "data:text/html,test", "https://user:secret@example.com", "https://user@example.com", "https://example.com\n", "not a url"] { assert!(validate_web_url(value).is_err(), "unsafe URL accepted"); }
    }
    #[test]
    fn source_metadata_and_retry_kind_survive_native_serialization() {
        let source: SourceSummary = serde_json::from_value(serde_json::json!({"id":"w1","originalPath":"","storedPath":"","filename":"网页标题","contentHash":"hash","size":12,"mimeType":"text/html","importedAt":"2026-09-10","status":"searchable","sourceType":"web_page","originalUrl":"https://example.com/a","finalUrl":"https://example.com/b","capturedAt":"2026-09-10","bodyHash":"body"})).unwrap();
        let wire = serde_json::to_value(source).unwrap();
        assert_eq!(wire["sourceType"], "web_page"); assert_eq!(wire["bodyHash"], "body"); assert_eq!(wire["originalUrl"], "https://example.com/a");
        let failure: ImportFailure = serde_json::from_value(serde_json::json!({"path":"https://example.com/a","reason":"超时","inputKind":"url"})).unwrap();
        assert_eq!(serde_json::to_value(failure).unwrap()["inputKind"], "url");
    }
}
