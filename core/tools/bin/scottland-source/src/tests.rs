use crate::paths::Paths;
use crate::system::System;
use crate::workspace::Workspace;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::os::unix::fs::{symlink, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::atomic::{AtomicUsize, Ordering};

static NEXT_SCRATCH: AtomicUsize = AtomicUsize::new(0);

fn write_executable(path: &Path, contents: &str) {
    fs::write(path, contents).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}

fn stage_packaged_tools(system: &System, binaries: &Path, names: &[&str]) {
    fs::create_dir_all(&system.paths.base_tools).unwrap();
    for name in names {
        let source = binaries.join(name);
        let staged = system.paths.base_tools.join(name);
        fs::copy(&source, &staged).unwrap();
        fs::set_permissions(&staged, fs::metadata(source).unwrap().permissions()).unwrap();
    }
}

fn package_system(root: &Path) -> System {
    System {
        paths: Paths::under_root("example", root, root.join("state")),
    }
}

#[test]
fn tool_names_accept_workspace_suffixes_and_binary_names() {
    let workspace = Workspace {
        source_cli: "example-source".to_owned(),
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

#[test]
fn source_build_checks_all_outputs_and_only_source_cli_identity() {
    let scratch = Scratch::new();
    let commit = "0123456789abcdef0123456789abcdef01234567";
    let source_cli = scratch.0.join("example-source");
    write_executable(
        &source_cli,
        &format!("#!/bin/sh\nprintf 'example-source {commit}\\n'\n"),
    );
    let tool = scratch.0.join("example-tool");
    write_executable(&tool, "#!/bin/sh\necho 'unknown option: --version' >&2\nexit 2\n");

    let workspace = Workspace {
        source_cli: "example-source".to_owned(),
        relative_root: PathBuf::from("core/tools"),
        manifest: PathBuf::from("core/tools/Cargo.toml"),
        bins: BTreeMap::from([
            ("example-source".to_owned(), PathBuf::from("source")),
            ("example-tool".to_owned(), PathBuf::from("tool")),
        ]),
    };

    workspace
        .verify_build_outputs(&scratch.0, commit)
        .expect("a source-built tool without a commit-bearing --version should be accepted");

    let error = workspace
        .verify_build_outputs(&scratch.0, "abcdef0123456789abcdef0123456789abcdef01")
        .expect_err("the source CLI must still identify the exact build commit");
    assert!(error.to_string().contains("source CLI"));
    assert!(error.to_string().contains("did not report build commit"));

    fs::remove_file(&tool).unwrap();
    let error = workspace
        .verify_build_outputs(&scratch.0, commit)
        .expect_err("every declared binary must still be present in the build output");
    assert!(error.to_string().contains("did not produce"));

    fs::write(&tool, "not executable").unwrap();
    let error = workspace
        .verify_build_outputs(&scratch.0, commit)
        .expect_err("every declared binary must still be executable");
    assert!(error.to_string().contains("non-executable"));
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
        "[workspace]\nmembers = [\"bin/example-source\", \"bin/example-available-tool\"]\nresolver = \"2\"\n",
    )
    .unwrap();
    fs::write(
        source_crate.join("Cargo.toml"),
        "[package]\nname = \"example-source\"\nversion = \"0.1.0\"\nedition = \"2021\"\n\n[[bin]]\nname = \"example-tool\"\npath = \"src/main.rs\"\n",
    )
    .unwrap();
    fs::write(source_crate.join("src/main.rs"), "fn main() {}\n").unwrap();
    let available_crate = workspace.join("bin/example-available-tool");
    fs::create_dir_all(available_crate.join("src")).unwrap();
    fs::write(
        available_crate.join("Cargo.toml"),
        "[package]\nname = \"example-available-tool\"\nversion = \"0.1.0\"\nedition = \"2021\"\n",
    )
    .unwrap();
    fs::write(available_crate.join("src/main.rs"), "fn main() {}\n").unwrap();
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
    assert!(Command::new("git")
        .arg("-C")
        .arg(&source)
        .args(["init"])
        .status()
        .unwrap()
        .success());
    git_ok(&source, &["config", "user.name", "Source Mechanism Test"]);
    git_ok(
        &source,
        &["config", "user.email", "source-mechanism@example.invalid"],
    );
    fs::write(source.join("source.txt"), "base\n").unwrap();
    git_ok(&source, &["add", "source.txt"]);
    git_ok(&source, &["commit", "-m", "base"]);
    let commit = git_ok(&source, &["rev-parse", "HEAD"]);

    assert!(Command::new("git")
        .arg("-C")
        .arg(scratch.0.as_path())
        .args(["-c", "init.defaultRefFormat=files", "init", "--bare"])
        .arg(&bare)
        .status()
        .unwrap()
        .success());
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
fn packaged_source_cli_must_report_the_package_commit() {
    let scratch = Scratch::new();
    let system = package_system(&scratch.0.join("package-root"));
    fs::create_dir_all(&system.paths.base_tools).unwrap();
    let commit = "0123456789abcdef0123456789abcdef01234567";
    let binary = system.paths.base_tools.join("example-source");
    write_executable(
        &binary,
        &format!("#!/bin/sh\nprintf 'example-source {commit}\\n'\n"),
    );

    system.verify_packaged_source_identity(commit).unwrap();

    let error = system
        .verify_packaged_source_identity("abcdef0123456789abcdef0123456789abcdef01")
        .expect_err("a mismatched packaged CLI must be refused");
    assert!(error.to_string().contains("does not report source commit"));
}

#[test]
fn missing_packaged_source_cli_is_refused() {
    let scratch = Scratch::new();
    let system = package_system(&scratch.0.join("package-root"));
    fs::create_dir_all(&system.paths.base_tools).unwrap();

    let error = system
        .verify_packaged_source_identity("0123456789abcdef0123456789abcdef01234567")
        .expect_err("an absent packaged CLI must be refused");
    assert!(error
        .to_string()
        .contains("cannot inspect packaged source CLI"));
}

#[test]
fn unavailable_packaged_tools_are_named_and_filtered() {
    let scratch = Scratch::new();
    let system = package_system(&scratch.0.join("package-root"));
    fs::create_dir_all(&system.paths.base_tools).unwrap();
    let expected = BTreeSet::from([
        "example-source".to_owned(),
        "example-available".to_owned(),
        "example-missing".to_owned(),
        "example-not-executable".to_owned(),
    ]);
    write_executable(
        &system.paths.base_tools.join("example-source"),
        "#!/bin/sh\nexit 0\n",
    );
    write_executable(
        &system.paths.base_tools.join("example-available"),
        "#!/bin/sh\nexit 0\n",
    );
    fs::write(
        system.paths.base_tools.join("example-not-executable"),
        "#!/bin/sh\nexit 0\n",
    )
    .unwrap();
    fs::set_permissions(
        system.paths.base_tools.join("example-not-executable"),
        fs::Permissions::from_mode(0o644),
    )
    .unwrap();

    let (available, warnings) = system.available_packaged_tools(&expected).unwrap();
    assert_eq!(
        available,
        BTreeSet::from(["example-source".to_owned(), "example-available".to_owned()])
    );
    assert!(warnings
        .iter()
        .any(|warning| { warning.contains("example-missing") && warning.contains("is missing") }));
    assert!(warnings.iter().any(|warning| {
        warning.contains("example-not-executable") && warning.contains("is not executable")
    }));
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
fn missing_packaged_source_cli_is_rejected_without_changing_installed_build() {
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

    let system = System {
        paths: paths.clone(),
    };
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
        .adopt_release(&release, &empty_binaries, &BTreeSet::new())
        .expect_err("missing source CLI must still be refused");
    assert!(
        error
            .to_string()
            .contains("packaged source CLI example-source"),
        "{error}"
    );
    assert_eq!(system.base().unwrap(), Some(installed.clone()));
    assert_eq!(system.installed().unwrap(), Some(installed));
}

#[test]
fn adopt_prepares_received_release_when_local_commits_prevent_activation() {
    let scratch = Scratch::new();
    let source = scratch.0.join("source");
    let (base, installed) = create_workspace_repository(&source);
    git_ok(&source, &["checkout", "-b", "package-release", &base]);
    fs::write(source.join("release-only.txt"), "package release\n").unwrap();
    git_ok(&source, &["add", "--all"]);
    git_ok(&source, &["commit", "-m", "package release"]);
    let release = git_ok(&source, &["rev-parse", "HEAD"]);
    git_ok(&source, &["checkout", "main"]);

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
        &["update-ref", "refs/heads/base", &base],
    );

    let active_build = paths.build_dir(&installed);
    fs::create_dir_all(&active_build).unwrap();
    fs::write(
        paths.switch_commit_for(&installed),
        format!("{installed}\n"),
    )
    .unwrap();
    fs::create_dir_all(paths.switch.parent().unwrap()).unwrap();
    symlink(&active_build, &paths.switch).unwrap();

    let binaries = scratch.0.join("release-binaries");
    fs::create_dir_all(&binaries).unwrap();
    for name in ["example-source", "example-available-tool"] {
        let tool = binaries.join(name);
        fs::write(
            &tool,
            format!("#!/bin/sh\nprintf '{name} version {release}\\n'\n"),
        )
        .unwrap();
        fs::set_permissions(&tool, fs::Permissions::from_mode(0o755)).unwrap();
    }

    let system = System {
        paths: paths.clone(),
    };
    assert_eq!(system.base().unwrap(), Some(base.clone()));
    assert_eq!(system.installed().unwrap(), Some(installed.clone()));

    let expected_tools =
        crate::workspace::bin_names_at(&paths.source_repo, "example", &release).unwrap();
    stage_packaged_tools(
        &system,
        &binaries,
        &["example-source", "example-available-tool"],
    );
    let (available_tools, warnings) = system.available_packaged_tools(&expected_tools).unwrap();
    assert!(warnings.iter().any(|warning| {
        warning.contains("example-tool")
            && warning.contains("is missing")
            && warning.contains("will be disabled")
    }));
    assert_eq!(
        available_tools,
        BTreeSet::from([
            "example-source".to_owned(),
            "example-available-tool".to_owned(),
        ])
    );

    let activated = system
        .adopt_release(&release, &binaries, &available_tools)
        .unwrap();
    assert!(
        activated.is_none(),
        "local commits should prevent activation"
    );
    assert_eq!(system.base().unwrap(), Some(base));
    assert_eq!(system.installed().unwrap(), Some(installed));

    let release_build = paths.build_dir(&release);
    assert_eq!(
        fs::read_to_string(paths.switch_commit_for(&release)).unwrap(),
        format!("{release}\n")
    );
    assert_eq!(
        git_ok(&paths.build_source(&release), &["rev-parse", "HEAD"]),
        release
    );
    assert!(release_build.is_dir());
    for name in ["example-source", "example-available-tool"] {
        let installed_tool = paths.build_bin(&release).join(name);
        assert!(
            installed_tool.is_file(),
            "prepared release is missing {name}"
        );
        assert_ne!(
            fs::metadata(installed_tool).unwrap().permissions().mode() & 0o111,
            0,
            "prepared release tool {name} is not executable"
        );
    }
    assert!(!paths.build_bin(&release).join("example-tool").exists());
    let output = Command::new(paths.build_bin(&release).join("example-available-tool"))
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap().trim(),
        format!("example-available-tool version {release}")
    );
}

#[test]
fn package_adoption_disables_and_restores_tools_in_a_cached_build() {
    let scratch = Scratch::new();
    let source = scratch.0.join("source");
    let (release, _) = create_workspace_repository(&source);
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

    let binaries = scratch.0.join("release-binaries");
    fs::create_dir_all(&binaries).unwrap();
    for name in ["example-source", "example-tool", "example-available-tool"] {
        write_executable(
            &binaries.join(name),
            &format!("#!/bin/sh\nprintf '{name} available\\n'\n"),
        );
    }

    let system = System {
        paths: paths.clone(),
    };
    let expected_tools =
        crate::workspace::bin_names_at(&paths.source_repo, "example", &release).unwrap();

    let build = paths.build_dir(&release);
    fs::create_dir_all(&build).unwrap();
    let worktree = paths.build_source(&release);
    let added = Command::new("git")
        .arg("-C")
        .arg(&paths.source_repo)
        .args(["worktree", "add", "--detach"])
        .arg(&worktree)
        .arg(&release)
        .output()
        .unwrap();
    assert!(
        added.status.success(),
        "git worktree add failed: {}",
        String::from_utf8_lossy(&added.stderr)
    );
    let build_bin = paths.build_bin(&release);
    fs::create_dir_all(&build_bin).unwrap();
    for name in ["example-source", "example-tool", "example-available-tool"] {
        let stored = build_bin.join(name);
        fs::copy(binaries.join(name), &stored).unwrap();
        fs::set_permissions(&stored, fs::Permissions::from_mode(0o755)).unwrap();
    }
    fs::write(paths.switch_commit_for(&release), format!("{release}\n")).unwrap();
    git_ok(
        &paths.source_repo,
        &["update-ref", "refs/heads/base", &release],
    );
    fs::create_dir_all(paths.switch.parent().unwrap()).unwrap();
    symlink(&build, &paths.switch).unwrap();
    fs::create_dir_all(&paths.tool_bin).unwrap();
    for name in ["example-source", "example-tool", "example-available-tool"] {
        symlink(paths.tool_target(name), paths.tool_link(name)).unwrap();
    }

    stage_packaged_tools(
        &system,
        &binaries,
        &["example-source", "example-tool", "example-available-tool"],
    );
    let (all_tools, warnings) = system.available_packaged_tools(&expected_tools).unwrap();
    assert!(warnings.is_empty());
    assert_eq!(all_tools, expected_tools);
    assert_eq!(system.installed().unwrap(), Some(release.clone()));
    assert!(paths.tool_link("example-tool").is_file());

    fs::remove_file(paths.base_tools.join("example-tool")).unwrap();
    let (available_tools, warnings) = system.available_packaged_tools(&expected_tools).unwrap();
    assert!(warnings.iter().any(|warning| {
        warning.contains("example-tool")
            && warning.contains("is missing")
            && warning.contains("will be disabled")
    }));
    assert!(
        system
            .adopt_release(&release, &binaries, &available_tools)
            .unwrap()
            .is_none(),
        "the already-installed build should remain active"
    );
    assert_eq!(system.installed_tool_names().unwrap(), available_tools);
    assert!(!paths.tool_link("example-tool").exists());
    assert!(paths.tool_link("example-available-tool").is_file());
    let output = Command::new(paths.tool_link("example-available-tool"))
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap().trim(),
        "example-available-tool available"
    );

    fs::copy(
        binaries.join("example-tool"),
        paths.base_tools.join("example-tool"),
    )
    .unwrap();
    let (restored_tools, warnings) = system.available_packaged_tools(&expected_tools).unwrap();
    assert!(warnings.is_empty());
    assert_eq!(restored_tools, all_tools);
    assert!(
        system
            .adopt_release(&release, &binaries, &restored_tools)
            .unwrap()
            .is_none(),
        "restoring a packaged tool for the active release should not fail"
    );
    assert_eq!(system.installed_tool_names().unwrap(), restored_tools);
    assert!(paths.tool_link("example-tool").is_file());
    let output = Command::new(paths.tool_link("example-tool"))
        .output()
        .unwrap();
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap().trim(),
        "example-tool available"
    );
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
