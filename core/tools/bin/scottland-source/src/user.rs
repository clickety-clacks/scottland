use crate::error::{Error, Result};
use crate::git;
use crate::system::System;
use crate::workspace::{Workspace, bin_names_at};
use std::ffi::OsString;
use std::fs;
use std::io::{self, IsTerminal, Write};
use std::path::Path;
use std::process::Command;

pub fn run(system: &System, command: &str, args: &[OsString]) -> Result<()> {
    match command {
        "status" => status(system),
        "edit" => edit(system, args),
        "rollback" => rollback(system, args),
        "update" => update(system, args),
        "help" | "--help" | "-h" => {
            print_help(&system.paths.product);
            Ok(())
        }
        _ => Err(Error::new(format!(
            "unknown command {command:?}; run {0}-source --help",
            system.paths.product
        ))),
    }
}

fn status(system: &System) -> Result<()> {
    let paths = &system.paths;
    let (release, base, installed) = if paths.source_repo.exists() {
        let release = system.release()?;
        let base = system.base()?;
        let installed = system.installed()?;
        println!("release: {}", display_ref(release.as_deref()));
        println!("base: {}", display_ref(base.as_deref()));
        println!("installed: {}", display_ref(installed.as_deref()));
        println!(
            "switch: {}",
            paths
                .switch
                .canonicalize()
                .map(|path| path.display().to_string())
                .unwrap_or_else(|_| "none".to_owned())
        );
        (release, base, installed)
    } else {
        println!("source repository: not initialized");
        (None, None, None)
    };

    if paths.source_repo.exists() {
        if let (Some(base), Some(installed)) = (base.as_deref(), installed.as_deref()) {
            let local = git_text(
                &paths.source_repo,
                &[
                    "log",
                    "--reverse",
                    "--format=%H %s",
                    &format!("{base}..{installed}"),
                ],
            )?;
            if local.is_empty() {
                println!("local commits: none");
            } else {
                println!("local commits:");
                for line in local.lines() {
                    println!("  {line}");
                }
            }
        }

        if let Some(installed) = installed.as_deref() {
            report_binary_versions(system, installed)?;
            if let Some(release) = release.as_deref() {
                let installed_tools = system.installed_tool_names()?;
                let release_tools = bin_names_at(&paths.source_repo, &paths.product, release)?;
                let added = release_tools
                    .difference(&installed_tools)
                    .cloned()
                    .collect::<Vec<_>>();
                let removed = installed_tools
                    .difference(&release_tools)
                    .cloned()
                    .collect::<Vec<_>>();
                if !added.is_empty() {
                    println!("release adds tools not installed: {}", added.join(", "));
                }
                if !removed.is_empty() {
                    println!(
                        "release removes tools not installed: {}",
                        removed.join(", ")
                    );
                }
            }
        }

        let hand_edits = system.inspect_running()?;
        if hand_edits.is_empty() {
            println!("running source: clean");
        } else {
            for item in hand_edits {
                println!("running source: hand-edited: {item}");
            }
        }
        report_release_state(
            system,
            release.as_deref(),
            base.as_deref(),
            installed.as_deref(),
        )?;
    }

    if !paths.working_clone.exists() {
        println!("working clone: absent");
        return Ok(());
    }
    let clone = &paths.working_clone;
    let workspace = Workspace::discover(clone, &paths.product)?;
    let status = git_output(clone, &["status", "--short", "--branch"])?;
    print!(
        "working clone:\n{}",
        String::from_utf8_lossy(&status.stdout)
    );
    if !String::from_utf8_lossy(&status.stdout).ends_with('\n') {
        println!();
    }
    if git_output(clone, &["rev-parse", "--verify", "refs/heads/local"]).is_ok() {
        let local = git_text(clone, &["rev-parse", "refs/heads/local"])?;
        println!("local: {local}");
        if let Some(installed) = installed.as_deref() {
            if is_ancestor(clone, installed, &local)? {
                let count = git_text(
                    clone,
                    &["rev-list", "--count", &format!("{installed}..{local}")],
                )?;
                if count != "0" {
                    println!("pending: {count} local commit(s) not installed");
                    for line in git_text(
                        clone,
                        &["log", "--format=%H %s", &format!("{installed}..{local}")],
                    )?
                    .lines()
                    {
                        println!("  {line}");
                    }
                }
            } else if !is_ancestor(clone, &local, installed)? {
                println!(
                    "local and installed have diverged; run {} update",
                    paths.product
                );
            }
        } else {
            println!(
                "initial source commit {local} is not installed; rerun {} update --init after a successful build",
                paths.product
            );
        }
        let merge_head = git_output(clone, &["rev-parse", "--verify", "MERGE_HEAD"]);
        if merge_head.is_ok() {
            println!(
                "merge in progress: resolve conflicts, then run {} update --continue",
                paths.product
            );
        }
        let dirty = git_output(
            clone,
            &["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        )?;
        if !dirty.stdout.is_empty() {
            match workspace.validate_changes(clone) {
                Ok(()) => println!("uncommitted changes: present inside the product workspace"),
                Err(error) => println!("uncommitted changes: {error}"),
            }
        }
    }
    Ok(())
}

fn report_binary_versions(system: &System, installed: &str) -> Result<()> {
    let mut mismatches = Vec::new();
    for name in system.installed_tool_names()? {
        let binary = system.paths.tool_link(&name);
        let output = Command::new(&binary).arg("--version").output();
        let reports_commit = output.is_ok_and(|output| {
            output.status.success()
                && String::from_utf8_lossy(&output.stdout)
                    .split_whitespace()
                    .last()
                    == Some(installed)
        });
        if !reports_commit {
            mismatches.push(name);
        }
    }
    if mismatches.is_empty() {
        println!("tool versions: every active binary reports {installed}");
    } else {
        println!(
            "tool versions: these binaries do not report installed {installed}: {}",
            mismatches.join(", ")
        );
    }
    Ok(())
}

fn report_release_state(
    system: &System,
    release: Option<&str>,
    base: Option<&str>,
    installed: Option<&str>,
) -> Result<()> {
    let (Some(release), Some(base)) = (release, base) else {
        return Ok(());
    };
    if release != base {
        if is_ancestor(&system.paths.source_repo, base, release)? {
            println!(
                "release {release} is ahead of base; run {} update",
                system.paths.product
            );
        } else if is_ancestor(&system.paths.source_repo, release, base)? {
            println!("release {release} is behind base after a package downgrade");
        } else {
            println!("release {release} is on another line than base {base}");
        }
    }
    if let Some(installed) = installed {
        if !is_ancestor(&system.paths.source_repo, release, installed)?
            && !is_ancestor(&system.paths.source_repo, installed, release)?
        {
            println!("received release {release} is on another line than installed {installed}");
        }
    }
    Ok(())
}

fn edit(system: &System, args: &[OsString]) -> Result<()> {
    let (requested_tool, options) = parse_tool_args(args)?;
    let no_editor = options.contains_key("no-editor");
    let show_path = options.contains_key("path");
    if show_path && (no_editor || options.contains_key("message") || options.contains_key("reason"))
    {
        return Err(Error::new("edit --path does not take other options"));
    }
    if let Some(unexpected) = options
        .keys()
        .find(|key| !["no-editor", "path", "message"].contains(&key.as_str()))
    {
        return Err(Error::new(format!("unknown edit option --{unexpected}")));
    }
    let message = options.get("message").cloned();
    if no_editor && message.is_none() {
        return Err(Error::new("edit --no-editor requires --message"));
    }

    system.require_no_hand_edits()?;
    let installed = system
        .installed()?
        .ok_or_else(|| Error::new("no build is installed; adopt a package or run update --init"))?;
    let workspace = prepare_clone(system, None)?;
    settle_local(system, &workspace, &installed)?;
    let tool = workspace.resolve_tool(&system.paths.product, &requested_tool)?;
    let directory = workspace.tool_directory(&tool)?.to_path_buf();
    if show_path {
        println!("{}", directory.display());
        return Ok(());
    }

    if !no_editor && io::stdin().is_terminal() {
        run_editor(&directory)?;
    } else if message.is_none() {
        return Err(Error::new(
            "no terminal editor is available; use --no-editor --message",
        ));
    }
    workspace.validate_changes(&system.paths.working_clone)?;
    let message = match message {
        Some(message) if !message.trim().is_empty() => message,
        Some(_) => return Err(Error::new("commit message cannot be empty")),
        None => prompt("Why? ")?,
    };
    commit_workspace(system, &workspace, &message, &requested_tool)
}

fn rollback(system: &System, args: &[OsString]) -> Result<()> {
    system.require_no_hand_edits()?;
    let parsed = parse_rollback_args(args)?;
    let installed = system
        .installed()?
        .ok_or_else(|| Error::new("no build is installed; there is nothing to roll back"))?;
    let workspace = prepare_clone(system, None)?;
    settle_local(system, &workspace, &installed)?;
    require_clean(&system.paths.working_clone)?;

    let requested = parsed.to.as_deref();
    let target;
    let scope;
    if let Some(tool) = parsed.tool.as_deref() {
        let tool = workspace.resolve_tool(&system.paths.product, tool)?;
        let tool_directory = workspace.tool_directory(&tool)?;
        let relative = workspace.relative_tool_directory(&system.paths.working_clone, &tool)?;
        target = match requested {
            Some("base") => system
                .base()?
                .ok_or_else(|| Error::new("base ref is missing"))?,
            Some(commit) => resolve_user_commit(&system.paths.working_clone, commit)?,
            None => previous_tool_state(system, &workspace, tool_directory, &relative)?,
        };
        scope = Some(relative);
    } else {
        target = match requested {
            Some("base") => system
                .base()?
                .ok_or_else(|| Error::new("base ref is missing"))?,
            Some(commit) => resolve_user_commit(&system.paths.working_clone, commit)?,
            None => git_text(
                &system.paths.working_clone,
                &["rev-parse", &format!("{installed}^")],
            )
            .map_err(|_| Error::new("installed has no previous commit to roll back to"))?,
        };
        scope = Some(workspace.relative_root.clone());
    }
    let scope = scope.unwrap();
    let mut command = vec![
        OsString::from("checkout"),
        OsString::from(target.as_str()),
        OsString::from("--"),
    ];
    command.push(scope.as_os_str().to_os_string());
    git_command(&system.paths.working_clone, command)?;
    let subject = parsed.tool.as_deref().unwrap_or("workspace");
    let reason = parsed
        .reason
        .map(|reason| format!("; {reason}"))
        .unwrap_or_default();
    let message = format!("Roll back {subject} to {target}{reason}");
    commit_workspace(system, &workspace, &message, subject)
}

fn update(system: &System, args: &[OsString]) -> Result<()> {
    let parsed = parse_update_args(args)?;
    if parsed.abort && (parsed.continue_merge || parsed.from.is_some() || parsed.init) {
        return Err(Error::new(
            "update --abort cannot be combined with other options",
        ));
    }
    if parsed.continue_merge && (parsed.from.is_some() || parsed.init) {
        return Err(Error::new(
            "update --continue cannot be combined with --from or --init",
        ));
    }
    if parsed.init && parsed.from.is_none() {
        return Err(Error::new("update --init requires --from and --ref"));
    }
    if parsed.from.is_some() != parsed.ref_commit.is_some() {
        return Err(Error::new(
            "update --from and --ref must be supplied together",
        ));
    }
    if parsed
        .ref_commit
        .as_deref()
        .is_some_and(|commit| !git::valid_object_id(commit))
    {
        return Err(Error::new(
            "--ref must be a full commit id, not a branch name",
        ));
    }
    if parsed.init && system.paths.source_repo.exists() {
        return Err(Error::new(
            "update --init is only for a machine without a source repository",
        ));
    }

    system.require_no_hand_edits()?;
    if parsed.abort {
        let workspace = prepare_clone(system, None)?;
        git_command(&system.paths.working_clone, ["merge", "--abort"])?;
        println!("update merge aborted; installed build unchanged");
        let _ = workspace;
        return Ok(());
    }

    let release = if let Some(commit) = parsed.ref_commit.as_deref() {
        Some(fetch_remote_commit(
            &system.paths.working_clone,
            parsed.from.as_deref().unwrap(),
            commit,
            parsed.init,
            system,
        )?)
    } else if parsed.init {
        return Err(Error::new("update --init requires --from and --ref"));
    } else {
        system.release()?
    };
    let release = release.ok_or_else(|| Error::new("there is no received release to update to"))?;

    if parsed.init {
        let workspace = Workspace::discover(&system.paths.working_clone, &system.paths.product)?;
        let local = git_text(&system.paths.working_clone, &["rev-parse", "HEAD"])?;
        let bins = workspace
            .build(&system.paths.working_clone, &system.paths.build_target, &local)
            .map_err(|error| Error::new(format!("initial update build failed; source repository and installed build are unchanged: {error}")))?;
        let root_args = vec![
            OsString::from("__install"),
            system.paths.working_clone.as_os_str().to_os_string(),
            bins.as_os_str().to_os_string(),
            OsString::from(local.as_str()),
            OsString::from(release.as_str()),
            OsString::from("--init"),
        ];
        crate::root_call(&system.paths.product, &root_args)?;
        return Ok(());
    }

    let installed = system
        .installed()?
        .ok_or_else(|| Error::new("no build is installed; use update --init"))?;
    let workspace = prepare_clone(system, None)?;
    settle_local(system, &workspace, &installed)?;
    if parsed.continue_merge {
        let merge_release = git_text(
            &system.paths.working_clone,
            &["rev-parse", "--verify", "MERGE_HEAD"],
        )
        .map_err(|_| Error::new("there is no merge to continue"))?;
        let unresolved = git_text(
            &system.paths.working_clone,
            &["diff", "--name-only", "--diff-filter=U"],
        )?;
        if !unresolved.is_empty() {
            return Err(Error::new(format!(
                "merge still has conflicts: {}",
                unresolved.lines().collect::<Vec<_>>().join(", ")
            )));
        }
        git_command(&system.paths.working_clone, ["commit", "--no-edit"])?;
        let local = git_text(&system.paths.working_clone, &["rev-parse", "HEAD"])?;
        let bins = workspace
            .build(&system.paths.working_clone, &system.paths.build_target, &local)
            .map_err(|error| Error::new(format!("update build failed; source repository, installed build and running binaries are unchanged: {error}")))?;
        let root_args = vec![
            OsString::from("__install"),
            system.paths.working_clone.as_os_str().to_os_string(),
            bins.as_os_str().to_os_string(),
            OsString::from(local.as_str()),
            OsString::from(merge_release.as_str()),
        ];
        crate::root_call(&system.paths.product, &root_args)?;
        return Ok(());
    }

    require_clean(&system.paths.working_clone)?;

    if has_merge_head(&system.paths.working_clone)? {
        return Err(Error::new(
            "merge in progress; resolve it and run update --continue or update --abort",
        ));
    }
    let local = git_text(
        &system.paths.working_clone,
        &["rev-parse", "refs/heads/local"],
    )?;
    if parsed.from.is_none() {
        fetch_commit_from(
            &system.paths.working_clone,
            &system.paths.source_repo,
            &release,
        )?;
    }
    if is_ancestor(&system.paths.working_clone, &release, &local)? {
        if parsed.from.is_some() {
            let args = vec![
                OsString::from("__receive-release"),
                system.paths.working_clone.as_os_str().to_os_string(),
                OsString::from(release.as_str()),
            ];
            crate::root_call(&system.paths.product, &args)?;
        } else {
            println!("release {release} is already in local; installed build unchanged");
        }
        return Ok(());
    }

    let merge = git_command(
        &system.paths.working_clone,
        [
            OsString::from("merge"),
            OsString::from("--no-edit"),
            OsString::from(release.as_str()),
        ],
    );
    if let Err(error) = merge {
        let conflicts = git_text(
            &system.paths.working_clone,
            &["diff", "--name-only", "--diff-filter=U"],
        )
        .unwrap_or_default();
        if conflicts.is_empty() {
            return Err(error);
        }
        return Err(Error::new(format!(
            "update merge conflicts in: {}; resolve them and run {} update --continue, or run {} update --abort; installed build and source repository are unchanged",
            conflicts.lines().collect::<Vec<_>>().join(", "),
            system.paths.product,
            system.paths.product
        )));
    }
    let local = git_text(&system.paths.working_clone, &["rev-parse", "HEAD"])?;
    let bins = workspace
        .build(&system.paths.working_clone, &system.paths.build_target, &local)
        .map_err(|error| Error::new(format!("update build failed; source repository, installed build and running binaries are unchanged: {error}")))?;
    let args = vec![
        OsString::from("__install"),
        system.paths.working_clone.as_os_str().to_os_string(),
        bins.as_os_str().to_os_string(),
        OsString::from(local.as_str()),
        OsString::from(release.as_str()),
    ];
    crate::root_call(&system.paths.product, &args)
}

fn fetch_remote_commit(
    clone: &Path,
    remote: &str,
    commit: &str,
    initialize: bool,
    system: &System,
) -> Result<String> {
    if !system.paths.working_clone.exists() {
        if !initialize {
            if !system.paths.source_repo.exists() {
                return Err(Error::new("source repository is absent; use update --init"));
            }
            prepare_clone(system, None)?;
        } else {
            if let Some(parent) = clone.parent() {
                fs::create_dir_all(parent)?;
            }
            git_command(
                Path::new("/"),
                [
                    OsString::from("clone"),
                    OsString::from("--no-checkout"),
                    OsString::from(remote),
                    clone.as_os_str().to_os_string(),
                ],
            )?;
        }
    }
    if !git::valid_object_id(commit) {
        return Err(Error::new("--ref must be a full commit id"));
    }
    git_command(
        clone,
        [
            OsString::from("fetch"),
            OsString::from("--no-tags"),
            OsString::from(remote),
            OsString::from(commit),
        ],
    )?;
    let resolved = git_text(
        clone,
        &["rev-parse", "--verify", &format!("{commit}^{{commit}}")],
    )?;
    if resolved != commit.to_ascii_lowercase() {
        return Err(Error::new(
            "--ref did not resolve to the requested full commit",
        ));
    }
    if initialize {
        if has_local_branch(clone)? {
            let local = git_text(clone, &["rev-parse", "refs/heads/local"])?;
            if local != resolved {
                return Err(Error::new(format!(
                    "initial working clone already has local commit {local}; it was left untouched"
                )));
            }
            if git_text(clone, &["branch", "--show-current"])? != "local" {
                require_clean(clone)?;
                git_command(clone, ["checkout", "local"])?;
            }
            require_clean(clone)?;
        } else {
            require_clean(clone)?;
            git_command(
                clone,
                [
                    OsString::from("checkout"),
                    OsString::from("-b"),
                    OsString::from("local"),
                    OsString::from(resolved.as_str()),
                ],
            )?;
        }
    } else {
        let installed = system
            .installed()?
            .ok_or_else(|| Error::new("no active build; use update --init"))?;
        let _ = prepare_clone(system, None)?;
        settle_local(
            system,
            &Workspace::discover(clone, &system.paths.product)?,
            &installed,
        )?;
    }
    Ok(resolved)
}

fn prepare_clone(system: &System, _: Option<&str>) -> Result<Workspace> {
    let paths = &system.paths;
    if !paths.working_clone.exists() {
        if !paths.source_repo.exists() {
            return Err(Error::new("source repository is not initialized"));
        }
        if let Some(parent) = paths.working_clone.parent() {
            fs::create_dir_all(parent)?;
        }
        git_command(
            Path::new("/"),
            [
                OsString::from("clone"),
                OsString::from("--no-checkout"),
                paths.source_repo.as_os_str().to_os_string(),
                paths.working_clone.as_os_str().to_os_string(),
            ],
        )?;
    }
    let installed = system
        .installed()?
        .ok_or_else(|| Error::new("no active build; use update --init"))?;
    fetch_commit_from(&paths.working_clone, &paths.source_repo, &installed)?;
    if !has_local_branch(&paths.working_clone)? {
        git_command(
            &paths.working_clone,
            [
                OsString::from("checkout"),
                OsString::from("-b"),
                OsString::from("local"),
                OsString::from(installed.as_str()),
            ],
        )?;
    } else {
        let branch = git_text(&paths.working_clone, &["branch", "--show-current"])?;
        if branch != "local" {
            require_clean(&paths.working_clone)?;
            git_command(&paths.working_clone, ["checkout", "local"])?;
        }
    }
    Workspace::discover(&paths.working_clone, &paths.product)
}

fn settle_local(system: &System, _workspace: &Workspace, installed: &str) -> Result<()> {
    let clone = &system.paths.working_clone;
    let local = git_text(clone, &["rev-parse", "refs/heads/local"])?;
    if local == installed || is_ancestor(clone, installed, &local)? {
        return Ok(());
    }
    if is_ancestor(clone, &local, installed)? {
        require_clean(clone)?;
        git_command(
            clone,
            [
                OsString::from("merge"),
                OsString::from("--ff-only"),
                OsString::from(installed),
            ],
        )?;
        return Ok(());
    }
    Err(Error::new(format!(
        "local and installed have diverged; run {} update to merge pending work onto the installed line",
        system.paths.product
    )))
}

fn commit_workspace(
    system: &System,
    workspace: &Workspace,
    message: &str,
    subject: &str,
) -> Result<()> {
    let clone = &system.paths.working_clone;
    if message.trim().is_empty() {
        return Err(Error::new("commit message cannot be empty"));
    }
    workspace.validate_changes(clone)?;
    let workspace_path = if workspace.relative_root.as_os_str().is_empty() {
        ".".to_owned()
    } else {
        workspace.relative_root.to_string_lossy().into_owned()
    };
    git_command(
        clone,
        [
            OsString::from("add"),
            OsString::from("-A"),
            OsString::from("--"),
            OsString::from(workspace_path),
        ],
    )?;
    let staged = git::git_status(
        clone,
        &[
            OsString::from("diff"),
            OsString::from("--cached"),
            OsString::from("--quiet"),
        ],
    )?;
    match staged.status.code() {
        Some(0) => return Err(Error::new(format!("no workspace changes to {subject}"))),
        Some(1) => {}
        _ => {
            return Err(Error::new(format!(
                "could not inspect staged workspace changes: {}",
                String::from_utf8_lossy(&staged.stderr).trim()
            )));
        }
    }
    let parent = git_text(clone, &["rev-parse", "HEAD"])?;
    git_command(
        clone,
        [
            OsString::from("commit"),
            OsString::from("-m"),
            OsString::from(message),
        ],
    )?;
    let commit = git_text(clone, &["rev-parse", "HEAD"])?;
    let build = workspace.build(clone, &system.paths.build_target, &commit);
    let binaries = match build {
        Ok(binaries) => binaries,
        Err(error) => {
            git_command(
                clone,
                [
                    OsString::from("reset"),
                    OsString::from("--mixed"),
                    OsString::from(parent.as_str()),
                ],
            )?;
            return Err(Error::new(format!(
                "{subject} build failed; the change is uncommitted for fixing, and the source repository, installed build and running binaries are unchanged: {error}"
            )));
        }
    };
    let args = vec![
        OsString::from("__install"),
        clone.as_os_str().to_os_string(),
        binaries.as_os_str().to_os_string(),
        OsString::from(commit.as_str()),
    ];
    crate::root_call(&system.paths.product, &args)
}

fn previous_tool_state(
    system: &System,
    workspace: &Workspace,
    tool_directory: &Path,
    relative: &Path,
) -> Result<String> {
    let clone = &system.paths.working_clone;
    let base = system
        .base()?
        .ok_or_else(|| Error::new("base ref is missing"))?;
    let commits = git_text(
        clone,
        &[
            "log",
            "--format=%H",
            "-n",
            "2",
            &format!("{base}..local"),
            "--",
            &relative.to_string_lossy(),
        ],
    )?;
    let mut commits = commits.lines();
    let latest = commits.next();
    if latest.is_none() {
        return Err(Error::new("no commit has changed this tool"));
    }
    if let Some(previous) = commits.next() {
        return Ok(previous.to_owned());
    }
    let _ = (workspace, tool_directory);
    Ok(base)
}

fn run_editor(directory: &Path) -> Result<()> {
    let editor = std::env::var_os("VISUAL")
        .filter(|value| !value.is_empty())
        .or_else(|| std::env::var_os("EDITOR").filter(|value| !value.is_empty()))
        .ok_or_else(|| Error::new("VISUAL and EDITOR are unset; use --no-editor --message"))?;
    let editor = editor.to_string_lossy();
    let status = Command::new("sh")
        .arg("-c")
        .arg("exec $EDITOR \"$1\"")
        .arg("source-editor")
        .arg(directory)
        .env("EDITOR", editor.as_ref())
        .status()
        .map_err(|error| Error::new(format!("could not start editor: {error}")))?;
    if status.success() {
        Ok(())
    } else {
        Err(Error::new(
            "editor exited unsuccessfully; working clone was left unchanged by the mechanism",
        ))
    }
}

fn prompt(prompt: &str) -> Result<String> {
    if !io::stdin().is_terminal() {
        return Err(Error::new(
            "a commit message is required when no terminal is available",
        ));
    }
    print!("{prompt}");
    io::stdout().flush()?;
    let mut message = String::new();
    io::stdin().read_line(&mut message)?;
    if message.trim().is_empty() {
        return Err(Error::new("commit message cannot be empty"));
    }
    Ok(message.trim().to_owned())
}

fn parse_tool_args(
    args: &[OsString],
) -> Result<(String, std::collections::BTreeMap<String, String>)> {
    let mut iter = args.iter();
    let tool = iter
        .next()
        .and_then(|arg| arg.to_str())
        .ok_or_else(|| Error::new("edit requires a tool name"))?
        .to_owned();
    if tool.is_empty() || tool.contains('/') || tool.starts_with('-') {
        return Err(Error::new("tool name is invalid"));
    }
    let mut options = std::collections::BTreeMap::new();
    while let Some(arg) = iter.next() {
        let name = arg
            .to_str()
            .and_then(|arg| arg.strip_prefix("--"))
            .ok_or_else(|| Error::new("unexpected positional argument"))?;
        let value = if ["message"].contains(&name) {
            iter.next()
                .and_then(|value| value.to_str())
                .ok_or_else(|| Error::new(format!("--{name} requires a value")))?
                .to_owned()
        } else {
            String::new()
        };
        if options.insert(name.to_owned(), value).is_some() {
            return Err(Error::new(format!("--{name} was supplied more than once")));
        }
    }
    Ok((tool, options))
}

#[derive(Default)]
struct RollbackArgs {
    tool: Option<String>,
    to: Option<String>,
    reason: Option<String>,
}

fn parse_rollback_args(args: &[OsString]) -> Result<RollbackArgs> {
    let mut result = RollbackArgs::default();
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        let value = arg
            .to_str()
            .ok_or_else(|| Error::new("arguments must be plain text"))?;
        match value {
            "--to" => result.to = Some(next_text(&mut iter, "--to")?),
            "--reason" => result.reason = Some(next_text(&mut iter, "--reason")?),
            _ if value.starts_with('-') => {
                return Err(Error::new(format!("unknown rollback option {value}")));
            }
            _ if result.tool.is_none() => result.tool = Some(value.to_owned()),
            _ => return Err(Error::new("rollback accepts at most one tool")),
        }
    }
    if let Some(tool) = result.tool.as_deref() {
        if tool.contains('/') || tool.is_empty() {
            return Err(Error::new("tool name is invalid"));
        }
    }
    Ok(result)
}

#[derive(Default)]
struct UpdateArgs {
    from: Option<String>,
    ref_commit: Option<String>,
    init: bool,
    continue_merge: bool,
    abort: bool,
}

fn parse_update_args(args: &[OsString]) -> Result<UpdateArgs> {
    let mut result = UpdateArgs::default();
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        let value = arg
            .to_str()
            .ok_or_else(|| Error::new("arguments must be plain text"))?;
        match value {
            "--from" => result.from = Some(next_text(&mut iter, "--from")?),
            "--ref" => result.ref_commit = Some(next_text(&mut iter, "--ref")?),
            "--init" => result.init = true,
            "--continue" => result.continue_merge = true,
            "--abort" => result.abort = true,
            _ => return Err(Error::new(format!("unknown update option {value}"))),
        }
    }
    Ok(result)
}

fn next_text<'a>(iter: &mut impl Iterator<Item = &'a OsString>, option: &str) -> Result<String> {
    iter.next()
        .and_then(|value| value.to_str())
        .map(str::to_owned)
        .ok_or_else(|| Error::new(format!("{option} requires a value")))
}

fn fetch_commit_from(clone: &Path, remote: &Path, commit: &str) -> Result<()> {
    git_command(
        clone,
        [
            OsString::from("fetch"),
            OsString::from("--no-tags"),
            remote.as_os_str().to_os_string(),
            OsString::from(commit),
        ],
    )?;
    Ok(())
}

fn require_clean(clone: &Path) -> Result<()> {
    let status = git_output(
        clone,
        &["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    )?;
    if status.stdout.is_empty() {
        Ok(())
    } else {
        Err(Error::new(
            "working clone has uncommitted changes; they were left untouched",
        ))
    }
}

fn has_local_branch(clone: &Path) -> Result<bool> {
    Ok(git_output(
        clone,
        &["show-ref", "--verify", "--quiet", "refs/heads/local"],
    )
    .is_ok())
}

fn has_merge_head(clone: &Path) -> Result<bool> {
    Ok(git_output(clone, &["rev-parse", "--verify", "MERGE_HEAD"]).is_ok())
}

fn resolve_user_commit(clone: &Path, commit: &str) -> Result<String> {
    if !git::valid_object_id(commit) {
        return Err(Error::new("--to must name a full commit id or base"));
    }
    let resolved = git_text(
        clone,
        &["rev-parse", "--verify", &format!("{commit}^{{commit}}")],
    )?;
    if resolved != commit.to_ascii_lowercase() {
        return Err(Error::new(
            "--to did not resolve to the requested full commit",
        ));
    }
    Ok(resolved)
}

fn is_ancestor(repository: &Path, ancestor: &str, descendant: &str) -> Result<bool> {
    let output = git::git_status(
        repository,
        &[
            OsString::from("merge-base"),
            OsString::from("--is-ancestor"),
            OsString::from(ancestor),
            OsString::from(descendant),
        ],
    )?;
    match output.status.code() {
        Some(0) => Ok(true),
        Some(1) => Ok(false),
        _ => Err(Error::new(format!(
            "git could not compare ancestry of {ancestor} and {descendant}: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ))),
    }
}

fn git_output(repository: &Path, args: &[&str]) -> Result<std::process::Output> {
    git::git_status(
        repository,
        &args.iter().map(OsString::from).collect::<Vec<_>>(),
    )
    .map_err(|error| Error::new(format!("could not run git: {error}")))
    .and_then(|output| {
        if output.status.success() {
            Ok(output)
        } else {
            Err(Error::new(format!(
                "git {} failed: {}",
                args.join(" "),
                String::from_utf8_lossy(&output.stderr).trim()
            )))
        }
    })
}

fn git_command<I, S>(repository: &Path, args: I) -> Result<()>
where
    I: IntoIterator<Item = S>,
    S: AsRef<std::ffi::OsStr>,
{
    git::git_command(repository, args).map(|_| ())
}

fn git_text(repository: &Path, args: &[&str]) -> Result<String> {
    git::git_text(repository, args)
}

fn display_ref(reference: Option<&str>) -> &str {
    reference.unwrap_or("none")
}

fn print_help(product: &str) {
    println!(
        "usage: {product}-source <command>\n\
         \n\
         commands:\n\
           status\n\
           edit <tool> [--path | --no-editor --message <why>]\n\
           rollback [<tool>] [--to <commit|base>] [--reason <why>]\n\
           update [--continue | --abort] [--from <repository> --ref <full-commit> [--init]]"
    );
}
