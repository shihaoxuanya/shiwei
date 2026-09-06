use keyring::Entry;
use serde::{Deserialize, Serialize};
use std::{
    fs,
    path::{Path, PathBuf},
};
use tauri::{AppHandle, Manager};

const SERVICE: &str = "com.shiwei.desktop";
const CHAT_KEY: &str = "openai-compatible-api-key";
const EMBEDDING_KEY: &str = "embedding-api-key";
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
    let (public, key, embedding_key) = resolve_for_request(app, input.clone())?;
    if public.chat_model.is_empty()
        || (public.embedding_mode != "none" && public.embedding_model.is_empty())
    {
        return Err("请选择对话模型和已启用的语义检索模型".into());
    }
    entry(CHAT_KEY)?.set_password(&key).map_err(keyring_error)?;
    if let Some(key) = embedding_key {
        entry(EMBEDDING_KEY)?
            .set_password(&key)
            .map_err(keyring_error)?;
    }
    write_public(&config_path(app)?, &public)?;
    Ok(public)
}

pub fn read_public(app: &AppHandle) -> Result<Option<ProviderPublicConfig>, String> {
    let path = config_path(app)?;
    if !path.is_file() {
        return Ok(None);
    }
    let bytes = fs::read(&path).map_err(|e| format!("无法读取模型配置：{e}"))?;
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|_| "模型配置文件已损坏".into())
}

pub fn read_key() -> Result<Option<String>, String> {
    read_secret(CHAT_KEY)
}
fn read_secret(name: &str) -> Result<Option<String>, String> {
    match entry(name)?.get_password() {
        Ok(value) => Ok(Some(value)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(keyring_error(error)),
    }
}

pub fn runtime_params(app: &AppHandle) -> Result<Option<serde_json::Value>, String> {
    let Some(public) = read_public(app)? else {
        return Ok(None);
    };
    let Some(key) = read_key()? else {
        return Ok(None);
    };
    let embedding_key = if public.embedding_mode == "separate" {
        read_secret(EMBEDDING_KEY)?
    } else {
        None
    };
    Ok(Some(public.worker_params(key, embedding_key)))
}

pub fn status(app: &AppHandle) -> Result<ProviderStatus, String> {
    let public = read_public(app)?;
    let has_api_key = read_key()?.is_some();
    let has_embedding_api_key = public
        .as_ref()
        .is_some_and(|p| p.embedding_mode == "separate")
        && read_secret(EMBEDDING_KEY)?.is_some();
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
    let public = validate(input.public)?;
    let saved = read_public(app)?;
    let key = match input.api_key.filter(|s| !s.trim().is_empty()) {
        Some(key) => key.trim().to_owned(),
        None if saved
            .as_ref()
            .is_some_and(|s| same_chat_endpoint(&public, s)) =>
        {
            read_key()?.ok_or("请输入 API Key")?
        }
        None => return Err("请输入所选厂商的 API Key；切换地址不会复用其他厂商密钥".into()),
    };
    let embedding_key = if public.embedding_mode == "separate" {
        Some(
            match input.embedding_api_key.filter(|s| !s.trim().is_empty()) {
                Some(key) => key.trim().to_owned(),
                None if saved.as_ref().is_some_and(|s| {
                    s.embedding_mode == "separate"
                        && s.embedding_base_url == public.embedding_base_url
                }) =>
                {
                    read_secret(EMBEDDING_KEY)?.ok_or("请输入语义检索服务的 API Key")?
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
    Entry::new(SERVICE, name).map_err(keyring_error)
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
}
