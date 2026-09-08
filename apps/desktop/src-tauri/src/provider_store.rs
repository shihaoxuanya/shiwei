use keyring::Entry;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::{Mutex, MutexGuard},
};
use tauri::{AppHandle, Manager};

const SERVICE: &str = "com.shiwei.desktop";
const CHAT_KEY: &str = "openai-compatible-api-key";
const EMBEDDING_KEY: &str = "embedding-api-key";
const CREDENTIAL_PREFIX: &str = "shiwei-credential:";
// Serialize migration, credentials and public configuration as one operation.
// A draft endpoint must never become the owner of an unbound legacy secret.
static PROVIDER_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone, PartialEq, Eq, Deserialize, Serialize)]
struct CredentialBinding {
    endpoint: String,
    protocol: String,
    slot: String,
}

#[derive(Deserialize, Serialize)]
struct BoundCredential {
    version: u8,
    binding: CredentialBinding,
    secret: String,
}

trait CredentialStore {
    fn get(&self, name: &str) -> Result<Option<String>, String>;
    fn set(&self, name: &str, value: &str) -> Result<(), String>;
    fn delete(&self, name: &str) -> Result<(), String>;
}

struct SystemCredentialStore;
impl CredentialStore for SystemCredentialStore {
    fn get(&self, name: &str) -> Result<Option<String>, String> {
        match entry(name)?.get_password() {
            Ok(value) => Ok(Some(value)),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) => Err(keyring_error(error)),
        }
    }
    fn set(&self, name: &str, value: &str) -> Result<(), String> {
        entry(name)?.set_password(value).map_err(keyring_error)
    }
    fn delete(&self, name: &str) -> Result<(), String> {
        match entry(name)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(error) => Err(keyring_error(error)),
        }
    }
}

fn provider_lock() -> Result<MutexGuard<'static, ()>, String> {
    PROVIDER_LOCK
        .lock()
        .map_err(|_| "模型配置正在恢复，请重新启动拾微".into())
}

fn binding(public: &ProviderPublicConfig, name: &str) -> Option<CredentialBinding> {
    let (endpoint, protocol) = match name {
        CHAT_KEY => (&public.base_url, public.protocol.clone()),
        EMBEDDING_KEY if public.embedding_mode == "separate" => {
            (&public.embedding_base_url, compatible())
        }
        _ => return None,
    };
    Some(CredentialBinding {
        endpoint: endpoint.trim().trim_end_matches('/').to_owned(),
        protocol,
        slot: name.into(),
    })
}

fn encode_credential(binding: CredentialBinding, secret: String) -> Result<String, String> {
    serde_json::to_string(&BoundCredential {
        version: 1,
        binding,
        secret,
    })
    .map(|value| format!("{CREDENTIAL_PREFIX}{value}"))
    .map_err(|_| "无法安全保存模型凭据".into())
}

// `saved` must originate from the persisted public configuration, never a draft.
// Migrate legacy raw secrets locally, without a model request or secret logging.
fn read_bound_secret(
    store: &impl CredentialStore,
    name: &str,
    saved: Option<&ProviderPublicConfig>,
) -> Result<Option<String>, String> {
    let Some(expected) = saved.and_then(|public| binding(public, name)) else {
        return Ok(None);
    };
    let Some(value) = store.get(name)? else {
        return Ok(None);
    };
    if let Some(json) = value.strip_prefix(CREDENTIAL_PREFIX) {
        let Ok(credential) = serde_json::from_str::<BoundCredential>(json) else {
            return Ok(None);
        };
        return Ok((credential.version == 1
            && credential.binding == expected
            && !credential.secret.is_empty())
        .then_some(credential.secret));
    }
    if value.trim().is_empty() {
        return Ok(None);
    }
    store.set(name, &encode_credential(expected, value.clone())?)?;
    Ok(Some(value))
}

fn persist_configuration(
    store: &impl CredentialStore,
    saved: Option<&ProviderPublicConfig>,
    public: &ProviderPublicConfig,
    key: String,
    embedding_key: Option<String>,
    write_config: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    // Bind every available legacy key to the OLD saved endpoint before changing
    // either slot, even when the user supplied replacement keys for both.
    for name in [CHAT_KEY, EMBEDDING_KEY] {
        read_bound_secret(store, name, saved)?;
    }
    let mut changes = vec![(
        CHAT_KEY,
        encode_credential(binding(public, CHAT_KEY).ok_or("无效的对话凭据")?, key)?,
    )];
    if let Some(key) = embedding_key {
        changes.push((
            EMBEDDING_KEY,
            encode_credential(
                binding(public, EMBEDDING_KEY).ok_or("无效的语义检索凭据")?,
                key,
            )?,
        ));
    }
    let backups: Vec<_> = changes
        .iter()
        .map(|(name, _)| store.get(name).map(|old| (*name, old)))
        .collect::<Result<_, _>>()?;
    let result = (|| {
        for (name, value) in &changes {
            store.set(name, value)?;
        }
        write_config()
    })();
    if let Err(error) = result {
        let mut rollback_failed = false;
        for (name, old) in backups {
            let restored = match old {
                Some(value) => store.set(name, &value),
                None => store.delete(name),
            };
            rollback_failed |= restored.is_err();
        }
        // If the OS also rejects rollback, readers still validate the envelope
        // and cannot send a replacement key to the previously saved endpoint.
        return Err(if rollback_failed {
            format!("{error}；旧凭据未能恢复，请重新输入密钥")
        } else {
            error
        });
    }
    Ok(())
}
fn compatible() -> String {
    "openai_compatible".into()
}
fn custom() -> String {
    "custom".into()
}
fn same() -> String {
    "same".into()
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderPublicConfig {
    pub base_url: String,
    pub chat_model: String,
    #[serde(default)]
    pub embedding_model: String,
    #[serde(default = "compatible")]
    pub protocol: String,
    #[serde(default = "custom")]
    pub provider_id: String,
    #[serde(default = "same")]
    pub embedding_mode: String,
    #[serde(default)]
    pub embedding_base_url: String,
    #[serde(default = "custom")]
    pub embedding_provider_id: String,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderInput {
    #[serde(flatten)]
    pub public: ProviderPublicConfig,
    pub api_key: Option<String>,
    pub embedding_api_key: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderStatus {
    pub configured: bool,
    pub has_api_key: bool,
    pub has_embedding_api_key: bool,
    #[serde(flatten)]
    pub public: Option<ProviderPublicConfig>,
}

impl ProviderPublicConfig {
    pub fn worker_params(&self, key: String, embedding_key: Option<String>) -> serde_json::Value {
        let mut value = serde_json::to_value(self).expect("serializable config");
        value["apiKey"] = serde_json::json!(key);
        value["embeddingApiKey"] = serde_json::json!(embedding_key);
        value
    }
}

pub fn save(app: &AppHandle, input: &ProviderInput) -> Result<ProviderPublicConfig, String> {
    let _guard = provider_lock()?;
    let (public, key, embedding_key) = resolve_for_request_inner(app, input.clone())?;
    if public.chat_model.is_empty()
        || (public.embedding_mode != "none" && public.embedding_model.is_empty())
    {
        return Err("请选择对话模型和已启用的语义检索模型".into());
    }
    let saved = read_public(app)?;
    let path = config_path(app)?;
    persist_configuration(
        &SystemCredentialStore,
        saved.as_ref(),
        &public,
        key,
        embedding_key,
        || write_public(&path, &public),
    )?;
    Ok(public)
}

fn read_public(app: &AppHandle) -> Result<Option<ProviderPublicConfig>, String> {
    let path = config_path(app)?;
    if !path.is_file() {
        return Ok(None);
    }
    let bytes = fs::read(&path).map_err(|e| format!("无法读取模型配置：{e}"))?;
    serde_json::from_slice(&bytes)
        .map_err(|_| "模型配置文件已损坏".to_owned())
        .and_then(validate)
        .map(Some)
}

pub fn clear_key(app: &AppHandle, target: &str) -> Result<(), String> {
    let _guard = provider_lock()?;
    let name = match target {
        "chat" => CHAT_KEY,
        "embedding" => EMBEDDING_KEY,
        _ => return Err("无效的凭据类型".into()),
    };
    match entry(name)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => {}
        Err(e) => return Err(keyring_error(e)),
    }
    if target == "embedding" {
        if let Some(mut public) = read_public(app)? {
            public.embedding_mode = "none".into();
            write_public(&config_path(app)?, &public)?;
        }
    }
    Ok(())
}
pub fn runtime_params(app: &AppHandle) -> Result<Option<serde_json::Value>, String> {
    let _guard = provider_lock()?;
    let Some(public) = read_public(app)? else {
        return Ok(None);
    };
    let Some(key) = read_bound_secret(&SystemCredentialStore, CHAT_KEY, Some(&public))? else {
        return Ok(None);
    };
    let embedding_key = if public.embedding_mode == "separate" {
        read_bound_secret(&SystemCredentialStore, EMBEDDING_KEY, Some(&public))?
    } else {
        None
    };
    Ok(Some(public.worker_params(key, embedding_key)))
}

pub fn status(app: &AppHandle) -> Result<ProviderStatus, String> {
    let _guard = provider_lock()?;
    let public = read_public(app)?;
    let has_api_key =
        read_bound_secret(&SystemCredentialStore, CHAT_KEY, public.as_ref())?.is_some();
    let has_embedding_api_key = public
        .as_ref()
        .is_some_and(|p| p.embedding_mode == "separate")
        && read_bound_secret(&SystemCredentialStore, EMBEDDING_KEY, public.as_ref())?.is_some();
    Ok(ProviderStatus {
        configured: public.is_some() && has_api_key,
        has_api_key,
        has_embedding_api_key,
        public,
    })
}

fn same_chat_endpoint(current: &ProviderPublicConfig, saved: &ProviderPublicConfig) -> bool {
    current.base_url == saved.base_url && current.protocol == saved.protocol
}

pub fn resolve_for_request(
    app: &AppHandle,
    input: ProviderInput,
) -> Result<(ProviderPublicConfig, String, Option<String>), String> {
    let _guard = provider_lock()?;
    resolve_for_request_inner(app, input)
}

fn resolve_for_request_inner(
    app: &AppHandle,
    input: ProviderInput,
) -> Result<(ProviderPublicConfig, String, Option<String>), String> {
    let saved = read_public(app)?;
    resolve_with_store(input, saved.as_ref(), &SystemCredentialStore)
}

fn resolve_with_store(
    input: ProviderInput,
    saved: Option<&ProviderPublicConfig>,
    store: &impl CredentialStore,
) -> Result<(ProviderPublicConfig, String, Option<String>), String> {
    let public = validate(input.public)?;
    let key = match input.api_key.filter(|s| !s.trim().is_empty()) {
        Some(key) => key.trim().to_owned(),
        None if saved.is_some_and(|s| same_chat_endpoint(&public, s)) => {
            read_bound_secret(store, CHAT_KEY, saved)?.ok_or("请输入 API Key")?
        }
        None => return Err("请输入所选厂商的 API Key；切换地址不会复用其他厂商密钥".into()),
    };
    let embedding_key = if public.embedding_mode == "separate" {
        Some(
            match input.embedding_api_key.filter(|s| !s.trim().is_empty()) {
                Some(key) => key.trim().to_owned(),
                None if saved.is_some_and(|s| {
                    s.embedding_mode == "separate"
                        && s.embedding_base_url == public.embedding_base_url
                }) =>
                {
                    read_bound_secret(store, EMBEDDING_KEY, saved)?
                        .ok_or("请输入语义检索服务的 API Key")?
                }
                None => return Err("请输入语义检索服务的 API Key".into()),
            },
        )
    } else {
        None
    };
    Ok((public, key, embedding_key))
}

fn validate(mut public: ProviderPublicConfig) -> Result<ProviderPublicConfig, String> {
    public.base_url = public.base_url.trim().trim_end_matches('/').to_owned();
    public.embedding_base_url = public
        .embedding_base_url
        .trim()
        .trim_end_matches('/')
        .to_owned();
    public.chat_model = public.chat_model.trim().to_owned();
    public.embedding_model = public.embedding_model.trim().to_owned();
    for url in std::iter::once(&public.base_url)
        .chain((public.embedding_mode == "separate").then_some(&public.embedding_base_url))
    {
        let parsed = tauri::Url::parse(url).map_err(|_| "请输入有效的模型服务地址")?;
        if parsed.scheme() != "https"
            || parsed.host_str().is_none()
            || !parsed.username().is_empty()
            || parsed.password().is_some()
            || parsed.query().is_some()
            || parsed.fragment().is_some()
        {
            return Err("模型服务地址必须是 HTTPS，且不能包含密钥或查询参数".into());
        }
    }
    if !["openai_compatible", "anthropic"].contains(&public.protocol.as_str())
        || !["same", "separate", "none"].contains(&public.embedding_mode.as_str())
    {
        return Err("模型协议或语义检索模式不正确".into());
    }
    if public.protocol == "anthropic" && public.embedding_mode == "same" {
        return Err("Anthropic 需要独立的语义检索厂商，或选择暂不启用".into());
    }
    Ok(public)
}

fn config_path(app: &AppHandle) -> Result<PathBuf, String> {
    // Explicit development-only isolation; never read/write production settings
    // or credentials during synthetic desktop QA. Release ignores this variable.
    if cfg!(debug_assertions) {
        if let Some(path) = std::env::var_os("SHIWEI_QA_CONFIG_DIR") {
            let directory = PathBuf::from(path);
            if !directory.is_absolute() {
                return Err("QA 配置目录必须是绝对路径".into());
            }
            fs::create_dir_all(&directory).map_err(|_| "无法创建 QA 配置目录")?;
            return Ok(directory.join("provider.json"));
        }
    }
    let directory = app
        .path()
        .app_config_dir()
        .map_err(|e| format!("无法确定配置目录：{e}"))?;
    fs::create_dir_all(&directory).map_err(|e| format!("无法创建配置目录：{e}"))?;
    Ok(directory.join("provider.json"))
}

fn write_public(path: &Path, value: &ProviderPublicConfig) -> Result<(), String> {
    let temporary = path.with_extension("json.tmp");
    let bytes = serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?;
    fs::write(&temporary, bytes).map_err(|e| format!("无法保存模型配置：{e}"))?;
    fs::rename(&temporary, path).map_err(|e| format!("无法完成模型配置保存：{e}"))
}

fn entry(name: &str) -> Result<Entry, String> {
    let service = if cfg!(debug_assertions) {
        match std::env::var_os("SHIWEI_QA_CONFIG_DIR") {
            Some(path) => qa_credential_service(Path::new(&path))?,
            None => SERVICE.into(),
        }
    } else {
        SERVICE.into()
    };
    Entry::new(&service, name).map_err(keyring_error)
}

fn qa_credential_service(directory: &Path) -> Result<String, String> {
    if !directory.is_absolute() {
        return Err("QA 配置目录必须是绝对路径".into());
    }
    // Match the actual directory rather than a relative path or display alias.
    // Only the digest, never the path, is used in the OS credential namespace.
    fs::create_dir_all(directory).map_err(|_| "无法创建 QA 配置目录")?;
    let directory = fs::canonicalize(directory).map_err(|_| "无法确定 QA 配置目录")?;
    let mut identity = directory.to_string_lossy().into_owned();
    if cfg!(windows) {
        identity.make_ascii_lowercase();
    }
    let digest = Sha256::digest(identity.as_bytes());
    Ok(format!("{SERVICE}.qa.{digest:x}"))
}

pub fn resolve_embedding_test(
    app: &AppHandle,
    input: ProviderInput,
) -> Result<(ProviderPublicConfig, String, Option<String>), String> {
    let _guard = provider_lock()?;
    if input.public.embedding_mode == "none" {
        return Err("智能检索未启用".into());
    }
    if input.public.embedding_mode == "same" {
        return resolve_for_request_inner(app, input);
    }
    let saved = read_public(app)?;
    resolve_separate_embedding_test(input, saved.as_ref(), || {
        read_bound_secret(&SystemCredentialStore, EMBEDDING_KEY, saved.as_ref())
    })
}

fn resolve_separate_embedding_test(
    input: ProviderInput,
    saved: Option<&ProviderPublicConfig>,
    read_embedding_key: impl FnOnce() -> Result<Option<String>, String>,
) -> Result<(ProviderPublicConfig, String, Option<String>), String> {
    let mut public = input.public;
    public.base_url = public.embedding_base_url.clone();
    public.provider_id = public.embedding_provider_id.clone();
    public.protocol = compatible();
    public.embedding_mode = same();
    let public = validate(public)?;
    let key = match input.embedding_api_key.filter(|k| !k.trim().is_empty()) {
        Some(k) => k.trim().to_owned(),
        None if saved.is_some_and(|s| {
            s.embedding_mode == "separate" && s.embedding_base_url == public.base_url
        }) =>
        {
            read_embedding_key()?.ok_or("请输入语义检索 API Key")?
        }
        None => return Err("请输入所选语义检索服务的 API Key".into()),
    };
    // Deliberately ignore input.api_key: independent embedding tests must never
    // transmit a saved or newly typed chat key to the vector service.
    Ok((public, key, None))
}
fn keyring_error(error: keyring::Error) -> String {
    let store = if cfg!(target_os = "macos") {
        "macOS 钥匙串"
    } else if cfg!(windows) {
        "Windows 凭据管理器"
    } else {
        "系统凭据存储"
    };
    format!("无法访问{store}：{error}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        cell::{Cell, RefCell},
        collections::HashMap,
    };

    #[derive(Default)]
    struct MemoryCredentials {
        values: RefCell<HashMap<String, String>>,
        writes: Cell<usize>,
        fail_on_write: Cell<Option<usize>>,
    }
    impl CredentialStore for MemoryCredentials {
        fn get(&self, name: &str) -> Result<Option<String>, String> {
            Ok(self.values.borrow().get(name).cloned())
        }
        fn set(&self, name: &str, value: &str) -> Result<(), String> {
            let writes = self.writes.get() + 1;
            self.writes.set(writes);
            if self.fail_on_write.get() == Some(writes) {
                return Err("synthetic credential failure".into());
            }
            self.values.borrow_mut().insert(name.into(), value.into());
            Ok(())
        }
        fn delete(&self, name: &str) -> Result<(), String> {
            self.values.borrow_mut().remove(name);
            Ok(())
        }
    }
    fn separate_config() -> ProviderPublicConfig {
        let mut public = legacy();
        public.embedding_mode = "separate".into();
        public.embedding_base_url = "https://vector.example.com/v1".into();
        public
    }
    fn seeded_credentials() -> MemoryCredentials {
        let store = MemoryCredentials::default();
        // Directly seed legacy values; all test storage is in memory, not keyring.
        store
            .values
            .borrow_mut()
            .insert(CHAT_KEY.into(), "synthetic-old-chat".into());
        store
            .values
            .borrow_mut()
            .insert(EMBEDDING_KEY.into(), "synthetic-old-vector".into());
        store
    }
    #[cfg(target_os = "macos")]
    #[test]
    fn mac_keychain_round_trip_uses_only_a_disposable_test_entry() {
        let name = format!("mac-test-{}", uuid::Uuid::new_v4());
        let credential = Entry::new("com.shiwei.desktop.qa", &name).unwrap();
        credential.set_password("synthetic-test-value").unwrap();
        let value = credential.get_password();
        let cleanup = credential.delete_credential();
        assert_eq!(value.unwrap(), "synthetic-test-value");
        cleanup.unwrap();
        assert!(matches!(
            credential.get_password(),
            Err(keyring::Error::NoEntry)
        ));
    }
    fn legacy() -> ProviderPublicConfig {
        serde_json::from_value(serde_json::json!({"baseUrl":"https://api.example.com/v1","chatModel":"chat","embeddingModel":"embed"})).unwrap()
    }
    #[test]
    fn legacy_config_migrates_without_exposing_keys() {
        let c = legacy();
        assert_eq!(c.embedding_mode, "same");
        assert_eq!(c.protocol, "openai_compatible");
        assert!(!serde_json::to_string(&c).unwrap().contains("apiKey"));
    }
    #[test]
    fn keys_are_not_reused_for_another_endpoint() {
        let old = legacy();
        let mut next = old.clone();
        next.base_url = "https://different.example.com/v1".into();
        assert!(!same_chat_endpoint(&next, &old));
    }
    #[test]
    fn rejects_secret_in_url() {
        let mut c = legacy();
        c.base_url = "https://user:secret@api.example.com/v1".into();
        assert!(validate(c).is_err());
    }
    #[test]
    fn independent_embedding_test_never_uses_the_chat_key_or_endpoint() {
        let mut public = legacy();
        public.embedding_mode = "separate".into();
        public.embedding_base_url = "https://vector.example.com/v1/".into();
        public.embedding_provider_id = "vector".into();
        let input = ProviderInput {
            public,
            api_key: Some("synthetic-chat-secret".into()),
            embedding_api_key: Some("synthetic-vector-secret".into()),
        };
        let (public, key, extra_key) = resolve_separate_embedding_test(input, None, || {
            panic!("must not read saved credentials")
        })
        .unwrap();
        let worker = public.worker_params(key, extra_key);
        assert_eq!(worker["baseUrl"], "https://vector.example.com/v1");
        assert_eq!(worker["protocol"], "openai_compatible");
        assert_eq!(worker["apiKey"], "synthetic-vector-secret");
        assert!(!worker.to_string().contains("synthetic-chat-secret"));
    }
    #[test]
    fn independent_embedding_test_does_not_reuse_saved_key_after_endpoint_switch() {
        let mut saved = legacy();
        saved.embedding_mode = "separate".into();
        saved.embedding_base_url = "https://old.example.com/v1".into();
        let mut public = saved.clone();
        public.embedding_base_url = "https://new.example.com/v1".into();
        let input = ProviderInput {
            public,
            api_key: Some("unrelated-chat-key".into()),
            embedding_api_key: None,
        };
        assert!(
            resolve_separate_embedding_test(input, Some(&saved), || panic!(
                "old key must not be read"
            ))
            .is_err()
        );
    }

    #[test]
    fn failed_public_write_restores_both_keys_bound_to_old_endpoints() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("provider.json");
        let saved = separate_config();
        write_public(&path, &saved).unwrap();
        // Make the real public writer fail before rename, leaving the old file.
        fs::create_dir(path.with_extension("json.tmp")).unwrap();
        let mut next = saved.clone();
        next.base_url = "https://next.example.com/v1".into();
        next.embedding_base_url = "https://next-vector.example.com/v1".into();
        let store = seeded_credentials();
        assert!(persist_configuration(
            &store,
            Some(&saved),
            &next,
            "synthetic-new-chat".into(),
            Some("synthetic-new-vector".into()),
            || write_public(&path, &next)
        )
        .is_err());
        let actual: ProviderPublicConfig =
            serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(actual.base_url, saved.base_url);
        assert_eq!(
            read_bound_secret(&store, CHAT_KEY, Some(&actual))
                .unwrap()
                .as_deref(),
            Some("synthetic-old-chat")
        );
        assert_eq!(
            read_bound_secret(&store, EMBEDDING_KEY, Some(&actual))
                .unwrap()
                .as_deref(),
            Some("synthetic-old-vector")
        );
        assert!(read_bound_secret(&store, CHAT_KEY, Some(&next))
            .unwrap()
            .is_none());
        assert!(read_bound_secret(&store, EMBEDDING_KEY, Some(&next))
            .unwrap()
            .is_none());
        let public_bytes = fs::read_to_string(path).unwrap();
        assert!(!public_bytes.contains("synthetic-"));
    }

    #[test]
    fn rollback_failure_still_cannot_use_new_key_with_old_endpoint() {
        let saved = separate_config();
        let mut next = saved.clone();
        next.base_url = "https://next.example.com/v1".into();
        let store = seeded_credentials();
        // Legacy migrations are writes 1+2; replacements 3+4; chat rollback 5.
        store.fail_on_write.set(Some(5));
        assert!(persist_configuration(
            &store,
            Some(&saved),
            &next,
            "synthetic-new-chat".into(),
            Some("synthetic-new-vector".into()),
            || Err("synthetic public write failure".into())
        )
        .is_err());
        assert!(read_bound_secret(&store, CHAT_KEY, Some(&saved))
            .unwrap()
            .is_none());
        assert_eq!(
            read_bound_secret(&store, EMBEDDING_KEY, Some(&saved))
                .unwrap()
                .as_deref(),
            Some("synthetic-old-vector")
        );
    }

    #[test]
    fn partial_credential_write_rolls_back_before_public_commit() {
        let saved = separate_config();
        let store = seeded_credentials();
        store.fail_on_write.set(Some(4));
        assert!(persist_configuration(
            &store,
            Some(&saved),
            &saved,
            "synthetic-new-chat".into(),
            Some("synthetic-new-vector".into()),
            || panic!("public config must not commit")
        )
        .is_err());
        assert_eq!(
            read_bound_secret(&store, CHAT_KEY, Some(&saved))
                .unwrap()
                .as_deref(),
            Some("synthetic-old-chat")
        );
        assert_eq!(
            read_bound_secret(&store, EMBEDDING_KEY, Some(&saved))
                .unwrap()
                .as_deref(),
            Some("synthetic-old-vector")
        );
    }

    #[test]
    fn binding_rejects_endpoint_protocol_and_slot_changes() {
        let saved = separate_config();
        let store = seeded_credentials();
        read_bound_secret(&store, CHAT_KEY, Some(&saved)).unwrap();
        let mut changed = saved.clone();
        changed.base_url = "https://other.example.com/v1".into();
        assert!(read_bound_secret(&store, CHAT_KEY, Some(&changed))
            .unwrap()
            .is_none());
        changed = saved.clone();
        changed.protocol = "anthropic".into();
        assert!(read_bound_secret(&store, CHAT_KEY, Some(&changed))
            .unwrap()
            .is_none());
        let chat_value = store.get(CHAT_KEY).unwrap().unwrap();
        store.set(EMBEDDING_KEY, &chat_value).unwrap();
        changed.embedding_base_url = saved.base_url.clone();
        assert!(read_bound_secret(&store, EMBEDDING_KEY, Some(&changed))
            .unwrap()
            .is_none());
    }

    #[test]
    fn raw_legacy_key_requires_saved_endpoint_and_cannot_follow_a_draft() {
        let saved = legacy();
        let store = seeded_credentials();
        assert!(read_bound_secret(&store, CHAT_KEY, None).unwrap().is_none());
        assert_eq!(store.writes.get(), 0);
        let mut draft = saved.clone();
        draft.base_url = "https://draft.example.com/v1".into();
        let input = ProviderInput {
            public: draft.clone(),
            api_key: None,
            embedding_api_key: None,
        };
        assert!(resolve_with_store(input, Some(&saved), &store).is_err());
        assert_eq!(store.writes.get(), 0);
        let input = ProviderInput {
            public: saved.clone(),
            api_key: None,
            embedding_api_key: None,
        };
        assert_eq!(
            resolve_with_store(input, Some(&saved), &store).unwrap().1,
            "synthetic-old-chat"
        );
        assert_eq!(store.writes.get(), 1);
        assert!(read_bound_secret(&store, CHAT_KEY, Some(&draft))
            .unwrap()
            .is_none());
    }

    #[test]
    fn independent_embedding_test_reads_only_bound_embedding_credential() {
        let saved = separate_config();
        let store = seeded_credentials();
        let input = ProviderInput {
            public: saved.clone(),
            api_key: Some("synthetic-draft-chat".into()),
            embedding_api_key: None,
        };
        let (public, key, extra) = resolve_separate_embedding_test(input, Some(&saved), || {
            read_bound_secret(&store, EMBEDDING_KEY, Some(&saved))
        })
        .unwrap();
        assert_eq!(public.base_url, saved.embedding_base_url);
        assert_eq!(key, "synthetic-old-vector");
        assert!(extra.is_none());
        assert_eq!(store.writes.get(), 1);
        assert_eq!(
            store.get(CHAT_KEY).unwrap().as_deref(),
            Some("synthetic-old-chat")
        );
    }

    #[test]
    fn invalid_or_future_envelopes_are_not_treated_as_legacy_keys() {
        let store = seeded_credentials();
        let saved = legacy();
        store.set(CHAT_KEY, "shiwei-credential:invalid").unwrap();
        assert!(read_bound_secret(&store, CHAT_KEY, Some(&saved))
            .unwrap()
            .is_none());
        let mut envelope: serde_json::Value = serde_json::from_str(
            &encode_credential(binding(&saved, CHAT_KEY).unwrap(), "synthetic".into()).unwrap()
                [CREDENTIAL_PREFIX.len()..],
        )
        .unwrap();
        envelope["version"] = serde_json::json!(2);
        store
            .set(CHAT_KEY, &format!("{CREDENTIAL_PREFIX}{envelope}"))
            .unwrap();
        assert!(read_bound_secret(&store, CHAT_KEY, Some(&saved))
            .unwrap()
            .is_none());
    }

    #[test]
    fn qa_credential_namespace_is_isolated_by_canonical_config_directory() {
        let root = tempfile::tempdir().unwrap();
        let first = root.path().join("first");
        let second = root.path().join("second");
        let first_service = qa_credential_service(&first).unwrap();
        assert_ne!(first_service, qa_credential_service(&second).unwrap());
        assert_eq!(
            first_service,
            qa_credential_service(&first.join(".")).unwrap()
        );
        assert!(!first_service.contains("first"));
        assert_ne!(first_service, SERVICE);
        assert!(qa_credential_service(Path::new("relative-qa")).is_err());
    }
}
