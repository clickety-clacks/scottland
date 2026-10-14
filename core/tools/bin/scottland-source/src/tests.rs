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

fn create_workspace_repository(directory: &Path) -> (String, String) {
    fs::create_dir_all(directory).unwrap();
    let initialized = git(directory, &["init", "--initial-branch=main"]);
    assert!(
        initialized.status.success(),
        "git init failed: {}",
        String::from_utf8_lossy(&initialized.stderr)
    );
    git_ok(directory, &["config", "user.name", "Source Mechanism Test"]);
    git_ok(
        directory,
        &["config", "user.email", "source-mechanism@example.invalid"],
    );

    let workspace = directory.join("core/tools");
    let source_crate = workspace.join("bin/example-source");
    fs::create_dir_all(source_crate.join("src")).unwrap();
    fs::write(
        workspace.join("Cargo.toml"),
        "[workspace]\nmembers = [\"bin/example-source\"]\nresolver = \"2\"\n",
    )
    .unwrap();
    fs::write(
        source_crate.join("Cargo.toml"),
        "[package]\nname = \"example-source\"\nversion = \"0.1.0\"\nedition = \"2021\"\n\n[[bin]]\nname = \"example-tool\"\npath = \"src/main.rs\"\n",
    )
    .unwrap();
    fs::write(source_crate.join("src/main.rs"), "fn main() {}\n").unwrap();
    git_ok(directory, &["add", "--all"]);
    git_ok(directory, &["commit", "-m", "base workspace"]);
    let base = git_ok(directory, &["rev-parse", "HEAD"]);

    let added_crate = workspace.join("bin/example-added-tool");
    fs::create_dir_all(added_crate.join("src")).unwrap();
    fs::write(
        workspace.join("Cargo.toml"),
        "[workspace]\nmembers = [\"bin/example-source\", \"bin/example-added-tool\"]\nresolver = \"2\"\n",
    )
    .unwrap();
    fs::write(
        added_crate.join("Cargo.toml"),
        "[package]\nname = \"example-added-tool\"\nversion = \"0.1.0\"\nedition = \"2021\"\n",
    )
    .unwrap();
    fs::write(added_crate.join("src/main.rs"), "fn main() {}\n").unwrap();
    fs::write(workspace.join("later-added-file.txt"), "new after base\n").unwrap();
    git_ok(directory, &["add", "--all"]);
    git_ok(directory, &["commit", "-m", "add another workspace tool"]);
    let installed = git_ok(directory, &["rev-parse", "HEAD"]);
    (base, installed)
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

#[test]
fn adopt_downgrade_preparation_failure_preserves_base() {
    let scratch = Scratch::new();
    let source = scratch.0.join("source");
    let (release, installed) = create_workspace_repository(&source);
    let root = scratch.0.join("system-root");
    let paths = Paths::under_root("example", &root, scratch.0.join("state"));
    fs::create_dir_all(paths.source_repo.parent().unwrap()).unwrap();
    let cloned = Command::new("git")
        .arg("clone")
        .arg("--bare")
        .arg(&source)
        .arg(&paths.source_repo)
        .output()
        .unwrap();
    assert!(
        cloned.status.success(),
        "git clone --bare failed: {}",
        String::from_utf8_lossy(&cloned.stderr)
    );
    git_ok(
        &paths.source_repo,
        &["update-ref", "refs/heads/base", &installed],
    );
    fs::create_dir_all(paths.build_dir(&installed)).unwrap();
    fs::create_dir_all(paths.switch.parent().unwrap()).unwrap();
    symlink(paths.build_dir(&installed), &paths.switch).unwrap();

    let system = System { paths };
    assert_eq!(system.installed().unwrap(), Some(installed.clone()));
    assert_eq!(system.base().unwrap(), Some(installed.clone()));
    let ancestry = git(
        &source,
        &["merge-base", "--is-ancestor", &release, &installed],
    );
    assert!(
        ancestry.status.success(),
        "test release is not a downgrade from installed commit"
    );

    let empty_binaries = scratch.0.join("empty-binaries");
    fs::create_dir_all(&empty_binaries).unwrap();
    let error = system
        .adopt_release(&release, &empty_binaries)
        .expect_err("missing package binary must fail target-store preparation");
    assert!(error.to_string().contains("cannot run"), "{error}");
    assert_eq!(system.base().unwrap(), Some(installed.clone()));
    assert_eq!(system.installed().unwrap(), Some(installed));
}

#[test]
fn rollback_to_base_removes_later_workspace_files_and_tools() {
    let scratch = Scratch::new();
    let repository = scratch.0.join("rollback-repository");
    let (base, later) = create_workspace_repository(&repository);
    assert_eq!(git_ok(&repository, &["rev-parse", "HEAD"]), later);
    let added_tool = repository.join("core/tools/bin/example-added-tool");
    let added_file = repository.join("core/tools/later-added-file.txt");
    assert!(added_tool.exists());
    assert!(added_file.exists());

    crate::user::checkout_scope_from_target(&repository, &base, Path::new("core/tools")).unwrap();

    assert!(!added_tool.exists(), "later-added tool survived rollback");
    assert!(!added_file.exists(), "later-added file survived rollback");
    let base_tree_expr = format!("{base}^{{tree}}");
    let base_tree = git_ok(&repository, &["rev-parse", &base_tree_expr]);
    assert_eq!(git_ok(&repository, &["write-tree"]), base_tree);
    git_ok(&repository, &["diff", "--quiet", &base, "--", "core/tools"]);
}

fn assert_symlink_target(link: &Path, expected: &Path) {
    assert!(fs::symlink_metadata(link).unwrap().file_type().is_symlink());
    assert_eq!(fs::read_link(link).unwrap(), expected);
}
