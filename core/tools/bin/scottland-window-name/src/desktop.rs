use std::env;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, Default)]
pub struct Entry {
    pub name: Option<String>,
    pub categories: Vec<String>,
}

#[derive(Default)]
pub struct Index {
    entries: Vec<(String, Option<String>, Option<String>, Entry)>,
}

impl Index {
    pub fn read_from(env: &dyn Fn(&str) -> Option<OsString>) -> Self {
        let mut files = Vec::new();
        for root in data_roots(env) {
            collect_desktop_files(&root.join("applications"), &mut files);
        }

        let entries = files
            .into_iter()
            .filter_map(|(id, path)| {
                let source = fs::read_to_string(path).ok()?;
                let (entry, startup_class, exec_name) = parse(&source);
                Some((id, startup_class, exec_name, entry))
            })
            .collect();
        Self { entries }
    }

    pub fn find(&self, app_id: &str) -> Entry {
        if app_id.is_empty() {
            return Entry::default();
        }
        let wanted = app_id.to_lowercase();
        self.entries
            .iter()
            .find(|(id, _, _, _)| id.to_lowercase() == wanted)
            .or_else(|| {
                self.entries.iter().find(|(_, startup_class, _, _)| {
                    startup_class
                        .as_deref()
                        .is_some_and(|value| value.to_lowercase() == wanted)
                })
            })
            .or_else(|| {
                self.entries.iter().find(|(_, _, exec_name, _)| {
                    exec_name
                        .as_deref()
                        .is_some_and(|value| value.to_lowercase() == wanted)
                })
            })
            .map(|(_, _, _, entry)| entry.clone())
            .unwrap_or_default()
    }
}

fn data_roots(env: &dyn Fn(&str) -> Option<OsString>) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Some(home) = env("XDG_DATA_HOME").filter(|value| !value.is_empty()) {
        roots.push(PathBuf::from(home));
    } else if let Some(home) = env("HOME").filter(|value| !value.is_empty()) {
        roots.push(PathBuf::from(home).join(".local/share"));
    }

    if let Some(data_dirs) = env("XDG_DATA_DIRS").filter(|value| !value.is_empty()) {
        roots.extend(env::split_paths(&data_dirs));
    } else {
        roots.push(PathBuf::from("/usr/local/share"));
        roots.push(PathBuf::from("/usr/share"));
    }
    roots
}

fn collect_desktop_files(directory: &Path, files: &mut Vec<(String, PathBuf)>) {
    let Ok(entries) = fs::read_dir(directory) else {
        return;
    };
    let mut entries: Vec<_> = entries.filter_map(Result::ok).collect();
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        if kind.is_dir() {
            collect_desktop_files(&path, files);
        } else if kind.is_file()
            && path.extension().and_then(|extension| extension.to_str()) == Some("desktop")
        {
            let id = desktop_id(directory, &path);
            files.push((id, path));
        }
    }
}

fn desktop_id(root: &Path, path: &Path) -> String {
    let applications = root
        .ancestors()
        .find(|candidate| {
            candidate
                .file_name()
                .is_some_and(|name| name == "applications")
        })
        .unwrap_or(root);
    let relative = path.strip_prefix(applications).unwrap_or(path);
    relative
        .components()
        .filter_map(|component| component.as_os_str().to_str())
        .collect::<Vec<_>>()
        .join("-")
        .strip_suffix(".desktop")
        .unwrap_or_default()
        .to_string()
}

fn parse(source: &str) -> (Entry, Option<String>, Option<String>) {
    let mut in_desktop_entry = false;
    let mut name = None;
    let mut categories = Vec::new();
    let mut startup_class = None;
    let mut exec_name = None;

    for line in source.lines() {
        let line = line.trim();
        if line.starts_with('[') && line.ends_with(']') {
            in_desktop_entry = line == "[Desktop Entry]";
            continue;
        }
        if !in_desktop_entry || line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let value = decode_value(value.trim());
        match key.trim() {
            "Name" => name = Some(value),
            "Categories" => {
                categories = value
                    .split(';')
                    .filter(|category| !category.is_empty())
                    .map(str::to_string)
                    .collect();
            }
            "StartupWMClass" => startup_class = Some(value),
            "Exec" => exec_name = first_exec_name(&value),
            _ => {}
        }
    }

    (Entry { name, categories }, startup_class, exec_name)
}

fn decode_value(value: &str) -> String {
    let mut decoded = String::with_capacity(value.len());
    let mut chars = value.chars();
    while let Some(ch) = chars.next() {
        if ch == '\\' {
            match chars.next() {
                Some('s') => decoded.push(' '),
                Some('n') => decoded.push('\n'),
                Some('t') => decoded.push('\t'),
                Some('r') => decoded.push('\r'),
                Some('\\') => decoded.push('\\'),
                Some(other) => {
                    decoded.push('\\');
                    decoded.push(other);
                }
                None => decoded.push('\\'),
            }
        } else {
            decoded.push(ch);
        }
    }
    decoded
}

fn first_exec_name(exec: &str) -> Option<String> {
    let mut token = String::new();
    let mut quote = None;
    let mut escaped = false;
    for ch in exec.chars() {
        if escaped {
            token.push(ch);
            escaped = false;
            continue;
        }
        if ch == '\\' && quote != Some('\'') {
            escaped = true;
            continue;
        }
        if let Some(open) = quote {
            if ch == open {
                quote = None;
            } else {
                token.push(ch);
            }
            continue;
        }
        if ch == '\'' || ch == '"' {
            quote = Some(ch);
        } else if ch.is_whitespace() {
            break;
        } else {
            token.push(ch);
        }
    }
    if token.is_empty() {
        return None;
    }
    Path::new(&token)
        .file_name()
        .and_then(|name| name.to_str())
        .map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::{first_exec_name, parse};

    #[test]
    fn parses_only_the_desktop_entry_and_unescapes_standard_fields() {
        let (entry, startup_class, exec_name) = parse(
            "[Desktop Entry]\nName=Reader\\sApp\nCategories=Office;Viewer;\nStartupWMClass=reader\nExec=reader --open %f\n[Other]\nName=ignored\n",
        );
        assert_eq!(entry.name.as_deref(), Some("Reader App"));
        assert_eq!(entry.categories, ["Office", "Viewer"]);
        assert_eq!(startup_class.as_deref(), Some("reader"));
        assert_eq!(exec_name.as_deref(), Some("reader"));
    }

    #[test]
    fn reads_a_quoted_executable_basename_without_app_specific_rules() {
        assert_eq!(
            first_exec_name("\"/opt/example tool\" --flag"),
            Some("example tool".to_string())
        );
    }
}
