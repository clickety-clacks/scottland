use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

#[derive(Default)]
pub struct Snapshot {
    children: BTreeMap<u32, Vec<u32>>,
    executable_names: BTreeMap<u32, String>,
}

#[derive(Clone, Default, Debug)]
pub struct Tree {
    pub pids: BTreeSet<u32>,
    pub executable_names: BTreeSet<String>,
}

impl Snapshot {
    pub fn read() -> Self {
        let mut snapshot = Self::default();
        let Ok(entries) = fs::read_dir("/proc") else {
            return snapshot;
        };

        for entry in entries.filter_map(Result::ok) {
            let Some(pid) = entry
                .file_name()
                .to_str()
                .and_then(|name| name.parse::<u32>().ok())
            else {
                continue;
            };
            let path = entry.path();
            let Some(parent) = process_parent(&path.join("stat")) else {
                continue;
            };
            snapshot.children.entry(parent).or_default().push(pid);
            if let Ok(executable) = fs::read_link(path.join("exe")) {
                if let Some(name) = executable.file_name().and_then(|name| name.to_str()) {
                    snapshot.executable_names.insert(pid, name.to_string());
                }
            }
        }

        for children in snapshot.children.values_mut() {
            children.sort_unstable();
        }
        snapshot
    }

    pub fn tree(&self, root: u32) -> Tree {
        let mut tree = Tree::default();
        let mut pending = vec![root];
        while let Some(pid) = pending.pop() {
            if !tree.pids.insert(pid) {
                continue;
            }
            if let Some(name) = self.executable_names.get(&pid) {
                tree.executable_names.insert(name.clone());
            }
            if let Some(children) = self.children.get(&pid) {
                pending.extend(children.iter().rev().copied());
            }
        }
        tree
    }
}

fn process_parent(path: &Path) -> Option<u32> {
    let stat = fs::read_to_string(path).ok()?;
    let close = stat.rfind(')')?;
    // Fields after comm start with field 3 (state); ppid is field 4.
    stat[close + 1..].split_whitespace().nth(1)?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::process_parent;
    use std::fs;

    #[test]
    fn parses_parent_after_a_comm_with_spaces_and_parentheses() {
        let path = std::env::temp_dir().join(format!(
            "scottland-proc-stat-{}-{}",
            std::process::id(),
            std::thread::current().name().unwrap_or("test")
        ));
        fs::write(&path, "42 (name with ) parens) S 7 1 1 0 0").unwrap();
        assert_eq!(process_parent(&path), Some(7));
        fs::remove_file(path).unwrap();
    }
}
