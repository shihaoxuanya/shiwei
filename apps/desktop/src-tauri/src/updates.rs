use crate::{analytics::AnalyticsService, WorkerState};
use serde_json::{json, Value};
use std::sync::Arc;
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::Duration,
};
use tauri::{Emitter, Manager, State};
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Default)]
pub struct UpdateState {
    pending: Mutex<Option<Update>>,
    busy: AtomicBool,
}
struct BusyGuard<'a>(&'a AtomicBool);
impl Drop for BusyGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::SeqCst);
    }
}
fn begin(state: &UpdateState) -> Result<BusyGuard<'_>, String> {
    if state.busy.swap(true, Ordering::SeqCst) {
        return Err("update_busy".into());
    }
    Ok(BusyGuard(&state.busy))
}
pub fn configured(app: &tauri::AppHandle) -> bool {
    let value = app.config().plugins.0.get("updater");
    value.is_some_and(|v| {
        v["pubkey"].as_str().is_some_and(|s| !s.is_empty())
            && v["endpoints"].as_array().is_some_and(|a| !a.is_empty())
    })
}
pub fn is_stable_upgrade(current: &str, next: &str) -> bool {
    match (
        semver::Version::parse(current),
        semver::Version::parse(next),
    ) {
        (Ok(old), Ok(new)) => new.pre.is_empty() && new > old,
        _ => false,
    }
}
fn error_code(error: &tauri_plugin_updater::Error, phase: &str) -> &'static str {
    use tauri_plugin_updater::Error::*;
    match error {
        Minisign(_) | Base64(_) | SignatureUtf8(_) => "update_signature",
        Serialization(_) | Semver(_) | ReleaseNotFound | TargetNotFound(_) | TargetsNotFound(_) => {
            "update_manifest"
        }
        Reqwest(_) | Network(_) if phase == "check" => "update_network",
        Reqwest(_) | Network(_) => "update_download",
        _ if phase == "install" => "update_install",
        _ => "update_unknown",
    }
}
fn failure(app: &tauri::AppHandle, code: &str) -> String {
    // Deliberately never log raw URLs, network response bodies or error messages.
    eprintln!("[updater] {code}");
    app.state::<Arc<AnalyticsService>>()
        .track("update_failed", json!({"error_type": code}));
    code.to_owned()
}

fn mandatory(raw: &Value) -> bool {
    raw.get("mandatory").and_then(Value::as_bool).unwrap_or(false)
}

async fn fetch_update(app: &tauri::AppHandle) -> Result<Option<Update>, String> {
    let identity = app.state::<Arc<AnalyticsService>>().update_installation_id()?;
    let updater = app.updater_builder()
        .header("X-Installation-Id", identity)
        .map_err(|_| failure(app, "update_identity"))?
        .timeout(Duration::from_secs(15))
        .configure_client(|client| client.redirect(reqwest_updater::redirect::Policy::custom(|attempt| {
            // Never forward the cohort header through a manifest redirect. Installer
            // redirects may go only from GitHub to its CDN after the header is removed.
            let first = attempt.previous().first();
            if attempt.previous().len() == 1
                && first.is_some_and(|u| u.scheme() == "https" && u.host_str() == Some("github.com"))
                && attempt.url().scheme() == "https"
                && attempt.url().host_str() == Some("release-assets.githubusercontent.com") {
                attempt.follow()
            } else { attempt.stop() }
        })))
        .build().map_err(|e| failure(app, error_code(&e, "check")))?;
    let mut result = updater.check().await.map_err(|e| failure(app, error_code(&e, "check")))?;
    if let Some(ref mut update) = result {
        // Plugin headers otherwise persist into the installer download request.
        update.headers.remove("X-Installation-Id");
        update.timeout = Some(Duration::from_secs(1800));
    }
    Ok(result)
}

#[tauri::command]
pub async fn update_check(
    app: tauri::AppHandle,
    state: State<'_, UpdateState>,
) -> Result<Value, String> {
    let _busy = begin(&state)?;
    if !configured(&app) {
        return Ok(json!({"status":"unconfigured"}));
    }
    *state.pending.lock().map_err(|_| "update_unknown")? = None;
    match fetch_update(&app).await? {
        Some(mut update) => {
            if !is_stable_upgrade(env!("CARGO_PKG_VERSION"), &update.version) {
                return Ok(json!({"status":"current"}));
            }
            if update.download_url.scheme() != "https"
                || !update.download_url.username().is_empty()
                || update.download_url.password().is_some()
            {
                return Err(failure(&app, "update_manifest"));
            }
            // Large bundled Worker needs a longer download budget than the manifest check.
            update.timeout = Some(Duration::from_secs(1800));
            let result = json!({"status":"available", "version": update.version, "mandatory": mandatory(&update.raw_json), "notes": update.body.as_deref().unwrap_or("").chars().take(8000).collect::<String>()});
            *state.pending.lock().map_err(|_| "update_unknown")? = Some(update);
            app.state::<Arc<AnalyticsService>>()
                .track("update_available", json!({}));
            Ok(result)
        }
        None => Ok(json!({"status":"current"})),
    }
}

#[tauri::command]
pub async fn update_install(
    app: tauri::AppHandle,
    state: State<'_, UpdateState>,
) -> Result<(), String> {
    let _busy = begin(&state)?;
    let update = state
        .pending
        .lock()
        .map_err(|_| "update_unknown")?
        .take()
        .ok_or("update_not_checked")?;
    // Honor pause/revoke and policy changes while the user was reading the dialog.
    // Once downloading starts, official signature verification remains mandatory.
    let fresh = fetch_update(&app).await?.ok_or("update_withdrawn")?;
    if fresh.version != update.version || fresh.signature != update.signature
        || fresh.download_url != update.download_url || mandatory(&fresh.raw_json) != mandatory(&update.raw_json) {
        return Err(failure(&app, "update_withdrawn"));
    }
    let service = app.state::<Arc<AnalyticsService>>().inner().clone();
    service.track("update_started", json!({}));
    let mut received = 0_u64;
    let bytes = update
        .download(
            |chunk, total| {
                received = received.saturating_add(chunk as u64);
                let _ = app.emit(
                    "update-progress",
                    json!({"phase":"downloading", "received": received, "total": total}),
                );
            },
            || {
                let _ = app.emit("update-progress", json!({"phase":"verifying"}));
            },
        )
        .await
        .map_err(|e| failure(&app, error_code(&e, "download")))?;
    // Official download() has verified the signature. There is no bypass path or IPC for raw bytes.
    let worker = app.state::<WorkerState>().0.clone();
    worker
        .prepare_update()
        .map_err(|_| failure(&app, "update_worker_busy"))?;
    if service.mark_update(Some(update.version.clone())).is_err() {
        worker.resume_after_update();
        return Err(failure(&app, "update_install"));
    }
    let _ = app.emit("update-progress", json!({"phase":"installing"}));
    if let Err(error) = update.install(bytes) {
        worker.resume_after_update();
        let _ = service.mark_update(None);
        return Err(failure(&app, error_code(&error, "install")));
    }
    // On Windows the official NSIS updater exits this process and relaunches the new app.
    // Completion is reported only on the next launch with the expected installed version.
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn mandatory_requires_a_real_boolean() {
        assert!(mandatory(&json!({"mandatory":true})));
        for value in [json!({}), json!({"mandatory":"true"}), json!({"mandatory":1}), json!({"mandatory":false})] {
            assert!(!mandatory(&value));
        }
    }
    #[test]
    fn stable_never_downgrades_or_installs_beta() {
        assert!(is_stable_upgrade("0.3.0", "0.3.1"));
        for version in ["0.2.2", "0.3.0", "0.4.0-beta.2", "invalid"] {
            assert!(!is_stable_upgrade("0.3.0", version));
        }
    }
    #[test]
    fn failures_have_content_free_codes() {
        assert_eq!(
            error_code(
                &tauri_plugin_updater::Error::Network("secret path/token".into()),
                "download"
            ),
            "update_download"
        );
        assert_eq!(
            error_code(&tauri_plugin_updater::Error::ReleaseNotFound, "check"),
            "update_manifest"
        );
    }
    #[test]
    fn duplicate_updates_are_locked() {
        let state = UpdateState::default();
        let guard = begin(&state).unwrap();
        assert!(begin(&state).is_err());
        drop(guard);
        assert!(begin(&state).is_ok());
    }
}
