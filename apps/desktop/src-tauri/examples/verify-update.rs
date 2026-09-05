// Release/QA tool only: matches the official Tauri updater's signature format and verifier.
use base64::{engine::general_purpose::STANDARD, Engine};
use minisign_verify::{PublicKey, Signature};
use std::{env, fs, path::Path};
fn verify(
    artifact: &Path,
    signature: &Path,
    public_config: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let config: serde_json::Value = serde_json::from_slice(&fs::read(public_config)?)?;
    let key = config["plugins"]["updater"]["pubkey"]
        .as_str()
        .ok_or("Missing public key")?;
    let key = String::from_utf8(STANDARD.decode(key.trim())?)?;
    let sig = String::from_utf8(STANDARD.decode(fs::read_to_string(signature)?.trim())?)?;
    PublicKey::decode(&key)?.verify(&fs::read(artifact)?, &Signature::decode(&sig)?, true)?;
    Ok(())
}
fn main() {
    let args: Vec<_> = env::args().collect();
    if args.len() != 4 {
        eprintln!("Usage: verify-update <artifact> <signature> <public-config>");
        std::process::exit(2);
    }
    if verify(
        Path::new(&args[1]),
        Path::new(&args[2]),
        Path::new(&args[3]),
    )
    .is_err()
    {
        eprintln!("Official updater signature verification FAILED");
        std::process::exit(1);
    }
    println!("Official updater signature verification PASSED");
}
