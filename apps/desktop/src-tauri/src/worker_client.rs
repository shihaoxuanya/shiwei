use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver};
use std::sync::{Arc, Mutex};
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
    stdin: Arc<Mutex<ChildStdin>>,
    responses: Receiver<String>,
}

pub struct WorkerClient {
    process: Mutex<Option<WorkerProcess>>,
    control_stdin: Mutex<Option<Arc<Mutex<ChildStdin>>>>,
    cancelled_chats: Mutex<VecDeque<String>>,
    timeout: Duration,
    updating: AtomicBool,
    relocating: AtomicBool,
    location_config: Mutex<Option<PathBuf>>,
}

struct RelocationGuard<'a>(&'a AtomicBool);
impl Drop for RelocationGuard<'_> {
    fn drop(&mut self) { self.0.store(false, Ordering::SeqCst); }
}

fn library_busy() -> WorkerError {
    WorkerError::Remote { code: "LIBRARY_BUSY".into(), message: "资料库正在使用，请等待导入、保存或回答完成后再更改位置。".into() }
}

impl WorkerClient {
    pub fn new() -> Self {
        Self {
            process: Mutex::new(None),
            control_stdin: Mutex::new(None),
            cancelled_chats: Mutex::new(VecDeque::new()),
            timeout: DEFAULT_TIMEOUT,
            updating: AtomicBool::new(false),
            relocating: AtomicBool::new(false),
            location_config: Mutex::new(None),
        }
    }

    /// Inject an external pointer file into each child, including crash recovery.
    /// Never mutate the parent process environment or store this inside the library.
    pub fn set_location_config(&self, path: PathBuf) -> Result<(), WorkerError> {
        if !path.is_absolute() { return Err(WorkerError::Protocol("资料库配置路径必须是绝对路径".into())); }
        let process = self.process.lock().map_err(|_| WorkerError::Exited)?;
        if process.is_some() { return Err(library_busy()); }
        *self.location_config.lock().map_err(|_| WorkerError::Exited)? = Some(path);
        Ok(())
    }

    pub fn is_relocating(&self) -> bool { self.relocating.load(Ordering::SeqCst) }

    pub fn relocate_library<T, F>(&self, params: Value, mut on_event: F) -> Result<T, WorkerError>
    where T: DeserializeOwned, F: FnMut(&Value) {
        self.relocating.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .map_err(|_| library_busy())?;
        let _guard = RelocationGuard(&self.relocating);
        self.request_internal("relocate_library", params, Some(&mut on_event))
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
        let running = spawn_worker(self.location_config.lock().map_err(|_| WorkerError::Exited)?.as_deref())?;
        *self.control_stdin.lock().map_err(|_| WorkerError::Exited)? = Some(running.stdin.clone());
        *process = Some(running);
        Ok(())
    }

    pub fn ping<T: DeserializeOwned>(&self) -> Result<T, WorkerError> {
        self.request("ping", json!({}))
    }

    pub fn cancel_chat(&self, client_request_id: &str) -> Result<(), WorkerError> {
        // Only a cancellation flag travels beside the serialized RPC. No database
        // work runs here, and stopping a reply never kills a note save/import.
        let mut cancelled = self.cancelled_chats.lock().map_err(|_| WorkerError::Exited)?;
        if !cancelled.iter().any(|id| id == client_request_id) {
            cancelled.push_back(client_request_id.to_owned());
            if cancelled.len() > 128 { cancelled.pop_front(); }
        }
        let stdin = self.control_stdin.lock().map_err(|_| WorkerError::Exited)?.clone();
        let Some(stdin) = stdin else { return Ok(()); };
        let mut writer = stdin.lock().map_err(|_| WorkerError::Exited)?;
        let request = RpcRequest {
            jsonrpc: "2.0", protocol_version: PROTOCOL_VERSION,
            id: Uuid::new_v4().to_string(), method: "cancel_chat",
            params: json!({"clientRequestId": client_request_id}),
        };
        serde_json::to_writer(&mut *writer, &request)?;
        writer.write_all(b"\n")?;
        writer.flush()?;
        Ok(())
    }

    pub fn prepare_update(&self) -> Result<(), WorkerError> {
        // Never terminate an active import, save, migration or conversation.
        if self.is_relocating() { return Err(library_busy()); }
        let mut process = self.process.try_lock().map_err(|_| WorkerError::Timeout)?;
        if self.is_relocating() { return Err(library_busy()); }
        self.updating.store(true, Ordering::SeqCst);
        if let Some(mut running) = process.take() {
            let _ = running.child.kill();
            let _ = running.child.wait();
        }
        *self.control_stdin.lock().map_err(|_| WorkerError::Exited)? = None;
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
        if method == "relocate_library" { return Err(library_busy()); }
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
        if method == "relocate_library" { return Err(library_busy()); }
        self.request_internal(method, params, Some(&mut on_event))
    }

    fn request_internal<T: DeserializeOwned>(
        &self,
        method: &str,
        params: Value,
        mut on_event: Option<&mut dyn FnMut(&Value)>,
    ) -> Result<T, WorkerError> {
        let relocating = method == "relocate_library";
        if self.is_relocating() && !relocating { return Err(library_busy()); }
        let cancellable_chat = if method == "chat" {
            params.get("clientRequestId").and_then(Value::as_str).map(str::to_owned)
        } else { None };
        let cancelled_error = || WorkerError::Remote { code: "CHAT_CANCELLED".into(), message: "已停止本次回答，问题仍保留，可重新发送。".into() };
        if let Some(id) = &cancellable_chat {
            let mut cancelled = self.cancelled_chats.lock().map_err(|_| WorkerError::Exited)?;
            if cancelled.contains(id) {
                cancelled.retain(|item| item != id);
                return Err(cancelled_error());
            }
        }
        if !relocating { self.start()?; }
        let mut process_guard = if relocating {
            // Do not queue a location switch behind active work with a stale UI.
            self.process.try_lock().map_err(|_| library_busy())?
        } else { self.process.lock().map_err(|_| WorkerError::Exited)? };
        if self.is_relocating() && !relocating { return Err(library_busy()); }
        if self.updating.load(Ordering::SeqCst) {
            return Err(WorkerError::Exited);
        }
        if relocating && (process_guard.is_none() || process_guard.as_mut().is_some_and(|p| p.child.try_wait().ok().flatten().is_some())) {
            let running = spawn_worker(self.location_config.lock().map_err(|_| WorkerError::Exited)?.as_deref())?;
            *self.control_stdin.lock().map_err(|_| WorkerError::Exited)? = Some(running.stdin.clone());
            *process_guard = Some(running);
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
        {
            // A stop may arrive while this chat waits behind an import/save.
            // Serialize the cancellation check and pipe write with the control
            // lane, so a cancel can never overtake registration in Python.
            let mut cancelled = self.cancelled_chats.lock().map_err(|_| WorkerError::Exited)?;
            if let Some(id) = &cancellable_chat {
                if cancelled.contains(id) {
                    cancelled.retain(|item| item != id);
                    return Err(cancelled_error());
                }
            }
            let mut writer = process.stdin.lock().map_err(|_| WorkerError::Exited)?;
            serde_json::to_writer(&mut *writer, &request)?;
            writer.write_all(b"\n")?;
            writer.flush()?;
        }

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
            if !relocating && remaining.is_zero() {
                if let Some(id) = &cancellable_chat { let _ = self.cancel_chat(id); }
                return Err(WorkerError::Timeout);
            }
            // A timeout must not tell the UI migration failed while Python is
            // still verifying or about to atomically commit the new location.
            // Hold the exclusive lane until the final response or process exit.
            let raw_response = if relocating {
                process.responses.recv().map_err(|_| WorkerError::Exited)?
            } else {
                process
                    .responses
                    .recv_timeout(remaining)
                    .map_err(|error| match error {
                        mpsc::RecvTimeoutError::Timeout => {
                            if let Some(id) = &cancellable_chat { let _ = self.cancel_chat(id); }
                            WorkerError::Timeout
                        },
                        mpsc::RecvTimeoutError::Disconnected => WorkerError::Exited,
                    })?
            };
            let envelope: Value = match serde_json::from_str(&raw_response) {
                Ok(value) => value,
                // A stray stdout diagnostic is not a terminal migration result.
                // Never log the content or release the library lock early.
                Err(_) if relocating => continue,
                Err(error) => return Err(WorkerError::Json(error)),
            };
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
            if let Some(id) = &cancellable_chat {
                self.cancelled_chats.lock().map_err(|_| WorkerError::Exited)?.retain(|item| item != id);
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

fn spawn_worker(location_config: Option<&Path>) -> Result<WorkerProcess, WorkerError> {
    let worker_project = worker_project_dir_from(Path::new(env!("CARGO_MANIFEST_DIR")));
    let executable = env::current_exe()?;
    let mut command = worker_command(
        &worker_project,
        &executable,
        cfg!(debug_assertions),
        cfg!(windows),
    )?;
    apply_location_config(&mut command, location_config);
    configure_worker_process(&mut command);
    let mut child = command.spawn()?;

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
        stdin: Arc::new(Mutex::new(stdin)),
        responses,
    })
}

fn apply_location_config(command: &mut Command, location_config: Option<&Path>) {
    if let Some(path) = location_config { command.env("SHIWEI_LOCATION_CONFIG", path); }
}

fn development_python_path(project: &Path, windows: bool) -> PathBuf {
    project.join(".venv").join(if windows {
        "Scripts/python.exe"
    } else {
        "bin/python"
    })
}

fn sidecar_path(executable: &Path, windows: bool) -> Result<PathBuf, WorkerError> {
    Ok(executable
        .parent()
        .ok_or(WorkerError::ExecutableNotFound)?
        .join(if windows {
            "shiwei-ai-worker.exe"
        } else {
            "shiwei-ai-worker"
        }))
}

fn worker_command(
    project: &Path,
    executable: &Path,
    development: bool,
    windows: bool,
) -> Result<Command, WorkerError> {
    let python = development_python_path(project, windows);
    let sidecar = sidecar_path(executable, windows)?;
    if development && python.is_file() {
        let mut command = Command::new(python);
        command
            .args(["-m", "shiwei_ai.worker.main"])
            .current_dir(project);
        return Ok(command);
    }
    if sidecar.is_file() {
        let mut command = Command::new(&sidecar);
        command.current_dir(sidecar.parent().ok_or(WorkerError::ExecutableNotFound)?);
        return Ok(command);
    }
    // Never download/run a development environment on a tester's computer.
    if !development || !project.is_dir() {
        return Err(WorkerError::ExecutableNotFound);
    }
    let mut command = Command::new(find_uv().ok_or(WorkerError::ExecutableNotFound)?);
    command
        .args(["run", "--project"])
        .arg(project)
        .args(["python", "-m", "shiwei_ai.worker.main"])
        .current_dir(project);
    Ok(command)
}

fn configure_worker_process(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // The worker still needs stdin/stdout for JSONL. A windowless PyInstaller
        // build can remove those streams; suppress the console at process creation
        // instead, including the development Python and uv fallback paths.
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
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
    #[cfg(unix)]
    if let Ok(user_home) = env::var("HOME") {
        candidates.push(PathBuf::from(user_home).join(".local/bin/uv"));
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
    fn injects_location_config_only_into_worker_environment() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("library-location.json");
        let mut command = Command::new("unused-test-worker");
        let previous = env::var_os("SHIWEI_LOCATION_CONFIG");
        apply_location_config(&mut command, Some(&path));
        assert_eq!(command.get_envs().find(|(key, _)| *key == "SHIWEI_LOCATION_CONFIG").unwrap().1, Some(path.as_os_str()));
        assert_eq!(env::var_os("SHIWEI_LOCATION_CONFIG"), previous);
        let client = WorkerClient::new();
        assert!(client.set_location_config(PathBuf::from("relative/location.json")).is_err());
        client.set_location_config(path.clone()).unwrap();
        assert_eq!(*client.location_config.lock().unwrap(), Some(path));
    }

    #[test]
    fn relocation_refuses_busy_worker_and_always_releases_ui_guard() {
        let client = WorkerClient::new();
        let _active = client.process.lock().unwrap();
        let result = client.relocate_library::<Value, _>(json!({}), |_| {});
        assert!(matches!(result, Err(WorkerError::Remote {code, ..}) if code == "LIBRARY_BUSY"));
        assert!(!client.is_relocating());
    }

    #[test]
    fn relocation_blocks_updates_and_ordinary_requests_without_waiting() {
        let client = WorkerClient::new();
        client.relocating.store(true, Ordering::SeqCst);
        assert!(client.prepare_update().is_err());
        assert!(client.request::<Value>("update_note", json!({})).is_err());
        assert!(client.request_with_events::<Value, _>("import_paths", json!({}), |_|{}).is_err());
        assert!(client.relocate_library::<Value, _>(json!({}), |_|{}).is_err());
        assert!(client.is_relocating());
        drop(RelocationGuard(&client.relocating));
        assert!(!client.is_relocating());
        assert!(!client.updating.load(Ordering::SeqCst));
    }

    #[test]
    fn migration_waits_for_final_response_past_the_normal_rpc_timeout() {
        let python = development_python_path(&worker_project_dir_from(Path::new(env!("CARGO_MANIFEST_DIR"))), cfg!(windows));
        let mut command = Command::new(python);
        command.args(["-c", "import json,sys,time; r=json.loads(sys.stdin.readline()); print('stray diagnostic',flush=True); time.sleep(0.1); print(json.dumps({'jsonrpc':'2.0','protocol_version':'1.0','id':r['id'],'result':{'verified':True}}),flush=True)"]);
        configure_worker_process(&mut command);
        let mut child = command.spawn().unwrap();
        let stdin = Arc::new(Mutex::new(child.stdin.take().unwrap()));
        let stdout = child.stdout.take().unwrap();
        let (sender, responses) = mpsc::channel();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) { let _ = sender.send(line); }
        });
        let mut client = WorkerClient::new();
        client.timeout = Duration::from_millis(1);
        *client.process.lock().unwrap() = Some(WorkerProcess { child, stdin, responses });
        let result: Value = client.relocate_library(json!({}), |_| {}).unwrap();
        assert_eq!(result["verified"], true);
        assert!(!client.is_relocating());
    }

    #[test]
    fn migration_cannot_bypass_its_guard_through_generic_rpc_methods() {
        let client = WorkerClient::new();
        assert!(client.request::<Value>("relocate_library", json!({})).is_err());
        assert!(client.request_with_events::<Value, _>("relocate_library", json!({}), |_|{}).is_err());
        assert!(!client.is_relocating());
        assert!(client.process.lock().unwrap().is_none());
    }

    #[test]
    fn resolves_mac_and_windows_worker_layouts() {
        let project = Path::new("repo/services/ai-worker");
        assert!(development_python_path(project, true).ends_with(".venv/Scripts/python.exe"));
        assert!(development_python_path(project, false).ends_with(".venv/bin/python"));
        assert_eq!(
            sidecar_path(Path::new("拾微.app/Contents/MacOS/shiwei-desktop"), false).unwrap(),
            PathBuf::from("拾微.app/Contents/MacOS/shiwei-ai-worker")
        );
        assert!(
            sidecar_path(Path::new("installed/shiwei-desktop.exe"), true)
                .unwrap()
                .ends_with("shiwei-ai-worker.exe")
        );
    }

    #[test]
    fn release_never_falls_back_to_development_python_or_uv() {
        let temp = tempfile::tempdir().unwrap();
        for windows in [true, false] {
            let python = development_python_path(temp.path(), windows);
            std::fs::create_dir_all(python.parent().unwrap()).unwrap();
            std::fs::write(&python, b"fixture").unwrap();
            assert!(matches!(
                worker_command(temp.path(), &temp.path().join("app"), false, windows),
                Err(WorkerError::ExecutableNotFound)
            ));
        }
    }

    #[test]
    fn packaged_worker_is_launched_without_python_arguments() {
        let temp = tempfile::tempdir().unwrap();
        for windows in [true, false] {
            let app = temp.path().join("拾微.app/Contents/MacOS/app");
            let sidecar = sidecar_path(&app, windows).unwrap();
            std::fs::create_dir_all(sidecar.parent().unwrap()).unwrap();
            std::fs::write(&sidecar, b"fixture").unwrap();
            let command = worker_command(temp.path(), &app, false, windows).unwrap();
            assert_eq!(command.get_program(), sidecar.as_os_str());
            assert_eq!(command.get_args().count(), 0);
            assert_eq!(command.get_current_dir(), sidecar.parent());
        }
    }

    #[test]
    fn development_python_keeps_explicit_module_arguments_on_both_platforms() {
        let temp = tempfile::tempdir().unwrap();
        for windows in [true, false] {
            let python = development_python_path(temp.path(), windows);
            std::fs::create_dir_all(python.parent().unwrap()).unwrap();
            std::fs::write(&python, b"fixture").unwrap();
            let command =
                worker_command(temp.path(), &temp.path().join("app"), true, windows).unwrap();
            assert_eq!(command.get_program(), python.as_os_str());
            assert_eq!(
                command.get_args().collect::<Vec<_>>(),
                ["-m", "shiwei_ai.worker.main"]
            );
        }
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

    #[test]
    fn cancel_control_does_not_wait_for_serialized_rpc_lock_or_kill_worker() {
        let client = WorkerClient::new();
        let _: Ping = client.ping().unwrap();
        let active = client.process.lock().unwrap();
        client.cancel_chat("finished-request").unwrap();
        drop(active);
        let result: Ping = client.ping().unwrap();
        assert_eq!(result.status, "pong");
    }

    #[test]
    fn cancellation_before_chat_starts_is_not_lost() {
        let client = WorkerClient::new();
        client.cancel_chat("queued").unwrap();
        let result = client.request::<Value>("chat", json!({"clientRequestId":"queued", "query":"你好"}));
        assert!(matches!(result, Err(WorkerError::Remote { code, .. }) if code == "CHAT_CANCELLED"));
        assert!(client.process.lock().unwrap().is_none());
        assert!(client.cancelled_chats.lock().unwrap().is_empty());
    }

    #[cfg(windows)]
    #[test]
    fn background_worker_child_probe() {
        if env::var("SHIWEI_TEST_CONSOLE_PROBE").as_deref() != Ok("1") {
            return;
        }
        #[link(name = "kernel32")]
        extern "system" {
            fn GetConsoleWindow() -> *mut std::ffi::c_void;
        }
        assert!(
            unsafe { GetConsoleWindow() }.is_null(),
            "Worker must not own or inherit a console"
        );
        let mut line = String::new();
        std::io::stdin().read_line(&mut line).unwrap();
        assert_eq!(line.trim(), "pipe-request");
        println!("pipe-response");
        eprintln!("pipe-diagnostic");
    }

    #[cfg(windows)]
    #[test]
    fn background_worker_has_no_console_and_keeps_all_protocol_pipes() {
        let mut command = Command::new(env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "worker_client::tests::background_worker_child_probe",
                "--nocapture",
            ])
            .env("SHIWEI_TEST_CONSOLE_PROBE", "1");
        configure_worker_process(&mut command);
        let mut child = command.spawn().unwrap();
        child
            .stdin
            .take()
            .unwrap()
            .write_all(b"pipe-request\n")
            .unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stdout)
        );
        assert!(String::from_utf8_lossy(&output.stdout).contains("pipe-response"));
        assert!(String::from_utf8_lossy(&output.stderr).contains("pipe-diagnostic"));
    }
}
