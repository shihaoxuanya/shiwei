use std::path::{Path, PathBuf};
use tauri::Manager;

pub fn config_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let qa = if cfg!(debug_assertions) { std::env::var_os("SHIWEI_QA_CONFIG_DIR").map(PathBuf::from) } else { None };
    let directory = match qa {
        Some(path) => path,
        None => app.path().app_config_dir().map_err(|_| "无法确定资料库位置配置目录")?,
    };
    path_in(&directory)
}

fn path_in(directory: &Path) -> Result<PathBuf, String> {
    if !directory.is_absolute() { return Err("资料库位置配置目录必须是绝对路径".into()); }
    Ok(directory.join("library-location.json"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pointer_lives_in_external_config_not_in_moving_library() {
        let root = tempfile::tempdir().unwrap();
        assert_eq!(path_in(root.path()).unwrap(), root.path().join("library-location.json"));
        assert!(path_in(Path::new("relative")).is_err());
        assert!(!root.path().join("library-location.json").exists());
    }
}
