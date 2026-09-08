//! Optional content-free control plane. Production defaults on; saved opt-outs always win.
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Mutex, OnceLock},
    time::Duration,
};
use tokio::sync::{watch, Semaphore};
use uuid::Uuid;

static GLOBAL: OnceLock<Arc<AnalyticsService>> = OnceLock::new();
const EVENTS: &[&str] = &[
    "app_installed",
    "app_opened",
    "app_version",
    "import_started",
    "import_completed",
    "import_failed",
    "note_created",
    "question_asked",
    "retrieval_succeeded",
    "retrieval_abstained",
    "citation_clicked",
    "update_available",
    "update_started",
    "update_completed",
    "update_failed",
    "app_error",
];
const ERROR_TYPES: &[&str] = &[
    "frontend_error",
    "unhandled_rejection",
    "worker_exited",
    "worker_timeout",
    "worker_error",
    "rust_panic",
    "update_network",
    "update_manifest",
    "update_signature",
    "update_download",
    "update_install",
    "update_worker_busy",
    "update_unknown",
    "import_error",
    "chat_error",
];

/// Positive allowlist, including values. Arbitrary strings/nesting never cross this boundary.
pub fn sanitize_telemetry_payload(event: &str, input: &Value) -> Option<Value> {
    if !EVENTS.contains(&event) {
        return None;
    }
    let mut safe = Map::new();
    if event.starts_with("import_") {
        enum_field(
            input,
            &mut safe,
            "file_count_bucket",
            &["1", "2-10", "11-100", "100+"],
        );
        for key in ["success_count", "failure_count", "duration_ms"] {
            number_field(input, &mut safe, key);
        }
    }
    if event == "question_asked" {
        enum_field(input, &mut safe, "conversation_mode", &["knowledge_first"]);
    }
    if event == "app_error" || event == "update_failed" {
        enum_field(input, &mut safe, "error_type", ERROR_TYPES);
        // Stack frames contain only our module identifiers and bounded numeric positions.
        // No exception messages, function names, source code, absolute paths or URLs.
        let frames: Vec<Value> = input
            .get("frames")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .take(12)
            .filter_map(|frame| {
                let module = frame.get("module")?.as_str()?;
                if !["frontend", "worker_client", "desktop", "updater"].contains(&module) {
                    return None;
                }
                let mut output = json!({"module": module});
                for key in ["line", "column"] {
                    if let Some(n) = frame
                        .get(key)
                        .and_then(Value::as_u64)
                        .filter(|n| *n <= 10_000_000)
                    {
                        output[key] = json!(n);
                    }
                }
                Some(output)
            })
            .collect();
        if !frames.is_empty() {
            safe.insert("frames".into(), json!(frames));
        }
    }
    Some(Value::Object(safe))
}
fn enum_field(input: &Value, output: &mut Map<String, Value>, key: &str, allowed: &[&str]) {
    if let Some(value) = input
        .get(key)
        .and_then(Value::as_str)
        .filter(|v| allowed.contains(v))
    {
        output.insert(key.into(), json!(value));
    }
}
fn number_field(input: &Value, output: &mut Map<String, Value>, key: &str) {
    if let Some(value) = input.get(key).and_then(Value::as_u64).filter(|n| {
        *n <= if key == "duration_ms" {
            86_400_000
        } else {
            100_000
        }
    }) {
        output.insert(key.into(), json!(value));
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConsentChoice {
    Undecided,
    Enabled,
    Disabled,
}

#[derive(Serialize, Deserialize)]
struct Installation {
    installation_id: String,
    enabled: bool,
    // Keep legacy preference compatibility. This marker records the effective choice;
    // enabled also includes the current production default and is not proof of opt-in.
    #[serde(default)]
    consent_choice: Option<ConsentChoice>,
    #[serde(default)]
    install_reported: bool,
    #[serde(default)]
    first_recall_seen: bool,
    #[serde(default)]
    pending_update: Option<String>,
}
impl Default for Installation {
    fn default() -> Self {
        Self {
            installation_id: Uuid::new_v4().to_string(),
            enabled: false,
            consent_choice: Some(ConsentChoice::Disabled),
            install_reported: false,
            first_recall_seen: false,
            pending_update: None,
        }
    }
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AnalyticsStatus {
    pub enabled: bool,
    pub configured: bool,
    pub choice: ConsentChoice,
    pub needs_choice: bool,
}

pub struct AnalyticsService {
    state: Mutex<Installation>,
    path: Option<PathBuf>,
    provider: MetadataAnalyticsProvider,
    transport_allowed: bool,
    consent: watch::Sender<(bool, u64)>,
    slots: Arc<Semaphore>,
    #[cfg(test)]
    observer: Option<Arc<dyn Fn(&Value) + Send + Sync>>,
}
struct MetadataAnalyticsProvider {
    host: Option<reqwest::Url>,
    key: String,
}
impl MetadataAnalyticsProvider {
    fn new(host: &str, key: &str) -> Self {
        let host = reqwest::Url::parse(host).ok().filter(|u| {
            u.scheme() == "https"
                && u.username().is_empty()
                && u.password().is_none()
                && u.query().is_none()
                && u.fragment().is_none()
        });
        Self {
            host,
            key: key.to_owned(),
        }
    }
    fn configured(&self) -> bool {
        self.host.is_some() && (self.key.is_empty() || self.key.starts_with("phc_"))
    }
}
impl AnalyticsService {
    pub fn new(path: PathBuf, host: &str, key: &str) -> Self {
        // Development builds never send production statistics, even with a saved choice.
        Self::new_with_transport(path, host, key, !cfg!(debug_assertions))
    }
    fn new_with_transport(path: PathBuf, host: &str, key: &str, transport_allowed: bool) -> Self {
        // Only a genuinely missing file is a new installation. Unreadable/malformed
        // preferences must not become default-on, including on the following restart.
        let mut state = match fs::read(&path) {
            Ok(bytes) => serde_json::from_slice::<Installation>(&bytes).ok().filter(|s| {
                Uuid::parse_str(&s.installation_id).is_ok_and(|id| id.get_version_num() == 4)
            }).unwrap_or_default(),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Installation {
                enabled: true, consent_choice: Some(ConsentChoice::Enabled), ..Installation::default()
            },
            Err(_) => Installation::default(),
        };
        let mut choice = state.consent_choice.unwrap_or(if state.enabled {
            ConsentChoice::Enabled
        } else {
            // Preserve every legacy false, including old off defaults.
            ConsentChoice::Disabled
        });
        // A stored off value wins if a partially migrated setting is inconsistent.
        if !state.enabled && choice == ConsentChoice::Enabled {
            choice = ConsentChoice::Disabled;
        }
        // The latest product decision removes the undecided step, but not opt-outs.
        if choice == ConsentChoice::Undecided {
            choice = ConsentChoice::Enabled;
        }
        state.consent_choice = Some(choice);
        state.enabled = choice == ConsentChoice::Enabled;
        let (consent, _) = watch::channel((state.enabled, 0));
        let result = Self {
            state: Mutex::new(state),
            path: Some(path),
            provider: MetadataAnalyticsProvider::new(host, key),
            transport_allowed,
            consent,
            slots: Arc::new(Semaphore::new(8)),
            #[cfg(test)]
            observer: None,
        };
        if result.save().is_err() {
            if let Ok(mut state) = result.state.lock() {
                state.enabled = false;
                state.consent_choice = Some(ConsentChoice::Disabled);
            }
            result.consent.send_replace((false, 1));
        }
        result
    }
    fn save_locked(&self, state: &Installation) -> Result<(), String> {
        let action = || -> std::io::Result<()> {
            let path = self.path.as_ref().ok_or(std::io::ErrorKind::NotFound)?;
            fs::create_dir_all(path.parent().ok_or(std::io::ErrorKind::InvalidInput)?)?;
            let tmp = path.with_extension("json.tmp");
            fs::write(&tmp, serde_json::to_vec(state)?)?;
            fs::rename(tmp, path)
        };
        action().map_err(|_| "无法保存隐私设置；基础统计保持关闭".into())
    }
    fn save(&self) -> Result<(), String> {
        let state = self.state.lock().map_err(|_| "隐私状态不可用")?;
        self.save_locked(&state)
    }
    pub fn status(&self) -> AnalyticsStatus {
        let (enabled, choice) = self
            .state
            .lock()
            .map(|s| {
                (
                    s.enabled,
                    s.consent_choice.unwrap_or(ConsentChoice::Undecided),
                )
            })
            .unwrap_or((false, ConsentChoice::Undecided));
        AnalyticsStatus {
            enabled,
            configured: self.provider.configured(),
            choice,
            needs_choice: false,
        }
    }
    // Functional update cohort identifier, not telemetry consent. Native only.
    // Require successful persistence so a failed data directory cannot reshuffle cohorts.
    pub fn update_installation_id(&self) -> Result<String, String> {
        let state = self.state.lock().map_err(|_| "update_identity")?;
        self.save_locked(&state).map_err(|_| "update_identity")?;
        Ok(state.installation_id.clone())
    }
    pub fn set_enabled(&self, enabled: bool) -> Result<(), String> {
        let mut state = self.state.lock().map_err(|_| "隐私状态不可用")?;
        // Abort outstanding HTTP futures and invalidate all prior queued events, even on quick re-enable.
        let epoch = self.consent.borrow().1 + 1;
        self.consent.send_replace((false, epoch));
        state.enabled = enabled;
        state.consent_choice = Some(if enabled {
            ConsentChoice::Enabled
        } else {
            ConsentChoice::Disabled
        });
        if let Err(e) = self.save_locked(&state) {
            state.enabled = false;
            state.consent_choice = Some(ConsentChoice::Disabled);
            return Err(e);
        }
        self.consent.send_replace((enabled, epoch));
        Ok(())
    }
    pub fn mark_update(&self, version: Option<String>) -> Result<(), String> {
        let mut state = self.state.lock().map_err(|_| "更新状态不可用")?;
        state.pending_update = version;
        self.save_locked(&state)
    }
    pub fn opened(self: &Arc<Self>) {
        let (installed, completed) = {
            let Ok(mut state) = self.state.lock() else {
                return;
            };
            let installed = state.enabled
                && self.transport_allowed
                && !state.install_reported
                && self.provider.configured();
            if installed {
                state.install_reported = true;
            }
            let completed = state.pending_update.as_deref() == Some(env!("CARGO_PKG_VERSION"));
            if completed {
                state.pending_update = None;
            }
            if self.save_locked(&state).is_err() {
                return;
            }
            (installed, completed)
        };
        if installed {
            self.track("app_installed", json!({}));
        }
        self.track("app_opened", json!({}));
        self.track("app_version", json!({}));
        if completed {
            self.track("update_completed", json!({}));
        }
    }
    pub fn track(self: &Arc<Self>, event: &str, input: Value) {
        let Some(properties) = sanitize_telemetry_payload(event, &input) else {
            return;
        };
        let first = if event == "retrieval_succeeded" {
            let Ok(mut s) = self.state.lock() else {
                return;
            };
            if s.first_recall_seen {
                false
            } else {
                s.first_recall_seen = true;
                self.save_locked(&s).is_ok()
            }
        } else {
            false
        };
        self.enqueue(event, properties);
        if first {
            self.enqueue("first_successful_recall", json!({}));
        }
    }
    fn enqueue(self: &Arc<Self>, event: &str, properties: Value) {
        // Capture consent BEFORE building the payload: disable/re-enable must not give an
        // old queued event a new consent generation.
        let mut consent = self.consent.subscribe();
        let generation = *consent.borrow();
        if !generation.0 || !self.transport_allowed {
            return;
        }
        let Ok(state) = self.state.lock() else {
            return;
        };
        if !state.enabled
            || state.consent_choice != Some(ConsentChoice::Enabled)
            || !self.provider.configured()
        {
            return;
        }
        let Ok(permit) = self.slots.clone().try_acquire_owned() else {
            return;
        };
        let timestamp = chrono::Utc::now().to_rfc3339();
        let body = if self.provider.key.is_empty() {
            // First-party transport: no public project token, cookies, user agent SDK,
            // IP properties, or arbitrary strings. UUIDs are native-generated only.
            json!({ "schema_version": 1, "event_id": Uuid::new_v4().to_string(),
                "installation_id": state.installation_id, "event": event,
                "app_version": env!("CARGO_PKG_VERSION"), "os": std::env::consts::OS,
                "architecture": std::env::consts::ARCH, "timestamp": timestamp,
                "properties": properties })
        } else {
            // Retain the replaceable PostHog transport for existing configured builds.
            let mut payload = properties;
            payload["installation_id"] = json!(state.installation_id);
            payload["app_version"] = json!(env!("CARGO_PKG_VERSION"));
            payload["os"] = json!(std::env::consts::OS);
            payload["architecture"] = json!(std::env::consts::ARCH);
            payload["$process_person_profile"] = json!(false);
            payload["$geoip_disable"] = json!(true);
            payload["$ip"] = json!("0.0.0.0");
            payload["event_timestamp"] = json!(timestamp);
            json!({ "api_key": self.provider.key, "event": event, "distinct_id": state.installation_id, "properties": payload, "timestamp": timestamp })
        };
        drop(state);
        if *consent.borrow() != generation {
            return;
        }
        #[cfg(test)]
        if let Some(observer) = &self.observer {
            observer(&body);
            return;
        }
        let mut url = self.provider.host.clone().expect("validated host");
        url.set_path(if self.provider.key.is_empty() {
            "/api/v1/telemetry/events"
        } else {
            "/i/v0/e/"
        });
        if cfg!(debug_assertions) && std::env::var("SHIWEI_ANALYTICS_DEBUG").as_deref() == Ok("1") {
            // Only sanitized event metadata; excludes transport key and installation identity.
            eprintln!("[analytics] {event}");
        }
        tauri::async_runtime::spawn(async move {
            let _permit = permit;
            if *consent.borrow() != generation {
                return;
            }
            let Ok(client) = reqwest::Client::builder()
                .https_only(true)
                .redirect(reqwest::redirect::Policy::none())
                .timeout(Duration::from_secs(4))
                .build()
            else {
                return;
            };
            tokio::select! { biased;
                _ = consent.changed() => {},
                _ = client.post(url).json(&body).send() => {},
            }
            // No disk queue, retries, response logging, or exception recursion.
        });
    }
}
pub fn initialize(path: Option<PathBuf>) -> Arc<AnalyticsService> {
    // Process-local kill switch for isolated packaged-app QA and managed deployments.
    // Do not read or overwrite the real installation preference in this mode.
    if std::env::var("SHIWEI_TELEMETRY_DISABLED").as_deref() == Ok("1") {
        let service = Arc::new(AnalyticsService::disabled());
        let _ = GLOBAL.set(service.clone());
        return service;
    }
    let service = Arc::new(match path {
        Some(path) => AnalyticsService::new(
            path,
            option_env!("SHIWEI_ANALYTICS_HOST").unwrap_or(""),
            option_env!("SHIWEI_ANALYTICS_KEY").unwrap_or(""),
        ),
        None => AnalyticsService::disabled(),
    });
    let _ = GLOBAL.set(service.clone());
    service.opened();
    service
}
pub fn report_error(kind: &str, module: &str) {
    if let Some(service) = GLOBAL.get() {
        // A panic may originate while the telemetry mutex is held. Never deadlock
        // the app's panic handler trying to report a best-effort event.
        if service.state.try_lock().is_err() {
            return;
        }
        service.track(
            "app_error",
            json!({"error_type": kind, "frames": [{"module": module}]}),
        );
    }
}

impl AnalyticsService {
    fn disabled() -> Self {
        Self {
            state: Mutex::new(Installation::default()),
            path: None,
            provider: MetadataAnalyticsProvider::new("", ""),
            transport_allowed: false,
            consent: watch::channel((false, 0)).0,
            slots: Arc::new(Semaphore::new(8)),
            #[cfg(test)]
            observer: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn missing_app_data_cannot_enable_or_use_shared_temporary_identity() {
        let service = Arc::new(AnalyticsService::disabled());
        assert!(service.path.is_none());
        assert!(service.set_enabled(true).is_err());
        assert!(!service.status().enabled);
        assert!(!service.status().configured);
        service.track("app_opened", json!({}));
        assert_eq!(service.slots.available_permits(), 8);
    }
    #[test]
    fn real_event_envelope_is_content_free_and_activation_is_once_across_restarts() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("installation.json");
        let delivered = Arc::new(Mutex::new(Vec::<Value>::new()));
        for _ in 0..2 {
            let mut service = AnalyticsService::new_with_transport(
                path.clone(),
                "https://example.invalid",
                "phc_test_public_project",
                true,
            );
            let captured = delivered.clone();
            service.observer = Some(Arc::new(move |body| {
                captured.lock().unwrap().push(body.clone())
            }));
            service.set_enabled(true).unwrap();
            let service = Arc::new(service);
            service.opened();
            service.track("question_asked", json!({"query_text":"private meeting question", "file_path":"C:/Users/private", "api_key":"secret-provider-key", "frames": [{"content":"private"}]}));
            service.track("retrieval_succeeded", json!({"note_content":"private"}));
            service.track("retrieval_succeeded", json!({}));
            service.set_enabled(false).unwrap();
            let before = delivered.lock().unwrap().len();
            service.track("note_created", json!({}));
            assert_eq!(delivered.lock().unwrap().len(), before);
        }
        let events = delivered.lock().unwrap();
        assert_eq!(
            events
                .iter()
                .filter(|v| v["event"] == "first_successful_recall")
                .count(),
            1
        );
        assert_eq!(
            events
                .iter()
                .filter(|v| v["event"] == "app_installed")
                .count(),
            1
        );
        assert_eq!(
            events
                .iter()
                .filter(|v| v["event"] == "retrieval_succeeded")
                .count(),
            4
        );
        let serialized = serde_json::to_string(&*events).unwrap();
        for forbidden in [
            "query_text",
            "private",
            "file_path",
            "note_content",
            "secret-provider-key",
        ] {
            assert!(!serialized.contains(forbidden));
        }
        assert!(events
            .iter()
            .all(|v| v["properties"]["$geoip_disable"] == true
                && v["properties"]["$process_person_profile"] == false));
        assert!(events
            .iter()
            .all(|v| v["distinct_id"] == events[0]["distinct_id"]));
    }
    #[test]
    fn disable_invalidates_old_consent_generation_even_after_reenable() {
        let directory = tempfile::tempdir().unwrap();
        let service = AnalyticsService::new(directory.path().join("installation.json"), "", "");
        service.set_enabled(true).unwrap();
        let first = *service.consent.borrow();
        service.set_enabled(false).unwrap();
        service.set_enabled(true).unwrap();
        assert_ne!(*service.consent.borrow(), first);
    }
    #[test]
    fn installation_persists_and_defaults_on() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("installation.json");
        let a = AnalyticsService::new(path.clone(), "", "");
        let id = a.state.lock().unwrap().installation_id.clone();
        assert_eq!(Uuid::parse_str(&id).unwrap().get_version_num(), 4);
        assert!(a.status().enabled);
        let b = AnalyticsService::new(path, "", "");
        assert_eq!(b.state.lock().unwrap().installation_id, id);
    }
    #[test]
    fn release_defaults_on_and_preserves_disabled_and_corrupt_preferences() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("installation.json");
        let service =
            AnalyticsService::new_with_transport(path.clone(), "https://example.invalid", "", true);
        assert!(service.status().enabled && service.status().configured);
        assert_eq!(service.status().choice, ConsentChoice::Enabled);
        assert!(!service.status().needs_choice);
        service.set_enabled(false).unwrap();
        let old_id = service.update_installation_id().unwrap();
        let upgraded =
            AnalyticsService::new_with_transport(path.clone(), "https://example.invalid", "", true);
        assert!(!upgraded.status().enabled);
        assert_eq!(upgraded.status().choice, ConsentChoice::Disabled);
        assert!(!upgraded.status().needs_choice);
        assert_eq!(upgraded.update_installation_id().unwrap(), old_id);
        fs::write(&path, b"broken json").unwrap();
        for _ in 0..2 {
            assert!(!AnalyticsService::new_with_transport(path.clone(), "https://example.invalid", "", true).status().enabled);
        }
    }
    #[test]
    fn legacy_true_stays_on_without_changing_update_identity() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("installation.json");
        let id = Uuid::new_v4().to_string();
        fs::write(
            &path,
            serde_json::to_vec(
                &json!({"installation_id": id, "enabled": true, "pending_update": "0.3.2"}),
            )
            .unwrap(),
        )
        .unwrap();
        for _ in 0..2 {
            let service = AnalyticsService::new_with_transport(
                path.clone(),
                "https://example.invalid",
                "",
                true,
            );
            assert!(service.status().enabled);
            assert!(!service.status().needs_choice);
            assert_eq!(service.update_installation_id().unwrap(), id);
            assert_eq!(
                service.state.lock().unwrap().pending_update.as_deref(),
                Some("0.3.2")
            );
            assert_eq!(
                serde_json::from_slice::<Value>(&fs::read(&path).unwrap()).unwrap()["enabled"],
                true
            );
        }
        let service =
            AnalyticsService::new_with_transport(path.clone(), "https://example.invalid", "", true);
        service.set_enabled(true).unwrap();
        for _ in 0..2 {
            let restored = AnalyticsService::new_with_transport(
                path.clone(),
                "https://example.invalid",
                "",
                true,
            );
            assert!(restored.status().enabled);
            assert_eq!(restored.status().choice, ConsentChoice::Enabled);
            assert!(!restored.status().needs_choice);
        }
    }
    #[test]
    fn legacy_false_stays_disabled_and_is_not_reprompted() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("installation.json");
        let id = Uuid::new_v4().to_string();
        fs::write(
            &path,
            serde_json::to_vec(&json!({"installation_id": id, "enabled": false})).unwrap(),
        )
        .unwrap();
        let service =
            AnalyticsService::new_with_transport(path, "https://example.invalid", "", true);
        assert_eq!(service.status().choice, ConsentChoice::Disabled);
        assert!(!service.status().enabled && !service.status().needs_choice);
        assert_eq!(service.update_installation_id().unwrap(), id);
    }
    #[test]
    fn inconsistent_choice_marker_cannot_override_a_saved_off_value() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("installation.json");
        fs::write(&path, serde_json::to_vec(&json!({"installation_id": Uuid::new_v4().to_string(), "enabled": false, "consent_choice": "enabled"})).unwrap()).unwrap();
        let service =
            AnalyticsService::new_with_transport(path, "https://example.invalid", "", true);
        assert_eq!(service.status().choice, ConsentChoice::Disabled);
        assert!(!service.status().enabled);
    }
    #[test]
    fn no_events_after_disable_and_no_backfill_on_reenable() {
        let dir = tempfile::tempdir().unwrap();
        let captured = Arc::new(Mutex::new(Vec::<Value>::new()));
        let mut service = AnalyticsService::new_with_transport(
            dir.path().join("installation.json"),
            "https://example.invalid",
            "",
            true,
        );
        let observer = captured.clone();
        service.observer = Some(Arc::new(move |body| {
            observer.lock().unwrap().push(body.clone())
        }));
        let service = Arc::new(service);
        service.set_enabled(false).unwrap();
        service.opened();
        service.track("question_asked", json!({}));
        service.track("retrieval_succeeded", json!({}));
        assert!(captured.lock().unwrap().is_empty());
        assert_eq!(service.slots.available_permits(), 8);
        service.set_enabled(true).unwrap();
        service.track("note_created", json!({}));
        assert_eq!(captured.lock().unwrap().len(), 1);
        service.set_enabled(false).unwrap();
        service.track("import_completed", json!({"success_count": 1}));
        service.set_enabled(true).unwrap();
        assert_eq!(captured.lock().unwrap().len(), 1);
        service.track("retrieval_succeeded", json!({}));
        let events = captured.lock().unwrap();
        assert_eq!(events.len(), 2);
        assert!(!events
            .iter()
            .any(|event| event["event"] == "first_successful_recall"
                || event["event"] == "import_completed"));
    }
    #[test]
    fn previous_undecided_migrates_on_but_explicit_disabled_wins_over_true() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("installation.json");
        for (choice, saved, expected) in [("undecided", false, true), ("undecided", true, true), ("disabled", true, false)] {
            fs::write(&path, serde_json::to_vec(&json!({"installation_id": Uuid::new_v4().to_string(), "enabled": saved, "consent_choice": choice})).unwrap()).unwrap();
            let service = AnalyticsService::new_with_transport(path.clone(), "https://example.invalid", "", true);
            assert_eq!(service.status().enabled, expected);
            assert!(!service.status().needs_choice);
        }
    }
    #[test]
    fn invalid_or_unreadable_existing_settings_fail_closed_not_new_install() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("installation.json");
        for bytes in ["{}", "{\"enabled\":true}", "{\"installation_id\":\"invalid\",\"enabled\":true}"] {
            fs::write(&path, bytes).unwrap();
            assert!(!AnalyticsService::new_with_transport(path.clone(), "https://example.invalid", "", true).status().enabled);
        }
        let unreadable = dir.path().join("directory-not-file");
        fs::create_dir(&unreadable).unwrap();
        assert!(!AnalyticsService::new_with_transport(unreadable, "https://example.invalid", "", true).status().enabled);
    }
    #[test]
    fn fresh_production_reports_sanitized_events_without_an_opt_in_action() {
        let dir = tempfile::tempdir().unwrap();
        let captured = Arc::new(Mutex::new(Vec::<Value>::new()));
        let observer = captured.clone();
        let mut service = AnalyticsService::new_with_transport(dir.path().join("installation.json"), "https://example.invalid", "", true);
        service.observer = Some(Arc::new(move |body| { observer.lock().unwrap().push(body.clone()) }));
        let service = Arc::new(service);
        service.opened();
        assert_eq!(captured.lock().unwrap().len(), 3);
        service.set_enabled(false).unwrap();
        service.track("note_created", json!({}));
        assert_eq!(captured.lock().unwrap().len(), 3);
    }
    #[test]
    fn development_transport_is_disabled_even_with_an_explicit_saved_choice() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("installation.json");
        AnalyticsService::new_with_transport(path.clone(), "https://example.invalid", "", true)
            .set_enabled(true)
            .unwrap();
        let captured = Arc::new(Mutex::new(Vec::<Value>::new()));
        let observer = captured.clone();
        let mut service =
            AnalyticsService::new_with_transport(path, "https://example.invalid", "", false);
        service.observer = Some(Arc::new(move |body| {
            observer.lock().unwrap().push(body.clone())
        }));
        let service = Arc::new(service);
        service.opened();
        service.track("note_created", json!({}));
        assert!(service.status().enabled);
        assert!(captured.lock().unwrap().is_empty());
    }
    #[test]
    fn first_party_envelope_has_only_allowlisted_metadata_and_random_event_ids() {
        let dir = tempfile::tempdir().unwrap();
        let captured = Arc::new(Mutex::new(Vec::<Value>::new()));
        let mut service = AnalyticsService::new_with_transport(
            dir.path().join("installation.json"),
            "https://example.invalid",
            "",
            true,
        );
        let observer = captured.clone();
        service.observer = Some(Arc::new(move |body| {
            observer.lock().unwrap().push(body.clone())
        }));
        service.set_enabled(true).unwrap();
        let service = Arc::new(service);
        service.opened();
        service.track(
            "import_completed",
            json!({"success_count":2,"filename":"private.pdf","api_key":"secret"}),
        );
        service.track(
            "app_error",
            json!({"error_type":"frontend_error","message":"private content"}),
        );
        let events = captured.lock().unwrap();
        assert_eq!(events.len(), 5);
        for v in events.iter() {
            assert_eq!(v["schema_version"], 1);
            assert_eq!(
                Uuid::parse_str(v["event_id"].as_str().unwrap())
                    .unwrap()
                    .get_version_num(),
                4
            );
            assert_eq!(v["installation_id"], events[0]["installation_id"]);
            assert!(v.get("api_key").is_none() && v.get("distinct_id").is_none());
            assert!(!v.to_string().contains("private") && !v.to_string().contains("secret"));
        }
        assert_ne!(events[0]["event_id"], events[1]["event_id"]);
    }
    #[test]
    fn sanitizer_drops_all_content_and_untrusted_values() {
        let input = json!({"question": "黄总会议", "path": "C:\\Users\\HX\\secret.pdf", "api_key":"secret", "content":"笔记", "error_type":"contains a user question", "frames":[{"module":"C:/user/secret", "line":1}], "nested":{"answer":"secret"}});
        assert_eq!(
            sanitize_telemetry_payload("question_asked", &input),
            Some(json!({}))
        );
        assert_eq!(
            sanitize_telemetry_payload("app_error", &input),
            Some(json!({}))
        );
        assert_eq!(
            sanitize_telemetry_payload("arbitrary user content", &input),
            None
        );
    }
    #[test]
    fn safe_metadata_is_bounded() {
        assert_eq!(
            sanitize_telemetry_payload(
                "import_completed",
                &json!({"file_count_bucket":"2-10", "success_count":3, "failure_count":-1, "duration_ms":"a path"})
            ),
            Some(json!({"file_count_bucket":"2-10", "success_count":3}))
        );
        assert_eq!(
            sanitize_telemetry_payload(
                "app_error",
                &json!({"error_type":"frontend_error", "frames":[{"module":"frontend", "line":12, "column":3, "filename":"private", "content":"secret"}]})
            ),
            Some(
                json!({"error_type":"frontend_error", "frames":[{"module":"frontend", "line":12, "column":3}]})
            )
        );
    }
    #[test]
    fn disabled_tracking_does_not_send_or_replay_first_recall() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("installation.json");
        let service = Arc::new(AnalyticsService::new(path.clone(), "", ""));
        service.track("question_asked", json!({"query_text":"secret"}));
        service.track("retrieval_succeeded", json!({}));
        service.track("retrieval_succeeded", json!({}));
        assert!(service.state.lock().unwrap().first_recall_seen);
        let restarted = AnalyticsService::new(path, "", "");
        assert!(restarted.state.lock().unwrap().first_recall_seen);
        assert_eq!(service.slots.available_permits(), 8);
    }
    #[test]
    fn consent_persists_and_invalid_config_is_noop() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("installation.json");
        let service = Arc::new(AnalyticsService::new(
            path.clone(),
            "http://unsafe.example",
            "phc_placeholder",
        ));
        service.set_enabled(true).unwrap();
        service.track("note_created", json!({"note_content":"private"}));
        assert!(!service.status().configured);
        assert_eq!(service.slots.available_permits(), 8);
        service.set_enabled(false).unwrap();
        assert!(!AnalyticsService::new(path, "", "").status().enabled);
    }
}
