// A release is a Windows GUI application, not a console process. Keep the
// development console available to `tauri dev` for diagnostics.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    shiwei_desktop_lib::run();
}
