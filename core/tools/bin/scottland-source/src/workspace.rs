use crate::error::{Error, Result};
use crate::git;
use std::collections::BTreeMap;
use std::ffi::{OsStr, OsString};
use std::fs;
use std::os::unix::ffi::OsStrExt;
use std::path::{Component, Path, PathBuf};
use std::process::Command;

#[derive(Clone, Debug)]
pub struct Workspace {
    pub relative_root: PathBuf,
    pub manifest: PathBuf,
    pub bins: BTreeMap<String, PathBuf>,
}

impl Workspace {
    pub fn discover(repository: &Path, product: &str) -> Result<Self> {
        let mut manifests = Vec::new();
        find_manifests(repository, repository, &mut manifests)?;

        let mut matches = Vec::new();
        for manifest in manifests {
            let value = read_toml(&manifest)?;
            let Some(workspace) = value.get("workspace") else {
                continue;
            };
            let root = manifest
                .parent()
                .ok_or_else(|| Error::new("workspace manifest has no parent directory"))?;
            let packages = member_manifests(root, workspace)?;
            let mut bins = BTreeMap::new();
            let mut has_source = false;
            for package_manifest in packages {
                let package = read_toml(&package_manifest)?;
                let Some(name) = package
                    .get("package")
                    .and_then(|package| package.get("name"))
                    .and_then(toml::Value::as_str)
                else {
                    continue;
                };
                if name == format!("{product}-source") {
                    has_source = true;
                }
                for binary in binary_targets(&package_manifest, &package, name)? {
                    if binary == product || binary.starts_with(&format!("{product}-")) {
                        let crate_root = package_manifest
                            .parent()
                            .ok_or_else(|| Error::new("package manifest has no parent directory"))?
                            .to_path_buf();
                        if bins.insert(binary.clone(), crate_root).is_some() {
                            return Err(Error::new(format!(
                                "workspace defines the binary {binary:?} more than once"
                            )));
                        }
                    }
                }
            }
            if has_source {
                let relative_root = root
                    .strip_prefix(repository)
                    .map_err(|_| Error::new("workspace is outside the product repository"))?
                    .to_path_buf();
                matches.push(Self {
                    relative_root,
                    manifest,
                    bins,
                });
            }
        }

        match matches.len() {
            1 => Ok(matches.remove(0)),
            0 => Err(Error::new(format!(
                "could not find the product workspace containing {product}-source"
            ))),
            _ => Err(Error::new(format!(
                "more than one product workspace contains {product}-source"
            ))),
        }
    }

    pub fn tool_directory(&self, tool: &str) -> Result<&Path> {
        self.bins
            .get(tool)
            .map(PathBuf::as_path)
            .ok_or_else(|| Error::new(format!("{tool:?} is not a tool in this workspace")))
    }

    pub fn resolve_tool(&self, product: &str, tool: &str) -> Result<String> {
        let binary = if tool == product || tool.starts_with(&format!("{product}-")) {
            tool.to_owned()
        } else {
            format!("{product}-{tool}")
        };
        if self.bins.contains_key(&binary) {
            Ok(binary)
        } else {
            Err(Error::new(format!(
                "{tool:?} is not a tool in this workspace"
            )))
        }
    }

    pub fn bin_names(&self) -> Vec<String> {
        self.bins.keys().cloned().collect()
    }

    pub fn validate_changes(&self, repository: &Path) -> Result<()> {
        let output = git::git_command(
            repository,
            [
                OsString::from("status"),
                OsString::from("--porcelain=v1"),
                OsString::from("-z"),
                OsString::from("--untracked-files=all"),
            ],
        )?;
        let mut fields = output
            .stdout
            .split(|byte| *byte == 0)
            .filter(|field| !field.is_empty());
        while let Some(field) = fields.next() {
            if field.len() < 4 {
                continue;
            }
            let path = &field[3..];
            if !self.path_is_inside(path) {
                return Err(Error::new(format!(
                    "change outside the product workspace: {}",
                    String::from_utf8_lossy(path)
                )));
            }
            let status = &field[..2];
            if status.contains(&b'R') || status.contains(&b'C') {
                if let Some(other) = fields.next() {
                    if !self.path_is_inside(other) {
                        return Err(Error::new(format!(
                            "change outside the product workspace: {}",
                            String::from_utf8_lossy(other)
                        )));
                    }
                }
            }
        }
        Ok(())
    }

    pub fn build(&self, repository: &Path, target_dir: &Path, commit: &str) -> Result<PathBuf> {
        let output_dir = target_dir.join("release");
        eprintln!("building the complete workspace at {commit}");
        let status = Command::new("cargo")
            .arg("build")
            .arg("--locked")
            .arg("--release")
            .arg("--manifest-path")
            .arg(&self.manifest)
            .arg("--target-dir")
            .arg(target_dir)
            .current_dir(repository)
            .env("PRODUCT_SOURCE_BUILD_COMMIT", commit)
            .status()
            .map_err(|error| {
                if error.kind() == std::io::ErrorKind::NotFound {
                    Error::new(format!(
                        "cargo is missing; install a Rust toolchain to edit tools or merge updates; compiler output directory: {}",
                        output_dir.display()
                    ))
                } else {
                    Error::new(format!(
                        "could not run the workspace build: {error}; compiler output directory: {}",
                        output_dir.display()
                    ))
                }
            })?;
        if !status.success() {
            return Err(Error::new(format!(
                "workspace build failed; changes remain in the working clone; compiler output directory: {}",
                output_dir.display()
            )));
        }
        for name in self.bins.keys() {
            let binary = output_dir.join(name);
            if !binary.is_file() {
                return Err(Error::new(format!(
                    "workspace build succeeded but did not produce {}; compiler output directory: {}",
                    binary.display(),
                    output_dir.display()
                )));
            }
            let output = Command::new(&binary)
                .arg("--version")
                .output()
                .map_err(|error| {
                    Error::new(format!(
                        "could not verify {} --version: {error}; compiler output directory: {}",
                        binary.display(),
                        output_dir.display()
                    ))
                })?;
            let version = String::from_utf8_lossy(&output.stdout);
            if !output.status.success() || version.split_whitespace().last() != Some(commit) {
                return Err(Error::new(format!(
                    "{} --version did not report build commit {commit}; compiler output directory: {}",
                    binary.display(),
                    output_dir.display()
                )));
            }
        }
        Ok(output_dir)
    }

    pub fn relative_tool_directory(&self, repository: &Path, tool: &str) -> Result<PathBuf> {
        let directory = self.tool_directory(tool)?;
        directory
            .strip_prefix(repository)
            .map(Path::to_path_buf)
            .map_err(|_| Error::new("tool crate is outside the product repository"))
    }

    fn path_is_inside(&self, bytes: &[u8]) -> bool {
        let path = Path::new(OsStr::from_bytes(bytes));
        let mut relative = PathBuf::new();
        for component in path.components() {
            match component {
                Component::Normal(part) => relative.push(part),
                _ => return false,
            }
        }
        if self.relative_root.as_os_str().is_empty() {
            return true;
        }
        relative.starts_with(&self.relative_root)
    }
}

pub fn bin_names_at(
    repository: &Path,
    product: &str,
    commit: &str,
) -> Result<std::collections::BTreeSet<String>> {
    let entries = git::git_text(repository, &["ls-tree", "-r", "--name-only", commit])?;
    let files = entries
        .lines()
        .map(str::to_owned)
        .collect::<std::collections::BTreeSet<_>>();
    let manifests = files
        .iter()
        .filter(|path| path.rsplit('/').next() == Some("Cargo.toml"))
        .cloned()
        .collect::<Vec<_>>();
    let mut result = None;
    for manifest_path in manifests {
        let value = parse_toml(
            &git_blob(repository, commit, &manifest_path)?,
            &manifest_path,
        )?;
        let Some(workspace) = value.get("workspace") else {
            continue;
        };
        let parent = Path::new(&manifest_path)
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .to_path_buf();
        let members = member_paths_at(repository, commit, &parent, workspace)?;
        let mut names = std::collections::BTreeSet::new();
        let mut has_source = false;
        for member in members {
            let package_manifest = git_blob(repository, commit, &member)?;
            let package = parse_toml(&package_manifest, &member)?;
            let Some(package_name) = package
                .get("package")
                .and_then(|value| value.get("name"))
                .and_then(toml::Value::as_str)
            else {
                continue;
            };
            if package_name == format!("{product}-source") {
                has_source = true;
            }
            let package_root = Path::new(&member)
                .parent()
                .unwrap_or_else(|| Path::new("."));
            let binaries = binary_targets_at(package_root, &package, package_name, &files);
            for binary in binaries {
                if binary == product || binary.starts_with(&format!("{product}-")) {
                    names.insert(binary);
                }
            }
        }
        if has_source {
            if result.replace(names).is_some() {
                return Err(Error::new(format!(
                    "more than one workspace contains {product}-source at {commit}"
                )));
            }
        }
    }
    result.ok_or_else(|| Error::new(format!("no product workspace found at {commit}")))
}

fn member_paths_at(
    repository: &Path,
    commit: &str,
    root: &Path,
    workspace: &toml::Value,
) -> Result<Vec<String>> {
    let entries = workspace
        .get("members")
        .and_then(toml::Value::as_array)
        .ok_or_else(|| Error::new("product workspace must list its members"))?;
    let mut result = Vec::new();
    let excluded = workspace
        .get("exclude")
        .and_then(toml::Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(toml::Value::as_str)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    for entry in entries {
        let Some(member) = entry.as_str() else {
            continue;
        };
        if member.contains('*') {
            let (parent, pattern) = member
                .rsplit_once('/')
                .map_or((".", member), |(parent, pattern)| (parent, pattern));
            let parent = if root == Path::new(".") {
                PathBuf::from(parent)
            } else {
                root.join(parent)
            };
            let tree = if parent.as_os_str().is_empty() || parent == Path::new(".") {
                commit.to_owned()
            } else {
                format!("{commit}:{}", parent.display())
            };
            let directories = git::git_text(repository, &["ls-tree", "-d", "--name-only", &tree])?;
            for name in directories.lines() {
                if wildcard_str(pattern, name) {
                    let directory = if parent == Path::new(".") {
                        PathBuf::from(name)
                    } else {
                        parent.join(name)
                    };
                    let relative = directory.strip_prefix(root).unwrap_or(&directory);
                    if !excluded
                        .iter()
                        .any(|pattern| member_is_excluded(pattern, relative))
                    {
                        result.push(directory.join("Cargo.toml").to_string_lossy().into_owned());
                    }
                }
            }
        } else {
            let directory = if root == Path::new(".") {
                PathBuf::from(member)
            } else {
                root.join(member)
            };
            if !excluded
                .iter()
                .any(|pattern| member_is_excluded(pattern, Path::new(member)))
            {
                result.push(directory.join("Cargo.toml").to_string_lossy().into_owned());
            }
        }
    }
    result.sort();
    result.dedup();
    Ok(result)
}

fn wildcard_str(pattern: &str, name: &str) -> bool {
    let Some((prefix, suffix)) = pattern.split_once('*') else {
        return pattern == name;
    };
    !suffix.contains('*') && name.starts_with(prefix) && name.ends_with(suffix)
}

fn binary_targets_at(
    package_root: &Path,
    package: &toml::Value,
    package_name: &str,
    files: &std::collections::BTreeSet<String>,
) -> Vec<String> {
    let mut names = package
        .get("bin")
        .and_then(toml::Value::as_array)
        .map(|targets| {
            targets
                .iter()
                .filter_map(|target| target.get("name").and_then(toml::Value::as_str))
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if package
        .get("package")
        .and_then(|value| value.get("autobins"))
        .and_then(toml::Value::as_bool)
        != Some(false)
    {
        if files.contains(
            &package_root
                .join("src/main.rs")
                .to_string_lossy()
                .into_owned(),
        ) {
            names.push(package_name.to_owned());
        }
        let prefix = package_root.join("src/bin").to_string_lossy().into_owned();
        for file in files {
            let Some(relative) = file.strip_prefix(&format!("{prefix}/")) else {
                continue;
            };
            let mut components = relative.split('/');
            let Some(first) = components.next() else {
                continue;
            };
            if let Some(extensionless) = first.strip_suffix(".rs") {
                if components.next().is_none() {
                    names.push(extensionless.to_owned());
                }
            } else if components.next() == Some("main.rs") {
                names.push(first.to_owned());
            }
        }
    }
    names.sort();
    names.dedup();
    names
}

fn git_blob(repository: &Path, commit: &str, path: &str) -> Result<String> {
    let spec = format!("{commit}:{path}");
    let output = git::git_command(repository, [OsString::from("show"), OsString::from(spec)])?;
    String::from_utf8(output.stdout)
        .map_err(|_| Error::new(format!("Git file {path} at {commit} is not UTF-8")))
}

fn parse_toml(contents: &str, path: &str) -> Result<toml::Value> {
    toml::from_str(contents).map_err(|error| Error::new(format!("cannot parse {path}: {error}")))
}

fn find_manifests(root: &Path, directory: &Path, output: &mut Vec<PathBuf>) -> Result<()> {
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let path = entry.path();
        let file_type = entry.file_type()?;
        if file_type.is_file() && entry.file_name() == "Cargo.toml" {
            output.push(path);
        } else if file_type.is_dir() {
            let name = entry.file_name();
            if name == ".git" || name == "target" || name == "node_modules" || name == "build" {
                continue;
            }
            if path.starts_with(root) {
                find_manifests(root, &path, output)?;
            }
        }
    }
    Ok(())
}

fn read_toml(path: &Path) -> Result<toml::Value> {
    let contents = fs::read_to_string(path)
        .map_err(|error| Error::new(format!("cannot read {}: {error}", path.display())))?;
    toml::from_str(&contents)
        .map_err(|error| Error::new(format!("cannot parse {}: {error}", path.display())))
}

fn member_manifests(root: &Path, workspace: &toml::Value) -> Result<Vec<PathBuf>> {
    let mut members = Vec::new();
    let excluded = workspace
        .get("exclude")
        .and_then(toml::Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(toml::Value::as_str)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let Some(entries) = workspace.get("members").and_then(toml::Value::as_array) else {
        return Err(Error::new("product workspace must list its members"));
    };
    for entry in entries {
        let Some(member) = entry.as_str() else {
            continue;
        };
        if member.contains('*') {
            let (parent, pattern) = member
                .rsplit_once('/')
                .map_or((".", member), |(parent, pattern)| (parent, pattern));
            let directory = root.join(parent);
            for child in fs::read_dir(&directory).map_err(|error| {
                Error::new(format!(
                    "cannot read workspace member directory {}: {error}",
                    directory.display()
                ))
            })? {
                let child = child?;
                if !child.file_type()?.is_dir() || !wildcard_matches(pattern, &child.file_name()) {
                    continue;
                }
                let child_path = child.path();
                let relative = child_path.strip_prefix(root).unwrap_or(Path::new(""));
                if excluded
                    .iter()
                    .any(|pattern| member_is_excluded(pattern, relative))
                {
                    continue;
                }
                let manifest = child.path().join("Cargo.toml");
                if manifest.is_file() {
                    members.push(manifest);
                }
            }
        } else {
            let manifest = root.join(member).join("Cargo.toml");
            if excluded
                .iter()
                .any(|pattern| member_is_excluded(pattern, Path::new(member)))
            {
                continue;
            }
            if manifest.is_file() {
                members.push(manifest);
            }
        }
    }
    members.sort();
    members.dedup();
    Ok(members)
}

fn wildcard_matches(pattern: &str, name: &OsStr) -> bool {
    let Some(name) = name.to_str() else {
        return false;
    };
    let Some((prefix, suffix)) = pattern.split_once('*') else {
        return pattern == name;
    };
    !pattern[prefix.len() + 1..].contains('*') && name.starts_with(prefix) && name.ends_with(suffix)
}

fn member_is_excluded(pattern: &str, member: &Path) -> bool {
    let member = member.to_string_lossy();
    let Some((prefix, suffix)) = pattern.split_once('*') else {
        return member == pattern;
    };
    !suffix.contains('*') && member.starts_with(prefix) && member.ends_with(suffix)
}

fn binary_targets(
    manifest: &Path,
    package: &toml::Value,
    package_name: &str,
) -> Result<Vec<String>> {
    let mut names = package
        .get("bin")
        .and_then(toml::Value::as_array)
        .map(|targets| {
            targets
                .iter()
                .filter_map(|target| target.get("name").and_then(toml::Value::as_str))
                .map(str::to_owned)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if package
        .get("package")
        .and_then(|value| value.get("autobins"))
        .and_then(toml::Value::as_bool)
        == Some(false)
    {
        names.sort();
        names.dedup();
        return Ok(names);
    }
    let Some(root) = manifest.parent() else {
        return Ok(names);
    };
    if root.join("src/main.rs").is_file() {
        names.push(package_name.to_owned());
    }
    let bin_directory = root.join("src/bin");
    if bin_directory.is_dir() {
        for entry in fs::read_dir(&bin_directory)? {
            let entry = entry?;
            if entry.file_type()?.is_file() {
                if let Some(file_name) = entry.path().file_stem().and_then(OsStr::to_str) {
                    names.push(file_name.to_owned());
                }
            } else if entry.file_type()?.is_dir() && entry.path().join("main.rs").is_file() {
                if let Some(file_name) = entry.file_name().to_str() {
                    names.push(file_name.to_owned());
                }
            }
        }
    }
    names.sort();
    names.dedup();
    Ok(names)
}
