use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use thiserror::Error;
use uuid::Uuid;

const PROTOCOL_VERSION: &str = "1.0";
const DEFAULT_TIMEOUT: Duration = Duration::from_secs(180);

#[derive(Debug, Error)]
pub enum WorkerError {
    #[error("AI Worker executable not found")]
    ExecutableNotFound,
    #[error("AI Worker response timed out")]
    Timeout,
    #[error("AI Worker exited")]
    Exited,
    #[error("AI Worker protocol error: {0}")]
    Protocol(String),
    #[error("AI Worker remote error {code}: {message}")]
    Remote { code: String, message: String },
    #[error(transparent)]
    Io(#[from] std::io::Error),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

#[derive(Debug, Serialize)]
struct RpcRequest<'a> {
    jsonrpc: &'static str,
    protocol_version: &'static str,
    id: String,
    method: &'a str,
    params: Value,
}

#[derive(Debug, Deserialize)]
struct RpcErrorPayload {
    code: String,
    message: String,
}

#[derive(Debug, Deserialize)]
struct RpcResponse {
    jsonrpc: String,
    protocol_version: String,
    id: Option<String>,
    result: Option<Value>,
    error: Option<RpcErrorPayload>,
}

struct WorkerProcess {
    child: Child,
    stdin: ChildStdin,
    responses: Receiver<String>,
}

pub struct WorkerClient {
    process: Mutex<Option<WorkerProcess>>,
    timeout: Duration,
    updating: AtomicBool,
}

impl WorkerClient {
    pub fn new() -> Self {
        Self {
            process: Mutex::new(None),
            timeout: DEFAULT_TIMEOUT,
            updating: AtomicBool::new(false),
        }
    }

    pub fn start(&self) -> Result<(), WorkerError> {
        let mut process = self.process.lock().map_err(|_| WorkerError::Exited)?;
        if self.updating.load(Ordering::SeqCst) {
            return Err(WorkerError::Exited);
        }
        if let Some(running) = process.as_mut() {
            if running.child.try_wait()?.is_none() {
                return Ok(());
            }
        }
        *process = Some(spawn_worker()?);
        Ok(())
    }

    pub fn ping<T: DeserializeOwned>(&self) -> Result<T, WorkerError> {
        self.request("ping", json!({}))
    }

    pub fn prepare_update(&self) -> Result<(), WorkerError> {
        // Never terminate an active import, save, migration or conversation.
        let mut process = self.process.try_lock().map_err(|_| WorkerError::Timeout)?;
        self.updating.store(true, Ordering::SeqCst);
        if let Some(mut running) = process.take() {
            let _ = running.child.kill();
            let _ = running.child.wait();
        }
        Ok(())
    }
    pub fn resume_after_update(&self) {
        self.updating.store(false, Ordering::SeqCst);
    }

    pub fn request<T: DeserializeOwned>(
        &self,
        method: &str,
        params: Value,
    ) -> Result<T, WorkerError> {
        self.request_internal(method, params, None)
    }

    pub fn request_with_events<T, F>(
        &self,
        method: &str,
        params: Value,
        mut on_event: F,
    ) -> Result<T, WorkerError>
    where
        T: DeserializeOwned,
        F: FnMut(&Value),
    {
        self.request_internal(method, params, Some(&mut on_event))
    }

    fn request_internal<T: DeserializeOwned>(
        &self,
        method: &str,
        params: Value,
        mut on_event: Option<&mut dyn FnMut(&Value)>,
    ) -> Result<T, WorkerError> {
        self.start()?;
        let mut process_guard = self.process.lock().map_err(|_| WorkerError::Exited)?;
        if self.updating.load(Ordering::SeqCst) {
            return Err(WorkerError::Exited);
        }
        let process = process_guard.as_mut().ok_or(WorkerError::Exited)?;

        if process.child.try_wait()?.is_some() {
            *process_guard = None;
            return Err(WorkerError::Exited);
        }

        let request_id = Uuid::new_v4().to_string();
        let request = RpcRequest {
            jsonrpc: "2.0",
            protocol_version: PROTOCOL_VERSION,
            id: request_id.clone(),
            method,
            params,
        };
        serde_json::to_writer(&mut process.stdin, &request)?;
        process.stdin.write_all(b"\n")?;
        process.stdin.flush()?;

        // OCR and batch indexing are long-running local work, not chat requests.
        let timeout = if matches!(
            method,
            "import_paths" | "reindex_source" | "rebuild_embeddings"
        ) {
            Duration::from_secs(1800)
        } else {
            self.timeout
        };
        let deadline = Instant::now() + timeout;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(WorkerError::Timeout);
            }
            let raw_response =
                process
                    .responses
                    .recv_timeout(remaining)
                    .map_err(|error| match error {
                        mpsc::RecvTimeoutError::Timeout => WorkerError::Timeout,
                        mpsc::RecvTimeoutError::Disconnected => WorkerError::Exited,
                    })?;
            let envelope: Value = serde_json::from_str(&raw_response)?;
            if envelope.get("event").is_some() {
                if event_belongs_to(&envelope, &request_id) {
                    if let Some(handler) = on_event.as_deref_mut() {
                        handler(&envelope);
                    }
                }
                continue;
            }
            // A late reply from a previously timed-out operation must not poison
            // the next request on this single, serialized worker connection.
            if envelope.get("id").and_then(Value::as_str) != Some(&request_id) {
                continue;
            }
            return parse_response(&raw_response, &request_id);
        }
    }
}

fn event_belongs_to(event: &Value, request_id: &str) -> bool {
    event.get("requestId").and_then(Value::as_str) == Some(request_id)
}

impl Drop for WorkerClient {
    fn drop(&mut self) {
        if let Ok(mut process) = self.process.lock() {
            if let Some(running) = process.as_mut() {
                let _ = running.child.kill();
                let _ = running.child.wait();
            }
        }
    }
}

fn spawn_worker() -> Result<WorkerProcess, WorkerError> {
    let worker_project = worker_project_dir_from(Path::new(env!("CARGO_MANIFEST_DIR")));
    let development_python = worker_project
        .join(".venv")
        .join("Scripts")
        .join("python.exe");
    let installed_sidecar = env::current_exe().ok().and_then(|path| {
        path.parent()
            .map(|parent| parent.join("shiwei-ai-worker.exe"))
    });
    let mut command = if cfg!(debug_assertions) && development_python.is_file() {
        Command::new(&development_python)
    } else if let Some(sidecar) = installed_sidecar.filter(|path| path.is_file()) {
        Command::new(sidecar)
    } else if development_python.is_file() {
        Command::new(development_python)
    } else {
        let executable = find_uv().ok_or(WorkerError::ExecutableNotFound)?;
        let mut command = Command::new(executable);
        command
            .args(["run", "--project"])
            .arg(&worker_project)
            .arg("python");
        command
    };
    if command
        .get_program()
        .to_string_lossy()
        .ends_with("python.exe")
        || command.get_program().to_string_lossy().ends_with("uv.exe")
        || command.get_program() == "uv"
    {
        command.args(["-m", "shiwei_ai.worker.main"]);
    }
    let working_directory = if worker_project.is_dir() {
        worker_project
    } else {
        env::current_exe()
            .ok()
            .and_then(|path| path.parent().map(Path::to_path_buf))
            .ok_or(WorkerError::ExecutableNotFound)?
    };
    let mut child = command
        .current_dir(working_directory)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    let stdin = child.stdin.take().ok_or(WorkerError::Exited)?;
    let stdout = child.stdout.take().ok_or(WorkerError::Exited)?;
    let stderr = child.stderr.take().ok_or(WorkerError::Exited)?;
    let (sender, responses) = mpsc::channel();

    thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(line) => {
                    if sender.send(line).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            eprintln!("[shiwei-ai] {line}");
        }
    });

    Ok(WorkerProcess {
        child,
        stdin,
        responses,
    })
}

fn parse_response<T: DeserializeOwned>(raw: &str, expected_id: &str) -> Result<T, WorkerError> {
    let response: RpcResponse = serde_json::from_str(raw)?;
    if response.jsonrpc != "2.0" || response.protocol_version != PROTOCOL_VERSION {
        return Err(WorkerError::Protocol("版本不兼容".to_string()));
    }
    if response.id.as_deref() != Some(expected_id) {
        return Err(WorkerError::Protocol("响应 ID 不匹配".to_string()));
    }
    if let Some(error) = response.error {
        return Err(WorkerError::Remote {
            code: error.code,
            message: error.message,
        });
    }
    let result = response
        .result
        .ok_or_else(|| WorkerError::Protocol("响应缺少 result".to_string()))?;
    Ok(serde_json::from_value(result)?)
}

fn worker_project_dir_from(manifest_dir: &Path) -> PathBuf {
    manifest_dir.join("../../../services/ai-worker")
}

fn find_uv() -> Option<PathBuf> {
    if let Ok(explicit) = env::var("SHIWEI_UV_PATH") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return Some(path);
        }
    }

    let mut candidates = Vec::new();
    if let Ok(local_app_data) = env::var("LOCALAPPDATA") {
        candidates.push(
            PathBuf::from(&local_app_data)
                .join("Microsoft")
                .join("WinGet")
                .join("Links")
                .join("uv.exe"),
        );
        candidates.push(
            PathBuf::from(local_app_data)
                .join("Microsoft")
                .join("WinGet")
                .join("Packages")
                .join("astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe")
                .join("uv.exe"),
        );
    }
    if let Ok(user_profile) = env::var("USERPROFILE") {
        candidates.push(
            PathBuf::from(user_profile)
                .join(".local")
                .join("bin")
                .join("uv.exe"),
        );
    }

    candidates
        .into_iter()
        .find(|candidate| candidate.is_file())
        .or_else(|| Some(PathBuf::from("uv")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;

    #[derive(Debug, Deserialize, PartialEq)]
    struct Ping {
        status: String,
    }

    #[test]
    fn resolves_worker_project_without_windows_specific_literals() {
        let root = Path::new("repo");
        let path = worker_project_dir_from(&root.join("apps/desktop/src-tauri"));
        assert!(path.ends_with("services/ai-worker"));
    }

    #[test]
    fn parses_a_versioned_response() {
        let raw =
            r#"{"jsonrpc":"2.0","protocol_version":"1.0","id":"abc","result":{"status":"pong"}}"#;
        let result: Ping = parse_response(raw, "abc").expect("response should parse");
        assert_eq!(
            result,
            Ping {
                status: "pong".into()
            }
        );
    }

    #[test]
    fn rejects_a_mismatched_request_id() {
        let raw =
            r#"{"jsonrpc":"2.0","protocol_version":"1.0","id":"other","result":{"status":"pong"}}"#;
        let result = parse_response::<Ping>(raw, "abc");
        assert!(matches!(result, Err(WorkerError::Protocol(_))));
    }

    #[test]
    fn preserves_provider_errors_instead_of_calling_them_protocol_failures() {
        let raw = r#"{"jsonrpc":"2.0","protocol_version":"1.0","id":"abc","error":{"code":"PROVIDER_ERROR","message":"API Key 无效或没有权限"}}"#;
        assert!(
            matches!(parse_response::<Ping>(raw, "abc"), Err(WorkerError::Remote { code, message }) if code == "PROVIDER_ERROR" && message.contains("API Key"))
        );
    }

    #[test]
    fn drops_late_stream_events_from_another_request() {
        assert!(!event_belongs_to(
            &json!({"event":"chat_token","requestId":"expired"}),
            "current"
        ));
        assert!(!event_belongs_to(&json!({"event":"chat_token"}), "current"));
        assert!(event_belongs_to(
            &json!({"event":"chat_token","requestId":"current"}),
            "current"
        ));
    }

    #[test]
    fn update_waits_for_idle_worker_and_blocks_restart_until_failure_recovery() {
        let client = WorkerClient::new();
        let active_request = client.process.lock().unwrap();
        assert!(client.prepare_update().is_err());
        assert!(!client.updating.load(Ordering::SeqCst));
        drop(active_request);
        client.prepare_update().unwrap();
        assert!(client.start().is_err());
        assert!(client.process.lock().unwrap().is_none());
        client.resume_after_update();
        assert!(!client.updating.load(Ordering::SeqCst));
    }

    #[test]
    fn starts_the_python_worker_and_completes_a_real_round_trip() {
        let client = WorkerClient::new();
        let result: Ping = client.ping().expect("Python Worker should answer ping");
        assert_eq!(result.status, "pong");
    }
}
