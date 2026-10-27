use crate::error::{Error, Result};
use crate::git;
use crate::paths::Paths;
use crate::workspace::Workspace;
use std::collections::BTreeSet;
use std::env;
use std::ffi::{OsStr, OsString};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{symlink, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;

#[derive(Clone, Debug)]
pub struct System {
    pub paths: Paths,
}

#[derive(Clone, Debug, Default)]
pub struct ReconcileReport {
    pub repairs: Vec<String>,
    pub hand_edits: Vec<String>,
    pub installed: Option<String>,
    pub release: Option<String>,
    pub base: Option<String>,
    pub switch_target: Option<PathBuf>,
}

#[derive(Clone, Debug, Default)]
pub struct RemovalReport {
    pub old_installed: Option<String>,
    pub removed: Vec<String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum BinaryOrigin {
    Package,
    UserBuild,
}

impl System {
    pub fn for_user(product: &str) -> Result<Self> {
        Ok(Self {
            paths: Paths::system(product)?,
        })
    }

    pub fn for_root(product: &str) -> Result<Self> {
        Ok(Self {
            paths: Paths::system_root(product)?,
        })
    }

    pub fn reconcile(&self) -> Result<ReconcileReport> {
        let mut report = ReconcileReport::default();
        match fs::symlink_metadata(&self.paths.source_repo) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(report),
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
                return Err(Error::new(
                    "source repository path is not a real directory; it was left untouched",
                ));
            }
            Ok(_) => {}
            Err(error) => return Err(error.into()),
        }
        self.require_bare_repository()?;
        if self.ensure_installed_ref()? {
            report
                .repairs
                .push("restored installed ref as a symlink to current/commit".to_owned());
        }
        if self.remove_packed_installed()? {
            report
                .repairs
                .push("removed packed installed ref entry".to_owned());
        }

        let switch = self.switch_target()?;
        report.switch_target = switch.clone();
        if let Some(directory) = switch.as_ref() {
            let state = self.inspect_running_build(directory)?;
            report.installed = Some(state.commit.clone());
            report.hand_edits = state.hand_edits;
            if let Ok(commit_file) = fs::read_to_string(directory.join("commit")) {
                if commit_file == format!("{}\n", state.commit)
                    && self.ref_value("installed")?.as_deref() != Some(state.commit.as_str())
                {
                    return Err(Error::new(
                        "A7 failed: Git did not resolve the installed symlink to current/commit; stopped without a ref-writing fallback",
                    ));
                }
            }
        }

        report.release = self.ref_value("release")?;
        report.base = self.ref_value("base")?;
        if let (Some(installed), Some(release), Some(base)) = (
            report.installed.as_deref(),
            report.release.as_deref(),
            report.base.as_deref(),
        ) {
            let derived = if installed == release {
                Some(release)
            } else if self.is_ancestor(release, installed)? && self.is_ancestor(base, release)? {
                Some(release)
            } else {
                None
            };
            if let Some(derived) = derived {
                if derived != base {
                    self.set_ref("base", derived)?;
                    report.base = Some(derived.to_owned());
                    report.repairs.push(format!("base updated to {derived}"));
                }
            }
        }

        match switch {
            Some(directory) => {
                let expected = self.bin_names_from_store(&directory)?;
                self.reconcile_tool_links(&expected, &mut report.repairs)?;
            }
            None => self.remove_current_links(&mut report.repairs)?,
        }
        Ok(report)
    }

    /// Read-only reconciliation preview used so an ordinary command can avoid sudo when no
    /// root-owned state needs repair.
    pub fn needs_reconcile(&self) -> Result<bool> {
        match fs::symlink_metadata(&self.paths.source_repo) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
                return Err(Error::new(
                    "source repository path is not a real directory; it was left untouched",
                ));
            }
            Ok(_) => {}
            Err(error) => return Err(error.into()),
        }
        let bare = git::git_text(
            &self.paths.source_repo,
            &["rev-parse", "--is-bare-repository"],
        )?;
        if bare != "true" || self.paths.source_repo.join("reftable").exists() {
            return Err(Error::new(
                "source repository is not a bare files-ref repository; it was left untouched",
            ));
        }
        if config_value(&self.paths.source_repo, "gc.auto")?.as_deref() != Some("0")
            || config_value(&self.paths.source_repo, "gc.packRefs")?.as_deref() != Some("false")
            || tree_needs_readable(&self.paths.source_repo)?
        {
            return Ok(true);
        }
        if let Some(parent) = self.paths.source_repo.parent() {
            if mode_needs_readable(parent)? {
                return Ok(true);
            }
        }
        let installed_ref = self.paths.installed_ref();
        let installed_is_link = fs::symlink_metadata(&installed_ref)
            .is_ok_and(|metadata| metadata.file_type().is_symlink())
            && fs::read_link(&installed_ref).ok().as_deref()
                == Some(self.paths.switch_commit().as_path());
        if !installed_is_link || self.packed_installed_exists()? {
            return Ok(true);
        }

        let switch = self.switch_target()?;
        if let Some(directory) = switch.as_ref() {
            let state = self.inspect_running_build(directory)?;
            if let Ok(commit_file) = fs::read_to_string(directory.join("commit")) {
                if commit_file == format!("{}\n", state.commit)
                    && self.ref_value("installed")?.as_deref() != Some(state.commit.as_str())
                {
                    return Err(Error::new(
                        "A7 failed: Git did not resolve the installed symlink to current/commit; stopped without a ref-writing fallback",
                    ));
                }
            }
            let installed = state.commit;
            if let (Some(release), Some(base)) =
                (self.ref_value("release")?, self.ref_value("base")?)
            {
                let derived = if installed == release {
                    Some(release.as_str())
                } else if self.is_ancestor(&release, &installed)?
                    && self.is_ancestor(&base, &release)?
                {
                    Some(release.as_str())
                } else {
                    None
                };
                if derived.is_some_and(|derived| derived != base) {
                    return Ok(true);
                }
            }
            let expected = self.bin_names_from_store(directory)?;
            for name in &expected {
                if !self.is_mechanism_link(&self.paths.tool_link(name), OsStr::new(name)) {
                    return Ok(true);
                }
            }
            for entry in self.product_link_entries()? {
                let name = entry.file_name();
                if name
                    .to_str()
                    .is_some_and(|name| is_product_binary(name, &self.paths.product))
                    && !expected.contains(&name.to_string_lossy().into_owned())
                    && self.is_mechanism_link(&entry.path(), &name)
                {
                    return Ok(true);
                }
            }
        } else {
            for entry in self.product_link_entries()? {
                let name = entry.file_name();
                if name
                    .to_str()
                    .is_some_and(|name| is_product_binary(name, &self.paths.product))
                    && self.is_mechanism_link(&entry.path(), &name)
                {
                    return Ok(true);
                }
            }
        }
        Ok(false)
    }

    pub fn inspect_running(&self) -> Result<Vec<String>> {
        match self.switch_target()? {
            Some(directory) => Ok(self.inspect_running_build(&directory)?.hand_edits),
            None => Ok(Vec::new()),
        }
    }

    pub fn release(&self) -> Result<Option<String>> {
        self.ref_value("release")
    }

    pub fn base(&self) -> Result<Option<String>> {
        self.ref_value("base")
    }

    pub fn installed(&self) -> Result<Option<String>> {
        match self.switch_target()? {
            Some(directory) => Ok(directory
                .file_name()
                .and_then(OsStr::to_str)
                .map(str::to_owned)),
            None => Ok(None),
        }
    }

    pub fn installed_tool_names(&self) -> Result<BTreeSet<String>> {
        match self.switch_target()? {
            Some(directory) => self.bin_names_from_store(&directory),
            None => Ok(BTreeSet::new()),
        }
    }

    pub fn require_no_hand_edits(&self) -> Result<()> {
        let hand_edits = self.inspect_running()?;
        if hand_edits.is_empty() {
            return Ok(());
        }
        Err(Error::new(format!(
            "running source has a hand edit; no source operation was performed: {}",
            hand_edits.join("; ")
        )))
    }

    pub fn adopt(&self) -> Result<()> {
        require_root()?;
        if !self.paths.package_commit.is_file() || !self.paths.package_bundle.is_file() {
            return Err(Error::new(format!(
                "package release files are missing under {}",
                self.paths.package_source.display()
            )));
        }
        let release = fs::read_to_string(&self.paths.package_commit)?
            .trim()
            .to_owned();
        if !git::valid_object_id(&release) {
            return Err(Error::new("base.commit must contain a full commit id"));
        }
        self.verify_packaged_source_identity(&release)?;

        self.ensure_source_repository()?;
        if self.paths.switch.exists() {
            let report = self.reconcile()?;
            if !report.hand_edits.is_empty() {
                eprintln!(
                    "adopting the received release while preserving hand-edited running source: {}",
                    report.hand_edits.join("; ")
                );
            }
        }
        self.fetch_from_path(&self.paths.package_bundle, &release)?;
        set_tree_readable(&self.paths.source_repo)?;
        let release = self.resolve_commit(&release)?;
        let expected_tools =
            crate::workspace::bin_names_at(&self.paths.source_repo, &self.paths.product, &release)?;
        let (available_tools, warnings) = self.available_packaged_tools(&expected_tools)?;
        for warning in warnings {
            eprintln!("warning: {warning}");
        }
        self.set_ref("release", &release)?;

        let before = self.installed()?;
        let Some(changed) =
            self.adopt_release(&release, &self.paths.base_tools, &available_tools)?
        else {
            return Ok(());
        };
        let after = self.installed()?.unwrap_or_else(|| release.clone());
        if before.as_deref() != Some(after.as_str()) {
            let changed = if changed.is_empty() {
                "none".to_owned()
            } else {
                changed.join(", ")
            };
            if let Some(before) = before.as_deref() {
                println!(
                    "installed {before} → {after}; changed: {changed}; go back: {} rollback --to {before}",
                    self.paths.product
                );
            } else {
                println!(
                    "installed none → {after}; changed: {changed}; no previous installed build exists"
                );
            }
        } else {
            println!("release {release} is already installed; no binary changed");
        }
        Ok(())
    }

    /// Prepare a received package build before changing the base ref. A failed
    /// downgrade preparation must leave the currently installed base intact.
    pub(crate) fn adopt_release(
        &self,
        release: &str,
        binary_directory: &Path,
        available_tools: &BTreeSet<String>,
    ) -> Result<Option<Vec<String>>> {
        let source_cli = format!("{}-source", self.paths.product);
        if !available_tools.contains(&source_cli) {
            return Err(Error::new(format!(
                "packaged source CLI {source_cli} is unavailable; the package was not adopted"
            )));
        }
        let workspace = self.prepare_store_from_binaries(
            release,
            None,
            binary_directory,
            BinaryOrigin::Package,
            Some(available_tools),
        )?;
        let current = self.installed()?;
        let base = self.base()?;
        let mut update_base = false;
        let activate = match (current.as_deref(), base.as_deref()) {
            (None, _) => {
                update_base = true;
                true
            }
            (Some(installed), Some(base)) if installed == base => {
                if release == installed {
                    false
                } else if self.is_ancestor(release, installed)? {
                    update_base = true;
                    true
                } else if self.is_ancestor(installed, release)? {
                    true
                } else {
                    eprintln!(
                        "release {release} is on another line than installed {installed}; run {} update",
                        self.paths.product
                    );
                    false
                }
            }
            (Some(installed), Some(_)) => {
                eprintln!(
                    "local commits exist above installed {installed}; release {release} received, not merged: run {} update",
                    self.paths.product
                );
                false
            }
            (Some(installed), None) => {
                return Err(Error::new(format!(
                    "base is missing while installed is {installed}; release recorded, no activation attempted"
                )));
            }
        };

        if !activate {
            return Ok(None);
        }

        if update_base {
            self.set_ref("base", release)?;
        }
        Ok(Some(self.activate_prepared(release, &workspace)?))
    }

    pub fn install_user_build(
        &self,
        working_clone: &Path,
        binary_directory: &Path,
        commit: &str,
        release: Option<&str>,
        initialize: bool,
    ) -> Result<(Option<String>, String, Vec<String>)> {
        require_root()?;
        let before = self.installed()?;
        if !self.paths.source_repo.exists() {
            if !initialize {
                return Err(Error::new(
                    "source repository is not initialized; use update --init",
                ));
            }
            self.ensure_source_repository()?;
        }
        self.require_bare_repository()?;
        self.fetch_from_path(working_clone, commit)?;
        set_tree_readable(&self.paths.source_repo)?;
        let commit = self.resolve_commit(commit)?;
        let release_commit = release
            .map(|release| self.resolve_commit(release))
            .transpose()?;

        if before.is_none() {
            if !initialize {
                return Err(Error::new(
                    "there is no active build; install requires --init",
                ));
            }
        } else {
            let base = self
                .base()?
                .ok_or_else(|| Error::new("base ref is missing; no activation attempted"))?;
            if !self.is_ancestor(&base, &commit)? {
                return Err(Error::new(format!(
                    "built commit {commit} does not descend from base {base}; installed build and running binaries are unchanged, though unreferenced source objects may have been fetched"
                )));
            }
        }

        let workspace = Workspace::discover(working_clone, &self.paths.product)?;
        self.prepare_store_from_binaries(
            &commit,
            Some(&workspace),
            binary_directory,
            BinaryOrigin::UserBuild,
            None,
        )?;
        if before.is_none() {
            let release = release_commit.as_deref().unwrap_or(&commit);
            self.set_ref("release", release)?;
            self.set_ref("base", release)?;
        } else if let Some(release) = release_commit.as_deref() {
            self.set_ref("release", release)?;
        }
        let changed = self.activate_prepared(&commit, &workspace)?;
        let after = self.installed()?.unwrap_or_else(|| commit.clone());
        Ok((before, after, changed))
    }

    pub fn receive_release(&self, working_clone: &Path, release: &str) -> Result<()> {
        require_root()?;
        self.require_bare_repository()?;
        self.fetch_from_path(working_clone, release)?;
        set_tree_readable(&self.paths.source_repo)?;
        let release = self.resolve_commit(release)?;
        self.set_ref("release", &release)?;
        let report = self.reconcile()?;
        for repair in report.repairs {
            println!("reconcile: {repair}");
        }
        println!("release {release} received; installed build unchanged");
        Ok(())
    }

    pub fn remove(&self) -> Result<RemovalReport> {
        require_root()?;
        let _ = self.reconcile()?;
        let old_installed = self.installed()?;
        let report = self.remove_switch_before_links(old_installed)?;
        if let Some(old) = report.old_installed.as_deref() {
            println!(
                "installed {old} → none; changed: {}; package reinstallation restores the received release",
                report
                    .removed
                    .iter()
                    .filter_map(|item| item.strip_prefix("link "))
                    .collect::<Vec<_>>()
                    .join(", ")
            );
        }
        Ok(report)
    }

    pub(crate) fn remove_switch_before_links(
        &self,
        old_installed: Option<String>,
    ) -> Result<RemovalReport> {
        let mut removed = Vec::new();

        // Removal is intentionally switch first: the installed ref becomes dangling before any
        // mechanism-owned command link is removed.
        match fs::symlink_metadata(&self.paths.switch) {
            Ok(_) => {
                fs::remove_file(&self.paths.switch).map_err(|error| {
                    Error::new(format!(
                        "could not remove the switch; installed build and tool links remain unchanged: {error}"
                    ))
                })?;
                removed.push("switch".to_owned());
                println!("removed switch; installed now resolves to no active build");
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => {
                return Err(Error::new(format!(
                    "could not inspect the switch; installed build and tool links remain unchanged: {error}"
                )));
            }
        }
        let entries = self.product_link_entries().map_err(|error| {
            Error::new(format!(
                "switch is absent and mechanism tool links have not been removed: {error}"
            ))
        })?;
        for entry in entries {
            let name = entry.file_name();
            let Some(name_text) = name.to_str() else {
                continue;
            };
            if !is_product_binary(name_text, &self.paths.product) {
                continue;
            }
            if self.is_mechanism_link(&entry.path(), &name) {
                fs::remove_file(entry.path()).map_err(|error| {
                    Error::new(format!(
                        "switch was removed and links {} were removed, but link {name_text} remains; installed resolves to no active build: {error}",
                        removed
                            .iter()
                            .filter_map(|item| item.strip_prefix("link "))
                            .collect::<Vec<_>>()
                            .join(", ")
                    ))
                })?;
                removed.push(format!("link {name_text}"));
                println!("removed tool link {name_text}");
            }
        }
        Ok(RemovalReport {
            old_installed,
            removed,
        })
    }

    fn activate_prepared(&self, commit: &str, workspace: &Workspace) -> Result<Vec<String>> {
        let new_directory = self.paths.build_dir(commit);
        let old_commit = self.installed()?;
        let old_names = match self.switch_target()? {
            Some(directory) => self.bin_names_from_store(&directory)?,
            None => BTreeSet::new(),
        };
        self.ensure_links_before_activation(workspace, &old_names)?;
        self.rename_switch(&new_directory)?;
        let report = self.reconcile()?;
        if !report.hand_edits.is_empty() {
            eprintln!(
                "activated build with a preserved hand-edited source: {}",
                report.hand_edits.join("; ")
            );
        }
        let mut changed = old_names;
        changed.extend(workspace.bin_names());
        let changed = if old_commit.as_deref() == Some(commit) {
            Vec::new()
        } else {
            changed.into_iter().collect()
        };
        Ok(changed)
    }

    fn prepare_store_from_binaries(
        &self,
        commit: &str,
        expected_workspace: Option<&Workspace>,
        binary_directory: &Path,
        origin: BinaryOrigin,
        available_tools: Option<&BTreeSet<String>>,
    ) -> Result<Workspace> {
        let destination = self.paths.build_dir(commit);
        let source = self.paths.build_source(commit);
        let mut workspace = if source.is_dir() {
            Some(Workspace::discover(&source, &self.paths.product)?)
        } else {
            None
        };
        if let (Some(workspace), Some(available_tools)) = (workspace.as_mut(), available_tools) {
            workspace
                .bins
                .retain(|name, _| available_tools.contains(name));
        }
        if let Some(workspace) = workspace.as_ref() {
            if self.store_complete(commit, workspace)? {
                if let Some(expected) = expected_workspace {
                    if expected.bin_names() != workspace.bin_names() {
                        return Err(Error::new(
                            "working clone and fetched source have different tool manifests",
                        ));
                    }
                }
                if available_tools.is_some() {
                    let available_names: BTreeSet<_> = workspace.bin_names().into_iter().collect();
                    let stored_names = self.bin_names_from_store(&destination)?;
                    let disabled_names: Vec<_> = stored_names
                        .difference(&available_names)
                        .cloned()
                        .collect();
                    for name in &disabled_names {
                        fs::remove_file(self.paths.build_bin(commit).join(name))?;
                    }
                    if !disabled_names.is_empty()
                        && self.switch_target()?.as_deref() == Some(destination.as_path())
                    {
                        self.reconcile_tool_links(&available_names, &mut Vec::new())?;
                    }
                }
                return Ok(workspace.clone());
            }
        }
        if self.switch_target()?.as_deref() == Some(destination.as_path()) {
            return Err(Error::new(format!(
                "active build store {} is incomplete; it was left untouched",
                destination.display()
            )));
        }
        self.discard_incomplete_store(commit)?;
        fs::create_dir_all(&self.paths.builds)?;
        fs::set_permissions(&self.paths.builds, fs::Permissions::from_mode(0o755))?;
        fs::create_dir_all(&destination)?;
        fs::set_permissions(&destination, fs::Permissions::from_mode(0o755))?;
        git::git_command(
            &self.paths.source_repo,
            [
                OsString::from("worktree"),
                OsString::from("add"),
                OsString::from("--detach"),
                source.as_os_str().to_os_string(),
                OsString::from(commit),
            ],
        )?;
        set_tree_readable(&source)?;
        let mut workspace = Workspace::discover(&source, &self.paths.product)?;
        if let Some(expected) = expected_workspace {
            if expected.bin_names() != workspace.bin_names() {
                return Err(Error::new(
                    "working clone and fetched source have different tool manifests",
                ));
            }
        }
        if let Some(available_tools) = available_tools {
            workspace
                .bins
                .retain(|name, _| available_tools.contains(name));
        }
        self.write_commit_file(commit)?;
        let bin = self.paths.build_bin(commit);
        fs::create_dir_all(&bin)?;
        for name in workspace.bins.keys() {
            let built = binary_directory.join(name);
            let installed = bin.join(name);
            match origin {
                BinaryOrigin::Package => {
                    require_executable_regular_file(&built, "packaged tool")?;
                    fs::copy(&built, &installed).map_err(|error| {
                        Error::new(format!(
                            "could not copy packaged tool {}: {error}",
                            built.display()
                        ))
                    })?;
                }
                BinaryOrigin::UserBuild => copy_user_binary(&built, &installed)?,
            }
            fs::set_permissions(&installed, fs::Permissions::from_mode(0o755))?;
        }
        set_tree_readable(&source)?;
        fs::set_permissions(&bin, fs::Permissions::from_mode(0o755))?;
        if !self.store_complete(commit, &workspace)? {
            return Err(Error::new(format!(
                "build store {} is incomplete after preparation",
                destination.display()
            )));
        }
        Ok(workspace)
    }

    fn store_complete(&self, commit: &str, workspace: &Workspace) -> Result<bool> {
        let directory = self.paths.build_dir(commit);
        let source = self.paths.build_source(commit);
        let commit_file = self.paths.switch_commit_for(commit);
        if !directory.is_dir() || !source.is_dir() || !commit_file.is_file() {
            return Ok(false);
        }
        let expected_commit = format!("{commit}\n");
        if fs::read_to_string(&commit_file).ok().as_deref() != Some(expected_commit.as_str()) {
            return Ok(false);
        }
        if git::git_text(&source, &["rev-parse", "HEAD"])
            .ok()
            .as_deref()
            != Some(commit)
        {
            return Ok(false);
        }
        if git::git_status(
            &source,
            &[
                OsString::from("symbolic-ref"),
                OsString::from("-q"),
                OsString::from("HEAD"),
            ],
        )
        .is_ok_and(|output| output.status.success())
        {
            return Ok(false);
        }
        for name in workspace.bins.keys() {
            let path = self.paths.build_bin(commit).join(name);
            if !path.is_file() || !is_executable(&path) {
                return Ok(false);
            }
        }
        Ok(true)
    }

    fn discard_incomplete_store(&self, commit: &str) -> Result<()> {
        let directory = self.paths.build_dir(commit);
        if !directory.exists() {
            return Ok(());
        }
        let source = self.paths.build_source(commit);
        if source.exists() {
            let head = git::git_text(&source, &["rev-parse", "HEAD"]).map_err(|error| {
                Error::new(format!(
                    "incomplete store has an unreadable worktree: {error}"
                ))
            })?;
            if head != commit {
                return Err(Error::new(format!(
                    "incomplete build store {} has a moved worktree HEAD; it was left untouched",
                    directory.display()
                )));
            }
            let status = git::git_command(
                &source,
                [
                    OsString::from("status"),
                    OsString::from("--porcelain=v1"),
                    OsString::from("-z"),
                    OsString::from("--untracked-files=all"),
                ],
            )?;
            if !status.stdout.is_empty() {
                return Err(Error::new(format!(
                    "incomplete build store {} has source edits; it was left untouched",
                    directory.display()
                )));
            }
            git::git_command(
                &self.paths.source_repo,
                [
                    OsString::from("worktree"),
                    OsString::from("remove"),
                    OsString::from("--force"),
                    source.as_os_str().to_os_string(),
                ],
            )?;
        }
        fs::remove_dir_all(&directory)?;
        Ok(())
    }

    fn verify_binary_commit(&self, binary: &Path, commit: &str) -> Result<()> {
        let output = Command::new(binary)
            .arg("--version")
            .output()
            .map_err(|error| {
                Error::new(format!(
                    "cannot run packaged source CLI {} --version: {error}",
                    binary.display()
                ))
            })?;
        if !output.status.success() {
            return Err(Error::new(format!(
                "packaged source CLI {} --version failed; the package was not adopted",
                binary.display()
            )));
        }
        let version = String::from_utf8_lossy(&output.stdout);
        if version.split_whitespace().last() != Some(commit) {
            return Err(Error::new(format!(
                "packaged source CLI {} does not report source commit {commit}; the package was not adopted",
                binary.display()
            )));
        }
        Ok(())
    }

    pub(crate) fn verify_packaged_source_identity(&self, commit: &str) -> Result<()> {
        let binary = self
            .paths
            .base_tools
            .join(format!("{}-source", self.paths.product));
        require_executable_regular_file(&binary, "packaged source CLI")?;
        self.verify_binary_commit(&binary, commit)
    }

    pub(crate) fn available_packaged_tools(
        &self,
        expected: &BTreeSet<String>,
    ) -> Result<(BTreeSet<String>, Vec<String>)> {
        let source_cli = format!("{}-source", self.paths.product);
        if !expected.contains(&source_cli) {
            return Err(Error::new(format!(
                "packaged source bundle does not define {source_cli}"
            )));
        }
        let mut available = BTreeSet::new();
        let mut warnings = Vec::new();
        for name in expected {
            let path = self.paths.base_tools.join(name);
            if name == &source_cli {
                require_executable_regular_file(&path, "packaged source CLI")?;
                available.insert(name.clone());
                continue;
            }
            match packaged_tool_unavailability(&path)? {
                Some(reason) => warnings.push(format!(
                    "packaged tool {name} {reason}; it will be disabled"
                )),
                None => {
                    available.insert(name.clone());
                }
            }
        }
        Ok((available, warnings))
    }

    fn ensure_links_before_activation(
        &self,
        workspace: &Workspace,
        old_names: &BTreeSet<String>,
    ) -> Result<()> {
        fs::create_dir_all(&self.paths.tool_bin)?;
        for name in workspace.bins.keys() {
            if old_names.contains(name) {
                continue;
            }
            let link = self.paths.tool_link(name);
            if self.is_mechanism_link(&link, OsStr::new(name)) {
                continue;
            }
            self.create_or_check_link(&link, &self.paths.tool_target(name))?;
        }
        Ok(())
    }

    fn reconcile_tool_links(
        &self,
        expected: &BTreeSet<String>,
        repairs: &mut Vec<String>,
    ) -> Result<()> {
        fs::create_dir_all(&self.paths.tool_bin)?;
        for name in expected {
            let target = self.paths.tool_target(name);
            if !target.is_file() {
                return Err(Error::new(format!(
                    "active build is missing tool binary {}; it remains untouched",
                    target.display()
                )));
            }
            let link = self.paths.tool_link(name);
            if !self.is_mechanism_link(&link, OsStr::new(name)) {
                self.create_or_check_link(&link, &target)?;
                repairs.push(format!("created tool link {name}"));
            }
        }
        for entry in self.product_link_entries()? {
            let file_name = entry.file_name();
            let Some(name) = file_name.to_str() else {
                continue;
            };
            if !is_product_binary(name, &self.paths.product)
                || expected.contains(name)
                || !self.is_mechanism_link(&entry.path(), &file_name)
            {
                continue;
            }
            fs::remove_file(entry.path())?;
            repairs.push(format!("removed link {name}"));
        }
        Ok(())
    }

    fn bin_names_from_store(&self, directory: &Path) -> Result<BTreeSet<String>> {
        let bin = directory.join("bin");
        let mut names = BTreeSet::new();
        if !bin.is_dir() {
            return Ok(names);
        }
        for entry in fs::read_dir(bin)? {
            let entry = entry?;
            let name = entry.file_name();
            let Some(name_text) = name.to_str() else {
                continue;
            };
            if !is_product_binary(name_text, &self.paths.product) || !is_executable(&entry.path()) {
                continue;
            }
            names.insert(name_text.to_owned());
        }
        Ok(names)
    }

    fn remove_current_links(&self, repairs: &mut Vec<String>) -> Result<()> {
        for entry in self.product_link_entries()? {
            let name = entry.file_name();
            let Some(name_text) = name.to_str() else {
                continue;
            };
            if is_product_binary(name_text, &self.paths.product)
                && self.is_mechanism_link(&entry.path(), &name)
            {
                fs::remove_file(entry.path())?;
                repairs.push(format!("removed link {name_text} with no active build"));
            }
        }
        Ok(())
    }

    fn create_or_check_link(&self, link: &Path, target: &Path) -> Result<()> {
        match fs::symlink_metadata(link) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                if self.is_mechanism_link(link, link.file_name().unwrap_or_default()) {
                    return Ok(());
                }
                Err(Error::new(format!(
                    "cannot replace existing link {}; no activation was performed",
                    link.display()
                )))
            }
            Ok(_) => Err(Error::new(format!(
                "cannot replace existing path {}; no activation was performed",
                link.display()
            ))),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => symlink(target, link)
                .map_err(|error| {
                    Error::new(format!(
                        "cannot create tool link {}: {error}",
                        link.display()
                    ))
                }),
            Err(error) => Err(error.into()),
        }
    }

    fn is_mechanism_link(&self, link: &Path, name: &OsStr) -> bool {
        if !fs::symlink_metadata(link).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
            return false;
        }
        let Ok(target) = fs::read_link(link) else {
            return false;
        };
        target == self.paths.switch.join("bin").join(name)
    }

    fn product_link_entries(&self) -> Result<Vec<fs::DirEntry>> {
        if !self.paths.tool_bin.is_dir() {
            return Ok(Vec::new());
        }
        let mut entries = Vec::new();
        for entry in fs::read_dir(&self.paths.tool_bin)? {
            entries.push(entry?);
        }
        Ok(entries)
    }

    fn rename_switch(&self, destination: &Path) -> Result<()> {
        let parent = self
            .paths
            .switch
            .parent()
            .ok_or_else(|| Error::new("switch path has no parent directory"))?;
        fs::create_dir_all(parent)?;
        let temporary = parent.join(format!(".current-{}.new", std::process::id()));
        if fs::symlink_metadata(&temporary).is_ok() {
            fs::remove_file(&temporary)?;
        }
        symlink(destination, &temporary)?;
        fs::rename(&temporary, &self.paths.switch).map_err(|error| {
            let _ = fs::remove_file(&temporary);
            Error::new(format!("could not atomically activate build: {error}"))
        })
    }

    fn ensure_source_repository(&self) -> Result<()> {
        match fs::symlink_metadata(&self.paths.source_repo) {
            Ok(_) => {
                self.require_bare_repository()?;
                return Ok(());
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        if let Some(parent) = self.paths.source_repo.parent() {
            fs::create_dir_all(parent)?;
            ensure_mode(parent, 0o755)?;
        }
        git::git_command(
            Path::new("/"),
            [
                OsString::from("-c"),
                OsString::from("init.defaultRefFormat=files"),
                OsString::from("init"),
                OsString::from("--bare"),
                self.paths.source_repo.as_os_str().to_os_string(),
            ],
        )?;
        self.ensure_gc_configuration()?;
        set_tree_readable(&self.paths.source_repo)?;
        fs::create_dir_all(self.paths.source_repo.join("refs/heads"))?;
        self.ensure_installed_ref()?;
        self.require_bare_repository()?;
        Ok(())
    }

    fn require_bare_repository(&self) -> Result<()> {
        match fs::symlink_metadata(&self.paths.source_repo) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(Error::new("source repository is not initialized"));
            }
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
                return Err(Error::new(
                    "source repository path is not a real directory; it was left untouched",
                ));
            }
            Ok(_) => {}
            Err(error) => return Err(error.into()),
        }
        let bare = git::git_text(
            &self.paths.source_repo,
            &["rev-parse", "--is-bare-repository"],
        )?;
        if bare != "true" {
            return Err(Error::new(format!(
                "{} is not a bare source repository; it was left untouched",
                self.paths.source_repo.display()
            )));
        }
        let format = self.paths.source_repo.join("reftable");
        if format.exists() {
            return Err(Error::new(
                "source repository is not using loose files refs; no action was taken",
            ));
        }
        if let Some(path) = first_non_root_owned(&self.paths.source_repo)? {
            return Err(Error::new(format!(
                "source repository path {} is not root-owned; it was left untouched",
                path.display()
            )));
        }
        if let Some(parent) = self.paths.source_repo.parent() {
            let metadata = fs::symlink_metadata(parent)?;
            if metadata.file_type().is_symlink() || !metadata.is_dir() || metadata.uid() != 0 {
                return Err(Error::new(format!(
                    "source repository parent {} is not a root-owned directory; it was left untouched",
                    parent.display()
                )));
            }
        }
        self.ensure_gc_configuration()?;
        if let Some(parent) = self.paths.source_repo.parent() {
            ensure_mode(parent, 0o755)?;
        }
        set_tree_readable(&self.paths.source_repo)?;
        Ok(())
    }

    fn ensure_gc_configuration(&self) -> Result<()> {
        ensure_git_config(&self.paths.source_repo, "gc.auto", "0")?;
        ensure_git_config(&self.paths.source_repo, "gc.packRefs", "false")
    }

    fn ensure_installed_ref(&self) -> Result<bool> {
        let installed_ref = self.paths.installed_ref();
        let parent = installed_ref.parent().unwrap();
        fs::create_dir_all(parent)?;
        ensure_mode(parent, 0o755)?;
        let expected = self.paths.switch_commit();
        if fs::symlink_metadata(&installed_ref)
            .is_ok_and(|metadata| metadata.file_type().is_symlink())
            && fs::read_link(&installed_ref).ok().as_deref() == Some(expected.as_path())
        {
            return Ok(false);
        }
        match fs::symlink_metadata(&installed_ref) {
            Ok(metadata) if metadata.is_dir() && !metadata.file_type().is_symlink() => {
                return Err(Error::new(
                    "installed ref path is a directory; it was left untouched",
                ));
            }
            Ok(_) => fs::remove_file(&installed_ref)?,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        symlink(expected, installed_ref)?;
        Ok(true)
    }

    fn remove_packed_installed(&self) -> Result<bool> {
        let path = self.paths.source_repo.join("packed-refs");
        let contents = match fs::read(&path) {
            Ok(contents) => contents,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(error.into()),
        };
        let mut output = Vec::with_capacity(contents.len());
        let mut removed = false;
        let mut skip_peeled = false;
        for line in contents.split_inclusive(|byte| *byte == b'\n') {
            if skip_peeled && line.starts_with(b"^") {
                skip_peeled = false;
                continue;
            }
            skip_peeled = false;
            let fields = line
                .split(|byte| byte.is_ascii_whitespace())
                .filter(|field| !field.is_empty())
                .collect::<Vec<_>>();
            if fields
                .get(1)
                .is_some_and(|name| *name == b"refs/heads/installed")
            {
                removed = true;
                skip_peeled = true;
                continue;
            }
            output.extend_from_slice(line);
        }
        if !removed {
            return Ok(false);
        }
        let temporary = self
            .paths
            .source_repo
            .join(format!("packed-refs.{}.new", std::process::id()));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(&output)?;
        file.sync_all()?;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(0o644))?;
        fs::rename(temporary, path)?;
        Ok(true)
    }

    fn packed_installed_exists(&self) -> Result<bool> {
        let path = self.paths.source_repo.join("packed-refs");
        let contents = match fs::read(path) {
            Ok(contents) => contents,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(error.into()),
        };
        Ok(contents.split(|byte| *byte == b'\n').any(|line| {
            let fields = line
                .split(|byte| byte.is_ascii_whitespace())
                .filter(|field| !field.is_empty())
                .collect::<Vec<_>>();
            fields
                .get(1)
                .is_some_and(|name| *name == b"refs/heads/installed")
        }))
    }

    fn switch_target(&self) -> Result<Option<PathBuf>> {
        match fs::symlink_metadata(&self.paths.switch) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                let target = fs::read_link(&self.paths.switch)?;
                let target = if target.is_absolute() {
                    target
                } else {
                    self.paths
                        .switch
                        .parent()
                        .unwrap_or_else(|| Path::new("/"))
                        .join(target)
                };
                let canonical = target.canonicalize().map_err(|error| {
                    Error::new(format!(
                        "switch target {} is unavailable: {error}",
                        target.display()
                    ))
                })?;
                let builds = self.paths.builds.canonicalize().map_err(|error| {
                    Error::new(format!(
                        "build store {} is unavailable: {error}",
                        self.paths.builds.display()
                    ))
                })?;
                if !canonical.starts_with(builds) {
                    return Err(Error::new(format!(
                        "switch points outside the build store at {}; it was left untouched",
                        canonical.display()
                    )));
                }
                Ok(Some(canonical))
            }
            Ok(_) => Err(Error::new("switch is not a symlink; it was left untouched")),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    fn inspect_running_build(&self, directory: &Path) -> Result<RunningBuild> {
        let expected = directory
            .file_name()
            .and_then(OsStr::to_str)
            .ok_or_else(|| Error::new("active build directory has no commit name"))?;
        if !git::valid_object_id(expected) {
            return Err(Error::new(
                "active build directory name is not a full commit id",
            ));
        }
        let worktree = directory.join("source");
        let commit_path = directory.join("commit");
        let commit_contents = fs::read(&commit_path).unwrap_or_default();
        let mut hand_edits = Vec::new();
        match first_non_root_owned(directory) {
            Ok(Some(path)) => hand_edits.push(format!(
                "running build path {} is not root-owned",
                path.display()
            )),
            Ok(None) => {}
            Err(error) => {
                hand_edits.push(format!("cannot verify running build ownership: {error}"))
            }
        }
        if !worktree.is_dir() {
            hand_edits.push("running source worktree is missing".to_owned());
        } else {
            match git::git_text(&worktree, &["rev-parse", "HEAD"]) {
                Ok(head) if head != expected => {
                    hand_edits.push(format!(
                        "running source HEAD is {head}, directory is {expected}"
                    ));
                }
                Ok(_) => {}
                Err(error) => hand_edits.push(format!("cannot read running source HEAD: {error}")),
            }
        }
        if commit_contents != format!("{expected}\n").as_bytes() {
            hand_edits.push(format!(
                "running build commit file {} was altered",
                commit_path.display()
            ));
        }
        let status = git::git_command(
            &worktree,
            [
                OsString::from("status"),
                OsString::from("--porcelain=v1"),
                OsString::from("-z"),
                OsString::from("--untracked-files=all"),
            ],
        );
        match status {
            Ok(status) => {
                for field in status
                    .stdout
                    .split(|byte| *byte == 0)
                    .filter(|field| !field.is_empty())
                {
                    if field.len() >= 4 {
                        hand_edits.push(format!(
                            "running source has hand edit: {}",
                            String::from_utf8_lossy(&field[3..])
                        ));
                    }
                }
            }
            Err(error) => hand_edits.push(format!("cannot inspect running source: {error}")),
        }
        Ok(RunningBuild {
            commit: expected.to_owned(),
            hand_edits,
        })
    }

    fn write_commit_file(&self, commit: &str) -> Result<()> {
        let path = self.paths.switch_commit_for(commit);
        fs::write(&path, format!("{commit}\n"))?;
        fs::set_permissions(path, fs::Permissions::from_mode(0o644))?;
        Ok(())
    }

    fn ref_value(&self, name: &str) -> Result<Option<String>> {
        if !self.paths.source_repo.exists() {
            return Ok(None);
        }
        let reference = format!("refs/heads/{name}");
        let output = git::git_status(
            &self.paths.source_repo,
            &[
                OsString::from("rev-parse"),
                OsString::from("--verify"),
                OsString::from(&reference),
            ],
        )?;
        if !output.status.success() {
            return Ok(None);
        }
        let value = String::from_utf8_lossy(&output.stdout).trim().to_owned();
        if git::valid_object_id(&value) {
            Ok(Some(value))
        } else {
            Err(Error::new(format!(
                "{name} ref did not resolve to a full commit id"
            )))
        }
    }

    fn set_ref(&self, name: &str, commit: &str) -> Result<()> {
        if self.ref_value(name)?.as_deref() == Some(commit) {
            return Ok(());
        }
        git::git_at(
            &self.paths.source_repo,
            &["update-ref", &format!("refs/heads/{name}"), commit],
        )?;
        let reference = self.paths.source_repo.join("refs/heads").join(name);
        if reference.is_file() {
            ensure_mode(&reference, 0o644)?;
        }
        Ok(())
    }

    fn resolve_commit(&self, commit: &str) -> Result<String> {
        if !git::valid_object_id(commit) {
            return Err(Error::new("a full commit id is required"));
        }
        let resolved = git::git_text(
            &self.paths.source_repo,
            &["rev-parse", "--verify", &format!("{commit}^{{commit}}")],
        )?;
        if resolved != commit.to_ascii_lowercase() {
            return Err(Error::new(
                "the requested object id did not resolve to that commit",
            ));
        }
        Ok(resolved)
    }

    fn fetch_from_path(&self, source: &Path, commit: &str) -> Result<()> {
        if !git::valid_object_id(commit) {
            return Err(Error::new("a full commit id is required"));
        }
        if !source.is_absolute() {
            return Err(Error::new(
                "root fetch requires an absolute local path; remote updates must be fetched by the user first",
            ));
        }
        if self.resolve_commit(commit).is_ok() {
            return Ok(());
        }
        git::git_command(
            &self.paths.source_repo,
            [
                OsString::from("fetch"),
                OsString::from("--no-tags"),
                source.as_os_str().to_os_string(),
                OsString::from(commit),
            ],
        )?;
        Ok(())
    }

    fn is_ancestor(&self, ancestor: &str, descendant: &str) -> Result<bool> {
        let output = git::git_status(
            &self.paths.source_repo,
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
}

#[derive(Clone, Debug)]
struct RunningBuild {
    commit: String,
    hand_edits: Vec<String>,
}

pub fn require_root() -> Result<()> {
    let output = Command::new("id")
        .arg("-u")
        .output()
        .map_err(|error| Error::new(format!("could not determine effective user: {error}")))?;
    if output.status.success() && String::from_utf8_lossy(&output.stdout).trim() == "0" {
        Ok(())
    } else {
        Err(Error::new("this source operation requires root"))
    }
}

fn is_product_binary(name: &str, product: &str) -> bool {
    name == product || name.starts_with(&format!("{product}-"))
}

fn is_executable(path: &Path) -> bool {
    fs::metadata(path)
        .is_ok_and(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
}

fn require_executable_regular_file(path: &Path, description: &str) -> Result<()> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        Error::new(format!(
            "cannot inspect {description} {}: {error}",
            path.display()
        ))
    })?;
    if !metadata.file_type().is_file() || metadata.permissions().mode() & 0o111 == 0 {
        return Err(Error::new(format!(
            "{description} {} is not an executable regular file",
            path.display()
        )));
    }
    Ok(())
}

fn packaged_tool_unavailability(path: &Path) -> Result<Option<&'static str>> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(Some("is missing"));
        }
        Err(error) => {
            return Err(Error::new(format!(
                "cannot inspect packaged tool {}: {error}",
                path.display()
            )));
        }
    };
    if !metadata.file_type().is_file() {
        return Ok(Some("is not an executable regular file"));
    }
    if metadata.permissions().mode() & 0o111 == 0 {
        return Ok(Some("is not executable"));
    }
    Ok(None)
}

fn set_tree_readable(root: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(root)?;
    if metadata.file_type().is_symlink() {
        return Ok(());
    }
    if metadata.is_dir() {
        ensure_mode(root, 0o755)?;
        for entry in fs::read_dir(root)? {
            set_tree_readable(&entry?.path())?;
        }
    } else if metadata.is_file() {
        let mode = if metadata.permissions().mode() & 0o111 != 0 {
            0o755
        } else {
            0o644
        };
        ensure_mode(root, mode)?;
    }
    Ok(())
}

fn ensure_mode(path: &Path, mode: u32) -> Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() || metadata.permissions().mode() & 0o777 != mode {
        if metadata.file_type().is_symlink() {
            return Ok(());
        }
        fs::set_permissions(path, fs::Permissions::from_mode(mode))?;
    }
    Ok(())
}

fn ensure_git_config(repository: &Path, key: &str, value: &str) -> Result<()> {
    if config_value(repository, key)?.as_deref() == Some(value) {
        return Ok(());
    }
    git::git_command(
        repository,
        [
            OsString::from("config"),
            OsString::from("--local"),
            OsString::from(key),
            OsString::from(value),
        ],
    )?;
    Ok(())
}

fn config_value(repository: &Path, key: &str) -> Result<Option<String>> {
    let output = git::git_status(
        repository,
        &[
            OsString::from("config"),
            OsString::from("--local"),
            OsString::from("--get"),
            OsString::from(key),
        ],
    )?;
    Ok(output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_owned()))
}

fn mode_needs_readable(path: &Path) -> Result<bool> {
    let metadata = fs::symlink_metadata(path)?;
    Ok(metadata.uid() != 0
        || metadata.file_type().is_symlink()
        || metadata.permissions().mode() & 0o777 != 0o755)
}

fn tree_needs_readable(root: &Path) -> Result<bool> {
    let metadata = fs::symlink_metadata(root)?;
    if metadata.uid() != 0 {
        return Ok(true);
    }
    if metadata.file_type().is_symlink() {
        return Ok(false);
    }
    if metadata.file_type().is_symlink() {
        return Ok(false);
    }
    let expected = if metadata.is_dir() {
        0o755
    } else if metadata.permissions().mode() & 0o111 != 0 {
        0o755
    } else {
        0o644
    };
    if metadata.permissions().mode() & 0o777 != expected {
        return Ok(true);
    }
    if metadata.is_dir() {
        for entry in fs::read_dir(root)? {
            if tree_needs_readable(&entry?.path())? {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

fn first_non_root_owned(root: &Path) -> Result<Option<PathBuf>> {
    let metadata = fs::symlink_metadata(root)?;
    if metadata.uid() != 0 {
        return Ok(Some(root.to_path_buf()));
    }
    if metadata.is_dir() && !metadata.file_type().is_symlink() {
        for entry in fs::read_dir(root)? {
            if let Some(path) = first_non_root_owned(&entry?.path())? {
                return Ok(Some(path));
            }
        }
    }
    Ok(None)
}

fn copy_user_binary(source: &Path, destination: &Path) -> Result<()> {
    let caller_uid = env::var("SUDO_UID")
        .ok()
        .and_then(|value| value.parse::<u32>().ok())
        .filter(|uid| *uid != 0)
        .ok_or_else(|| {
            Error::new("user build install must be invoked through sudo by an unprivileged user")
        })?;
    let mut input = OpenOptions::new()
        .read(true)
        .open(source)
        .map_err(|error| {
            Error::new(format!(
                "could not open built tool {}: {error}",
                source.display()
            ))
        })?;
    let metadata = input.metadata()?;
    if !metadata.is_file()
        || metadata.uid() != caller_uid
        || metadata.permissions().mode() & 0o111 == 0
    {
        return Err(Error::new(format!(
            "built tool {} must be an executable regular file owned by the invoking user",
            source.display()
        )));
    }
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(destination)
        .map_err(|error| {
            Error::new(format!(
                "could not prepare build-store tool {}: {error}",
                destination.display()
            ))
        })?;
    std::io::copy(&mut input, &mut output)?;
    output.sync_all()?;
    Ok(())
}
