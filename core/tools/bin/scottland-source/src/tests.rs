use crate::paths::Paths;
use crate::system::System;
use crate::workspace::Workspace;
use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicUsize, Ordering};

static NEXT_SCRATCH: AtomicUsize = AtomicUsize::new(0);

#[test]
fn tool_names_accept_workspace_suffixes_and_binary_names() {
    let workspace = Workspace {
        relative_root: PathBuf::from("core/tools"),
        manifest: PathBuf::from("core/tools/Cargo.toml"),
        bins: BTreeMap::from([
            (
                "example".to_owned(),
                PathBuf::from("core/tools/bin/example"),
            ),
            (
                "example-source".to_owned(),
                PathBuf::from("core/tools/bin/example-source"),
            ),
        ]),
    };

    assert_eq!(
        workspace.resolve_tool("example", "source").unwrap(),
        "example-source"
    );
    assert_eq!(
        workspace.resolve_tool("example", "example-source").unwrap(),
        "example-source"
    );
    assert!(workspace.resolve_tool("example", "missing").is_err());
}

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        let number = NEXT_SCRATCH.fetch_add(1, Ordering::Relaxed);
        let root =
            std::env::temp_dir().join(format!("source-mechanism-{}-{number}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        Self(root)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn git(directory: &Path, args: &[&str]) -> Output {
    Command::new("git")
        .arg("-C")
        .arg(directory)
        .args(args)
        .output()
        .unwrap()
}

fn git_ok(directory: &Path, args: &[&str]) -> String {
    let output = git(directory, args);
    assert!(
        output.status.success(),
        "git {args:?} failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap().trim().to_owned()
}

#[test]
fn installed_loose_ref_symlink_survives_supported_git_operations() {
    let version = Command::new("git").arg("--version").output().unwrap();
    assert!(
        version.status.success(),
        "git --version failed: {}",
        String::from_utf8_lossy(&version.stderr)
    );
    println!(
        "A7 Git version: {}",
        String::from_utf8_lossy(&version.stdout).trim()
    );

    let scratch = Scratch::new();
    let source = scratch.0.join("source");
    let product_root = scratch.0.join("root");
    fs::create_dir_all(&source).unwrap();
    fs::create_dir_all(&product_root).unwrap();
    let paths = Paths::under_root("example", &product_root, product_root.join("state"));
    let bare = paths.source_repo.clone();
    fs::create_dir_all(bare.parent().unwrap()).unwrap();
    assert!(
        Command::new("git")
            .arg("-C")
            .arg(&source)
            .args(["init"])
            .status()
            .unwrap()
            .success()
    );
    git_ok(&source, &["config", "user.name", "Source Mechanism Test"]);
    git_ok(
        &source,
        &["config", "user.email", "source-mechanism@example.invalid"],
    );
    fs::write(source.join("source.txt"), "base\n").unwrap();
    git_ok(&source, &["add", "source.txt"]);
    git_ok(&source, &["commit", "-m", "base"]);
    let commit = git_ok(&source, &["rev-parse", "HEAD"]);

    assert!(
        Command::new("git")
            .arg("-C")
            .arg(scratch.0.as_path())
            .args(["-c", "init.defaultRefFormat=files", "init", "--bare"])
            .arg(&bare)
            .status()
            .unwrap()
            .success()
    );
    git_ok(&bare, &["config", "gc.auto", "0"]);
    git_ok(&bare, &["config", "gc.packRefs", "false"]);
    git_ok(
        &bare,
        &["fetch", "--no-tags", source.to_str().unwrap(), &commit],
    );

    let build = paths.build_dir(&commit);
    fs::create_dir_all(&build).unwrap();
    fs::create_dir_all(paths.switch.parent().unwrap()).unwrap();
    git_ok(
        &bare,
        &[
            "worktree",
            "add",
            "--detach",
            paths.build_source(&commit).to_str().unwrap(),
            &commit,
        ],
    );
    fs::write(paths.switch_commit_for(&commit), format!("{commit}\n")).unwrap();
    symlink(&build, &paths.switch).unwrap();
    fs::create_dir_all(paths.installed_ref().parent().unwrap()).unwrap();
    symlink(paths.switch_commit(), paths.installed_ref()).unwrap();

    assert_eq!(git_ok(&bare, &["rev-parse", "installed"]), commit);

    let packed = bare.join("packed-refs");
    fs::write(
        &packed,
        format!("0000000000000000000000000000000000000000 refs/heads/installed\n"),
    )
    .unwrap();
    assert_eq!(git_ok(&bare, &["rev-parse", "installed"]), commit);
    fs::remove_file(&packed).unwrap();

    let installed_link = paths.installed_ref();
    let expected_target = paths.switch_commit();
    git_ok(
        &bare,
        &["fetch", "--no-tags", source.to_str().unwrap(), &commit],
    );
    assert_symlink_target(&installed_link, &expected_target);
    assert_eq!(git_ok(&bare, &["rev-parse", "installed"]), commit);

    let second_worktree = scratch.0.join("second-worktree");
    git_ok(
        &bare,
        &[
            "worktree",
            "add",
            "--detach",
            second_worktree.to_str().unwrap(),
            &commit,
        ],
    );
    assert_symlink_target(&installed_link, &expected_target);
    git_ok(
        &bare,
        &[
            "worktree",
            "remove",
            "--force",
            second_worktree.to_str().unwrap(),
        ],
    );

    git_ok(&bare, &["gc"]);
    assert_symlink_target(&installed_link, &expected_target);
    assert_eq!(git_ok(&bare, &["rev-parse", "installed"]), commit);
}

#[test]
fn package_removal_drops_switch_before_mechanism_links() {
    let scratch = Scratch::new();
    let paths = Paths::under_root("example", &scratch.0, scratch.0.join("state"));
    let commit = "0123456789012345678901234567890123456789";
    let build = paths.build_dir(commit);
    fs::create_dir_all(build.join("bin")).unwrap();
    fs::create_dir_all(paths.switch.parent().unwrap()).unwrap();
    fs::create_dir_all(&paths.tool_bin).unwrap();
    symlink(&build, &paths.switch).unwrap();
    symlink(
        paths.tool_target("example-tool"),
        paths.tool_link("example-tool"),
    )
    .unwrap();

    let system = System {
        paths: paths.clone(),
    };
    let report = system
        .remove_switch_before_links(Some(commit.to_owned()))
        .unwrap();

    assert_eq!(report.removed, ["switch", "link example-tool"]);
    assert!(fs::symlink_metadata(&paths.switch).is_err());
    assert!(fs::symlink_metadata(paths.tool_link("example-tool")).is_err());
}

fn assert_symlink_target(link: &Path, expected: &Path) {
    assert!(fs::symlink_metadata(link).unwrap().file_type().is_symlink());
    assert_eq!(fs::read_link(link).unwrap(), expected);
}
