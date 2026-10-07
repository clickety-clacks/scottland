use crate::error::{Error, Result};
use std::env;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct Paths {
    pub product: String,
    pub source_repo: PathBuf,
    pub builds: PathBuf,
    pub switch: PathBuf,
    pub base_tools: PathBuf,
    pub package_source: PathBuf,
    pub package_bundle: PathBuf,
    pub package_commit: PathBuf,
    pub working_clone: PathBuf,
    pub build_target: PathBuf,
    pub tool_bin: PathBuf,
}

impl Paths {
    pub fn system(product: &str) -> Result<Self> {
        validate_product(product)?;
        let home = absolute_var("HOME").ok_or_else(|| {
            Error::new("HOME must be set to an absolute path to locate the working clone")
        })?;
        let state_home =
            absolute_var("XDG_STATE_HOME").unwrap_or_else(|| home.join(".local/state"));
        let cache_home = absolute_var("XDG_CACHE_HOME").unwrap_or_else(|| home.join(".cache"));
        Ok(Self::with_homes(
            product,
            Path::new("/"),
            state_home,
            cache_home,
        ))
    }

    #[cfg(test)]
    pub fn under_root(product: &str, root: &Path, state_home: PathBuf) -> Self {
        Self::with_homes(product, root, state_home, PathBuf::from("cache"))
    }

    pub fn system_root(product: &str) -> Result<Self> {
        validate_product(product)?;
        Ok(Self::with_homes(
            product,
            Path::new("/"),
            PathBuf::new(),
            PathBuf::new(),
        ))
    }

    fn with_homes(product: &str, root: &Path, state_home: PathBuf, cache_home: PathBuf) -> Self {
        let var = root.join("var/lib").join(product);
        let tools = root.join("usr/lib").join(product).join("tools");
        let share = root.join("usr/share").join(product).join("source");
        Self {
            product: product.to_owned(),
            source_repo: var.join("source"),
            builds: var.join("builds"),
            switch: tools.join("current"),
            base_tools: tools.join("base"),
            package_source: share.clone(),
            package_bundle: share.join("base.bundle"),
            package_commit: share.join("base.commit"),
            working_clone: state_home.join(product).join("source"),
            build_target: cache_home.join(product).join("source-target"),
            tool_bin: root.join("usr/bin"),
        }
    }

    pub fn installed_ref(&self) -> PathBuf {
        self.source_repo.join("refs/heads/installed")
    }

    pub fn switch_commit(&self) -> PathBuf {
        self.switch.join("commit")
    }

    pub fn switch_commit_for(&self, commit: &str) -> PathBuf {
        self.build_dir(commit).join("commit")
    }

    pub fn tool_link(&self, tool: &str) -> PathBuf {
        self.tool_bin.join(tool)
    }

    pub fn tool_target(&self, tool: &str) -> PathBuf {
        self.switch.join("bin").join(tool)
    }

    pub fn build_dir(&self, commit: &str) -> PathBuf {
        self.builds.join(commit)
    }

    pub fn build_source(&self, commit: &str) -> PathBuf {
        self.build_dir(commit).join("source")
    }

    pub fn build_bin(&self, commit: &str) -> PathBuf {
        self.build_dir(commit).join("bin")
    }
}

pub fn validate_product(product: &str) -> Result<()> {
    if product.is_empty()
        || !product
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
        || product.starts_with('-')
        || product.ends_with('-')
    {
        return Err(Error::new(
            "product name must be a lowercase name containing only letters, digits, and hyphens",
        ));
    }
    Ok(())
}

fn absolute_var(name: &str) -> Option<PathBuf> {
    env::var_os(name)
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
}
