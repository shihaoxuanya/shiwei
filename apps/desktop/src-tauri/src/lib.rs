mod analytics;
mod provider_store;
mod updates;
mod worker_client;

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
struct ImportFailure {
    path: String,
    reason: String,
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
async fn search_lexical(
    state: State<'_, WorkerState>,
    query: String,
    limit: Option<u32>,
) -> Result<Vec<LexicalHit>, String> {
    let client = state.0.clone();
    let result: LexicalSearchResult = tauri::async_runtime::spawn_blocking(move || {
        client.request(
            "search_lexical",
            serde_json::json!({ "query": query, "limit": limit.unwrap_or(20) }),
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
async fn provider_test(
    app: tauri::AppHandle,
    state: State<'_, WorkerState>,
    config: ProviderInput,
) -> Result<serde_json::Value, String> {
    let (public, api_key, embedding_key) = provider_store::resolve_for_request(&app, config)?;
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request(
            "provider_test",
            public.worker_params(api_key, embedding_key),
        )
    })
    .await
    .map_err(|error| format!("模型测试任务异常：{error}"))?
    .map_err(|error| error.user_message())
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
    state: State<'_, WorkerState>,
    source_id: String,
) -> Result<serde_json::Value, String> {
    let client = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        client.request(
            "reindex_source",
            serde_json::json!({ "sourceId": source_id }),
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
        .invoke_handler(tauri::generate_handler![
            release_status,
            analytics_consent,
            analytics_track,
            updates::update_check,
            updates::update_install,
            worker_ping,
            import_paths,
            list_sources,
            search_lexical,
            search_hybrid,
            chat_ask,
            list_conversations,
            get_conversation,
            delete_conversation,
            list_notes,
            get_note,
            create_note,
            update_note,
            delete_note,
            provider_status,
            provider_test,
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
