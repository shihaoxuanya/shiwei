fn main() {
    println!("cargo:rerun-if-changed=../../../release.config.json");
    let config: serde_json::Value = std::fs::read_to_string("../../../release.config.json")
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default();
    for (env, key) in [
        ("SHIWEI_ANALYTICS_HOST", "analyticsHost"),
        ("SHIWEI_ANALYTICS_KEY", "analyticsKey"),
    ] {
        println!("cargo:rerun-if-env-changed={env}");
        let value = std::env::var(env)
            .ok()
            .unwrap_or_else(|| config[key].as_str().unwrap_or("").to_owned());
        assert!(
            !value.contains(['\n', '\r']),
            "Invalid public control-plane configuration"
        );
        if env == "SHIWEI_ANALYTICS_KEY" && !value.is_empty() {
            assert!(
                value.starts_with("phc_")
                    && value.chars().all(|ch| ch.is_ascii_alphanumeric() || ch == '_'),
                "Analytics accepts only a public PostHog project key, never a personal/secret token"
            );
        }
        println!("cargo:rustc-env={env}={value}");
    }
    tauri_build::build()
}
