use super::*;
use crate::paths::Paths;
use std::os::unix::fs::symlink;

#[test]
fn package_owned_source_sync() {
    // Run in a child so Git's ownership simulation and isolated configuration
    // cannot affect other tests. Git still performs the real clone/fetch work.
    if let Some(root) = std::env::var_os("SCOTTLAND_SOURCE_SYNC_FIXTURE") {
        check_source_sync(Path::new(&root));
        return;
    }
    let root = std::env::temp_dir().join(format!("source-sync-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    let (old, new) = crate::tests::create_workspace_repository(&root.join("upstream"));
    fs::write(root.join("old"), &old).unwrap();
    fs::write(root.join("new"), &new).unwrap();
    fs::create_dir(root.join("home")).unwrap();
    fs::write(
        root.join("home/.gitconfig"),
        "[user]\nname = Source Test\nemail = source@example.invalid\n",
    )
    .unwrap();
    let output = Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "user::tests::package_owned_source_sync",
            "--nocapture",
        ])
        .env("SCOTTLAND_SOURCE_SYNC_FIXTURE", &root)
        .env("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", root.join("home/.gitconfig"))
        .env("HOME", root.join("home"))
        .output()
        .unwrap();
    let _ = fs::remove_dir_all(&root);
    assert!(
        output.status.success(),
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn check_source_sync(root: &Path) {
    let old = fs::read_to_string(root.join("old")).unwrap();
    let new = fs::read_to_string(root.join("new")).unwrap();
    let config = fs::read(root.join("home/.gitconfig")).unwrap();
    for existing_clone in [false, true] {
        let prefix = root.join(if existing_clone { "upgrade" } else { "fresh" });
        let paths = Paths::under_root("example", &prefix, prefix.join("state"));
        fs::create_dir_all(&paths.source_repo).unwrap();
        // Publish only the old objects first: the upgrade must fetch new ones.
        git_command(&paths.source_repo, ["init", "--bare"]).unwrap();
        publish_commit(&paths.source_repo, &root.join("upstream"), &old);
        git_command(
            &paths.source_repo,
            ["update-ref", "refs/heads/release", &old],
        )
        .unwrap();
        fs::create_dir_all(paths.switch.parent().unwrap()).unwrap();
        fs::create_dir_all(paths.build_dir(&old)).unwrap();
        symlink(paths.build_dir(&old), &paths.switch).unwrap();
        let system = System { paths };
        if existing_clone {
            prepare_clone(&system, None).unwrap();
            assert_eq!(
                git_text(&system.paths.working_clone, &["rev-parse", "HEAD"]).unwrap(),
                old
            );
        }
        // Package adoption advances the installed build before the user's update.
        fs::remove_file(&system.paths.switch).unwrap();
        fs::create_dir_all(system.paths.build_dir(&new)).unwrap();
        symlink(system.paths.build_dir(&new), &system.paths.switch).unwrap();
        publish_commit(&system.paths.source_repo, &root.join("upstream"), &new);
        git_command(
            &system.paths.source_repo,
            ["update-ref", "refs/heads/release", &new],
        )
        .unwrap();
        let workspace = prepare_clone(&system, None).unwrap();
        settle_local(&system, &workspace, &new).unwrap();
        fetch_commit_from(&system.paths.working_clone, &system.paths.source_repo, &new).unwrap();
        assert_eq!(
            git_text(&system.paths.working_clone, &["rev-parse", "HEAD"]).unwrap(),
            new
        );
        assert!(
            system
                .paths
                .working_clone
                .join("core/tools/later-added-file.txt")
                .is_file()
        );
        assert_eq!(
            git_text(&system.paths.working_clone, &["branch", "--show-current"]).unwrap(),
            "local"
        );

        // Neither the package source nor another user's remote is trusted by
        // subsequent ordinary Git commands or by the explicit --from path.
        let refused = Command::new("git")
            .args(["clone", "--no-checkout"])
            .arg(&system.paths.source_repo)
            .arg(prefix.join("untrusted-clone"))
            .output()
            .unwrap();
        assert!(!refused.status.success());
        assert!(String::from_utf8_lossy(&refused.stderr).contains("dubious ownership"));
        let uninitialized = System {
            paths: Paths::under_root("example", &prefix.join("other"), prefix.join("other-state")),
        };
        let error = fetch_remote_commit(
            &uninitialized.paths.working_clone,
            root.join("upstream").to_str().unwrap(),
            &new,
            true,
            &uninitialized,
        )
        .unwrap_err();
        assert!(error.to_string().contains("dubious ownership"), "{error}");
        let local_config =
            fs::read_to_string(system.paths.working_clone.join(".git/config")).unwrap();
        assert!(!local_config.contains("safe"));
    }
    assert_eq!(fs::read(root.join("home/.gitconfig")).unwrap(), config);
}

// Fixture setup, separate from the package-fetch operation under test.
fn publish_commit(repository: &Path, upstream: &Path, commit: &str) {
    let output = Command::new("git")
        .args(["-c", &format!("safe.directory={}", repository.display())])
        .args(["-c", &format!("safe.directory={}", upstream.display())])
        .arg("-C")
        .arg(repository)
        .args(["fetch", "--no-tags"])
        .arg(upstream)
        .arg(commit)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}
