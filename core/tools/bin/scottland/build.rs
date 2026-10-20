use std::env;
use std::process::Command;

fn main() {
    println!("cargo:rerun-if-env-changed=PRODUCT_SOURCE_BUILD_COMMIT");
    let commit = env::var("PRODUCT_SOURCE_BUILD_COMMIT")
        .ok()
        .filter(|value| is_object_id(value))
        .or_else(|| {
            let output = Command::new("git")
                .args(["-C", env!("CARGO_MANIFEST_DIR"), "rev-parse", "HEAD"])
                .output()
                .ok()?;
            if !output.status.success() {
                return None;
            }
            let value = String::from_utf8(output.stdout).ok()?.trim().to_owned();
            is_object_id(&value).then_some(value)
        })
        .unwrap_or_else(|| panic!("product command must be built from a full Git commit"));
    println!("cargo:rustc-env=PRODUCT_SOURCE_BUILD_COMMIT={commit}");
}

fn is_object_id(value: &str) -> bool {
    matches!(value.len(), 40 | 64) && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}
