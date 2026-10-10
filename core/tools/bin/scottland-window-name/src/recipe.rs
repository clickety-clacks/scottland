use regex::Regex;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::PathBuf;
use toml::Value;

pub const WINDOW_FIELDS: [&str; 6] = [
    "window",
    "pid",
    "app_id",
    "title",
    "app_name",
    "title_content",
];
const MPRIS_FIELDS: [&str; 4] = ["track", "artist", "album", "status"];

#[derive(Clone, Debug)]
pub struct Warning {
    pub recipe: String,
    pub reason: String,
}

#[derive(Clone, Debug)]
pub enum Token {
    Text(String),
    Field(String),
}

#[derive(Clone, Debug)]
pub struct Template {
    pub tokens: Vec<Token>,
    pub fields: BTreeSet<String>,
}

#[derive(Clone, Debug, Default)]
pub struct Match {
    pub app_id: Option<Regex>,
    pub title: Option<Regex>,
    pub desktop_category: Option<String>,
    pub process: Option<Vec<String>>,
}

#[derive(Clone, Debug)]
pub enum Source {
    Mpris {
        timeout_ms: u64,
    },
    Command {
        argv: Vec<String>,
        output: CommandOutput,
        fields: Option<BTreeMap<String, String>>,
        basis: Option<String>,
        timeout_ms: u64,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CommandOutput {
    Json,
    Text,
}

#[derive(Clone, Debug)]
pub struct Recipe {
    pub id: String,
    pub name: Template,
    pub priority: i64,
    pub matching: Match,
    pub source: Option<Source>,
    pub title_capture_names: BTreeSet<String>,
}

#[derive(Default)]
pub struct Set {
    pub recipes: Vec<Recipe>,
    pub warnings: Vec<Warning>,
}

pub enum MatchOutcome {
    NoMatch(String),
    Match(BTreeMap<String, String>),
}

impl Recipe {
    pub fn capture_fields(&self, title: &str) -> Option<BTreeMap<String, String>> {
        let Some(regex) = &self.matching.title else {
            return Some(BTreeMap::new());
        };
        let captures = regex.captures(title)?;
        let mut fields = BTreeMap::new();
        for name in &self.title_capture_names {
            fields.insert(
                name.clone(),
                captures
                    .name(name)
                    .map(|value| value.as_str())
                    .unwrap_or_default()
                    .to_string(),
            );
        }
        Some(fields)
    }

    pub fn matches(
        &self,
        app_id: &str,
        title: &str,
        categories: &[String],
        sole_owner: bool,
        process_names: &BTreeSet<String>,
    ) -> MatchOutcome {
        if self
            .matching
            .app_id
            .as_ref()
            .is_some_and(|pattern| !pattern.is_match(app_id))
        {
            return MatchOutcome::NoMatch("app_id did not match".to_string());
        }
        if self
            .matching
            .title
            .as_ref()
            .is_some_and(|pattern| !pattern.is_match(title))
        {
            return MatchOutcome::NoMatch("title did not match".to_string());
        }
        if let Some(category) = &self.matching.desktop_category {
            if !categories.iter().any(|candidate| candidate == category) {
                return MatchOutcome::NoMatch("desktop category did not match".to_string());
            }
        }
        if let Some(names) = &self.matching.process {
            if !sole_owner {
                return MatchOutcome::NoMatch(
                    "process match requires a sole-owner window".to_string(),
                );
            }
            if !names.iter().any(|name| process_names.contains(name)) {
                return MatchOutcome::NoMatch("process did not match".to_string());
            }
        }
        MatchOutcome::Match(self.capture_fields(title).unwrap_or_default())
    }
}

pub fn load(directories: &[PathBuf]) -> Set {
    let mut winners = BTreeMap::<String, PathBuf>::new();
    for directory in directories {
        let Ok(entries) = fs::read_dir(directory) else {
            continue;
        };
        let mut paths: Vec<_> = entries.filter_map(Result::ok).collect();
        paths.sort_by_key(|entry| entry.file_name());
        for entry in paths {
            let path = entry.path();
            if path.extension().and_then(|extension| extension.to_str()) != Some("toml") {
                continue;
            }
            let Some(id) = path
                .file_stem()
                .and_then(|stem| stem.to_str())
                .filter(|id| !id.is_empty())
            else {
                continue;
            };
            winners
                .entry(id.to_string())
                .or_insert_with(|| path.to_path_buf());
        }
    }

    let mut set = Set::default();
    for (id, path) in winners {
        let source = match fs::read_to_string(&path) {
            Ok(source) => source,
            Err(error) => {
                set.warnings.push(Warning {
                    recipe: id,
                    reason: format!("cannot read winning recipe: {error}"),
                });
                continue;
            }
        };
        let document = match toml::from_str::<Value>(&source) {
            Ok(document) => document,
            Err(error) => {
                set.warnings.push(Warning {
                    recipe: id,
                    reason: format!("invalid TOML: {error}"),
                });
                continue;
            }
        };
        match parse_recipe(&id, &document, &mut set.warnings) {
            Ok(Some(recipe)) => set.recipes.push(recipe),
            Ok(None) => {}
            Err(reason) => set.warnings.push(Warning { recipe: id, reason }),
        }
    }
    set.recipes
        .sort_by(|a, b| b.priority.cmp(&a.priority).then_with(|| a.id.cmp(&b.id)));
    set.warnings.sort_by(|a, b| {
        a.recipe
            .cmp(&b.recipe)
            .then_with(|| a.reason.cmp(&b.reason))
    });
    set
}

fn parse_recipe(
    id: &str,
    document: &Value,
    warnings: &mut Vec<Warning>,
) -> Result<Option<Recipe>, String> {
    let table = document
        .as_table()
        .ok_or_else(|| "recipe root must be a TOML table".to_string())?;
    if table.get("enabled").and_then(Value::as_bool) == Some(false) {
        return Ok(None);
    }
    if table.contains_key("enabled") && table.get("enabled").and_then(Value::as_bool).is_none() {
        return Err("enabled must be a boolean".to_string());
    }

    warn_unknown_keys(
        id,
        table,
        &["name", "priority", "enabled", "match", "source"],
        "",
        warnings,
    );
    let name = table
        .get("name")
        .and_then(Value::as_str)
        .ok_or_else(|| "missing or non-string name".to_string())?;
    let name = parse_template(name)?;
    let priority = match table.get("priority") {
        None => 0,
        Some(value) => value
            .as_integer()
            .ok_or_else(|| "priority must be an integer".to_string())?,
    };

    let matching = parse_match(id, table.get("match"), warnings)?;
    let title_capture_names = matching
        .title
        .as_ref()
        .map(|regex| {
            regex
                .capture_names()
                .flatten()
                .map(str::to_string)
                .collect::<BTreeSet<_>>()
        })
        .unwrap_or_default();
    let source = parse_source(id, table.get("source"), warnings)?;

    for field in &title_capture_names {
        if WINDOW_FIELDS.contains(&field.as_str()) {
            return Err(format!(
                "title capture {field:?} conflicts with a window field"
            ));
        }
    }
    let source_fields = match &source {
        Some(Source::Mpris { .. }) => MPRIS_FIELDS
            .iter()
            .map(|field| field.to_string())
            .collect::<BTreeSet<_>>(),
        Some(Source::Command {
            output: CommandOutput::Text,
            ..
        }) => ["text".to_string()].into_iter().collect(),
        Some(Source::Command {
            fields: Some(fields),
            ..
        }) => fields.keys().cloned().collect(),
        _ => BTreeSet::new(),
    };
    for field in &source_fields {
        if WINDOW_FIELDS.contains(&field.as_str()) {
            return Err(format!(
                "source field {field:?} conflicts with a window field"
            ));
        }
        if title_capture_names.contains(field) {
            return Err(format!(
                "source field {field:?} conflicts with a title capture"
            ));
        }
    }
    if let Some(field) = title_capture_names.intersection(&source_fields).next() {
        return Err(format!(
            "title capture {field:?} conflicts with a source field"
        ));
    }

    Ok(Some(Recipe {
        id: id.to_string(),
        name,
        priority,
        matching,
        source,
        title_capture_names,
    }))
}

fn parse_match(
    id: &str,
    value: Option<&Value>,
    warnings: &mut Vec<Warning>,
) -> Result<Match, String> {
    let Some(value) = value else {
        return Ok(Match::default());
    };
    let table = value
        .as_table()
        .ok_or_else(|| "match must be a table".to_string())?;
    warn_unknown_keys(
        id,
        table,
        &["app_id", "title", "desktop_category", "process"],
        "match.",
        warnings,
    );

    let regex = |key: &str| -> Result<Option<Regex>, String> {
        let Some(value) = table.get(key) else {
            return Ok(None);
        };
        let pattern = value
            .as_str()
            .ok_or_else(|| format!("match.{key} must be a string"))?;
        Regex::new(pattern)
            .map(Some)
            .map_err(|error| format!("invalid match.{key} regex: {error}"))
    };

    let desktop_category = table
        .get("desktop_category")
        .map(|value| {
            value
                .as_str()
                .map(str::to_string)
                .ok_or_else(|| "match.desktop_category must be a string".to_string())
        })
        .transpose()?;
    let process = table
        .get("process")
        .map(|value| {
            value
                .as_array()
                .ok_or_else(|| "match.process must be an array of strings".to_string())?
                .iter()
                .map(|item| {
                    item.as_str()
                        .map(str::to_string)
                        .ok_or_else(|| "match.process must be an array of strings".to_string())
                })
                .collect::<Result<Vec<_>, _>>()
        })
        .transpose()?;

    Ok(Match {
        app_id: regex("app_id")?,
        title: regex("title")?,
        desktop_category,
        process,
    })
}

fn parse_source(
    id: &str,
    value: Option<&Value>,
    warnings: &mut Vec<Warning>,
) -> Result<Option<Source>, String> {
    let Some(value) = value else {
        return Ok(None);
    };
    let table = value
        .as_table()
        .ok_or_else(|| "source must be a table".to_string())?;
    warn_unknown_keys(
        id,
        table,
        &[
            "provider",
            "command",
            "output",
            "fields",
            "basis",
            "timeout_ms",
        ],
        "source.",
        warnings,
    );

    let provider = table
        .get("provider")
        .and_then(Value::as_str)
        .ok_or_else(|| "source.provider must be a string".to_string())?;
    let timeout_ms = table
        .get("timeout_ms")
        .map(|value| {
            value
                .as_integer()
                .filter(|milliseconds| *milliseconds >= 0)
                .map(|milliseconds| milliseconds as u64)
                .ok_or_else(|| "source.timeout_ms must be a non-negative integer".to_string())
        })
        .transpose()?
        .unwrap_or(1000);

    match provider {
        "mpris" => {
            if ["command", "output", "fields", "basis"]
                .iter()
                .any(|key| table.contains_key(*key))
            {
                return Err("command-only source keys require provider = \"command\"".to_string());
            }
            Ok(Some(Source::Mpris { timeout_ms }))
        }
        "command" => {
            let argv = table
                .get("command")
                .and_then(Value::as_array)
                .ok_or_else(|| "source.command must be an argv array".to_string())?
                .iter()
                .map(|value| {
                    value
                        .as_str()
                        .map(str::to_string)
                        .ok_or_else(|| "source.command must contain only strings".to_string())
                })
                .collect::<Result<Vec<_>, _>>()?;
            if argv.is_empty() || argv[0].is_empty() {
                return Err("source.command must have a non-empty executable".to_string());
            }
            let output = match table
                .get("output")
                .and_then(Value::as_str)
                .unwrap_or("json")
            {
                "json" => CommandOutput::Json,
                "text" => CommandOutput::Text,
                _ => return Err("source.output must be \"json\" or \"text\"".to_string()),
            };
            let fields = table
                .get("fields")
                .map(|value| {
                    let table = value
                        .as_table()
                        .ok_or_else(|| "source.fields must be a table".to_string())?;
                    let mut fields = BTreeMap::new();
                    for (field, pointer) in table {
                        let pointer = pointer.as_str().ok_or_else(|| {
                            format!("source.fields.{field} must be a JSON Pointer string")
                        })?;
                        validate_pointer(pointer).map_err(|error| {
                            format!("invalid source.fields.{field} JSON Pointer: {error}")
                        })?;
                        fields.insert(field.clone(), pointer.to_string());
                    }
                    Ok::<_, String>(fields)
                })
                .transpose()?;
            let basis = table
                .get("basis")
                .map(|value| {
                    let pointer = value
                        .as_str()
                        .ok_or_else(|| "source.basis must be a JSON Pointer string".to_string())?;
                    validate_pointer(pointer)
                        .map_err(|error| format!("invalid source.basis JSON Pointer: {error}"))?;
                    Ok::<_, String>(pointer.to_string())
                })
                .transpose()?;
            if output == CommandOutput::Text && (fields.is_some() || basis.is_some()) {
                return Err("source.fields and source.basis require JSON output".to_string());
            }
            Ok(Some(Source::Command {
                argv,
                output,
                fields,
                basis,
                timeout_ms,
            }))
        }
        _ => Err(format!("unknown source.provider {provider:?}")),
    }
}

fn validate_pointer(pointer: &str) -> Result<(), String> {
    if pointer.is_empty() {
        return Ok(());
    }
    if !pointer.starts_with('/') {
        return Err("a non-empty pointer must start with '/'".to_string());
    }
    let mut chars = pointer.chars();
    while let Some(ch) = chars.next() {
        if ch == '~' && !matches!(chars.next(), Some('0' | '1')) {
            return Err("a '~' must be followed by '0' or '1'".to_string());
        }
    }
    Ok(())
}

fn parse_template(source: &str) -> Result<Template, String> {
    let mut tokens = Vec::new();
    let mut fields = BTreeSet::new();
    let mut literal = String::new();
    let mut chars = source.chars().peekable();
    while let Some(ch) = chars.next() {
        match ch {
            '{' if chars.peek() == Some(&'{') => {
                chars.next();
                literal.push('{');
            }
            '}' if chars.peek() == Some(&'}') => {
                chars.next();
                literal.push('}');
            }
            '{' => {
                if !literal.is_empty() {
                    tokens.push(Token::Text(std::mem::take(&mut literal)));
                }
                let mut field = String::new();
                let mut closed = false;
                for next in chars.by_ref() {
                    if next == '}' {
                        closed = true;
                        break;
                    }
                    if next == '{' {
                        return Err("a template field cannot contain '{'".to_string());
                    }
                    field.push(next);
                }
                if !closed || field.is_empty() {
                    return Err("template fields must be non-empty and closed with '}'".to_string());
                }
                fields.insert(field.clone());
                tokens.push(Token::Field(field));
            }
            '}' => return Err("a literal '}' must be written as '}}'".to_string()),
            other => literal.push(other),
        }
    }
    if !literal.is_empty() {
        tokens.push(Token::Text(literal));
    }
    Ok(Template { tokens, fields })
}

pub fn parse_template_for_command(source: &str) -> Result<Vec<Token>, String> {
    parse_template(source).map(|template| template.tokens)
}

fn warn_unknown_keys(
    id: &str,
    table: &toml::map::Map<String, Value>,
    known: &[&str],
    prefix: &str,
    warnings: &mut Vec<Warning>,
) {
    for key in table.keys() {
        if !known.contains(&key.as_str()) {
            warnings.push(Warning {
                recipe: id.to_string(),
                reason: format!("unknown key {prefix}{key}"),
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{CommandOutput, MatchOutcome, Source, load, parse_template, validate_pointer};
    use std::collections::BTreeSet;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct TempDir(PathBuf);

    impl TempDir {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "scottland-window-names-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }

        fn write(&self, name: &str, source: &str) {
            fs::write(self.0.join(name), source).unwrap();
        }

        fn path(&self) -> &std::path::Path {
            &self.0
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn template_braces_are_literal_only_when_doubled() {
        let template = parse_template("{{{track}}}").unwrap();
        assert_eq!(template.fields, ["track".to_string()].into_iter().collect());
        assert!(parse_template("{track").is_err());
        assert!(parse_template("track}").is_err());
    }

    #[test]
    fn validates_json_pointer_escapes() {
        assert!(validate_pointer("").is_ok());
        assert!(validate_pointer("/metadata/title").is_ok());
        assert!(validate_pointer("metadata/title").is_err());
        assert!(validate_pointer("/bad~2escape").is_err());
    }

    #[test]
    fn winning_disable_or_broken_file_suppresses_later_recipe() {
        let user = TempDir::new();
        let builtin = TempDir::new();
        user.write(
            "disabled.toml",
            "enabled = false\nname = { broken = \"but ignored\" }\n",
        );
        user.write("broken.toml", "name = \"{bad\"\n");
        builtin.write("disabled.toml", "name = \"lower priority\"\n");
        builtin.write("broken.toml", "name = \"also lower priority\"\n");
        let set = load(&[user.path().to_path_buf(), builtin.path().to_path_buf()]);
        assert!(set.recipes.is_empty());
        assert_eq!(set.warnings.len(), 1);
        assert_eq!(set.warnings[0].recipe, "broken");
    }

    #[test]
    fn unknown_keys_warn_without_disabling_a_recipe() {
        let directory = TempDir::new();
        directory.write("title.toml", "name = \"{title}\"\nprority = 7\n");
        let set = load(&[directory.path().to_path_buf()]);
        assert_eq!(set.recipes.len(), 1);
        assert_eq!(set.warnings[0].reason, "unknown key prority");
    }

    #[test]
    fn recipe_order_is_priority_then_id() {
        let directory = TempDir::new();
        directory.write("low.toml", "name = \"low\"\npriority = 1\n");
        directory.write("same-z.toml", "name = \"z\"\npriority = 9\n");
        directory.write("same-a.toml", "name = \"a\"\npriority = 9\n");
        let set = load(&[directory.path().to_path_buf()]);
        let ids = set
            .recipes
            .iter()
            .map(|recipe| recipe.id.as_str())
            .collect::<Vec<_>>();
        assert_eq!(ids, ["same-a", "same-z", "low"]);
    }

    #[test]
    fn every_match_condition_must_pass_and_named_title_captures_are_available() {
        let directory = TempDir::new();
        directory.write(
            "reader.toml",
            "name = \"{chapter}\"\n[match]\napp_id = '^org\\.example\\.reader$'\ntitle = '^Chapter (?P<chapter>.+)$'\ndesktop_category = 'Office'\n",
        );
        let set = load(&[directory.path().to_path_buf()]);
        let recipe = &set.recipes[0];
        let no_processes = BTreeSet::new();
        let matched = recipe.matches(
            "org.example.reader",
            "Chapter Seven",
            &["Office".to_string()],
            true,
            &no_processes,
        );
        assert!(
            matches!(matched, MatchOutcome::Match(fields) if fields.get("chapter").map(String::as_str) == Some("Seven"))
        );
        assert!(matches!(
            recipe.matches(
                "org.example.reader",
                "Chapter Seven",
                &["Game".to_string()],
                true,
                &no_processes,
            ),
            MatchOutcome::NoMatch(_)
        ));
    }

    #[test]
    fn source_and_title_capture_conflicts_are_rejected() {
        let directory = TempDir::new();
        directory.write(
            "conflict.toml",
            "name = \"{track}\"\n[match]\ntitle = \"(?P<track>.+)\"\n[source]\nprovider = \"mpris\"\n",
        );
        let set = load(&[directory.path().to_path_buf()]);
        assert!(set.recipes.is_empty());
        assert!(set.warnings[0].reason.contains("conflicts"));
    }

    #[test]
    fn command_fields_are_loaded_with_explicit_pointer_mapping() {
        let directory = TempDir::new();
        directory.write(
            "command.toml",
            "name = \"{session}\"\n[source]\nprovider = \"command\"\ncommand = [\"example\"]\n[source.fields]\nsession = \"/current/session\"\n",
        );
        let set = load(&[directory.path().to_path_buf()]);
        assert!(matches!(
            &set.recipes[0].source,
            Some(Source::Command {
                output: CommandOutput::Json,
                ..
            })
        ));
    }

    #[test]
    fn process_conditions_require_a_sole_owner() {
        let directory = TempDir::new();
        directory.write(
            "process.toml",
            "name = \"{title}\"\n[match]\nprocess = [\"player\"]\n",
        );
        let set = load(&[directory.path().to_path_buf()]);
        let recipe = &set.recipes[0];
        let process_names = BTreeSet::from(["player".to_string()]);
        assert!(matches!(
            recipe.matches("app", "title", &[], false, &process_names),
            MatchOutcome::NoMatch(_)
        ));
        assert!(matches!(
            recipe.matches("app", "title", &[], true, &process_names),
            MatchOutcome::Match(_)
        ));
    }

    #[test]
    fn command_output_text_is_an_explicit_source_kind() {
        let directory = TempDir::new();
        directory.write(
            "text.toml",
            "name = \"{text}\"\n[source]\nprovider = \"command\"\ncommand = [\"x\"]\noutput = \"text\"\n",
        );
        let set = load(&[directory.path().to_path_buf()]);
        assert!(matches!(
            &set.recipes[0].source,
            Some(Source::Command {
                output: CommandOutput::Text,
                ..
            })
        ));
    }
}
