use crate::command::{Cache as CommandCache, ResultData as CommandResult};
use crate::desktop;
use crate::ipc::Window;
use crate::mpris::{self, Player};
use crate::process;
use crate::recipe::{
    CommandOutput, MatchOutcome, Recipe, Set as RecipeSet, Source, Token, Warning,
};
use scottland::session::Environment;
use serde_json::{Map, Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::ffi::OsStr;

#[derive(Clone, Debug)]
pub struct Output {
    pub window: u64,
    pub missing: bool,
    pub name: Option<String>,
    pub basis: String,
    pub recipe: Option<String>,
    pub fields: BTreeMap<String, String>,
    pub warnings: Vec<Warning>,
    pub tried: Option<Vec<(String, String)>>,
}

#[derive(Clone)]
struct Context {
    window: Window,
    app_name: String,
    categories: Vec<String>,
    title_content: String,
    sole_owner: bool,
    tree: process::Tree,
}

impl Context {
    fn window_fields(&self) -> BTreeMap<String, String> {
        BTreeMap::from([
            ("window".to_string(), self.window.id.to_string()),
            ("pid".to_string(), self.window.pid.to_string()),
            ("app_id".to_string(), self.window.app_id.clone()),
            ("title".to_string(), self.window.title.clone()),
            ("app_name".to_string(), self.app_name.clone()),
            ("title_content".to_string(), self.title_content.clone()),
        ])
    }

    fn command_input(&self) -> Value {
        let fields = self.window_fields();
        let mut object = Map::new();
        for (key, value) in fields {
            object.insert(key, Value::String(value));
        }
        Value::Object(object)
    }
}

pub fn name(
    requested: &[u64],
    windows: &[Window],
    desktop_index: &desktop::Index,
    process_snapshot: &process::Snapshot,
    recipes: &RecipeSet,
    bus_address: Option<&OsStr>,
    command_environment: &Environment,
    explain: bool,
) -> Vec<Output> {
    let mut owner_counts = BTreeMap::<u32, usize>::new();
    let mut app_id_counts = BTreeMap::<String, usize>::new();
    for window in windows {
        *owner_counts.entry(window.pid).or_default() += 1;
        *app_id_counts
            .entry(window.app_id.to_lowercase())
            .or_default() += 1;
    }

    let mut contexts = BTreeMap::new();
    for window in windows {
        let entry = desktop_index.find(&window.app_id);
        let title_content = title_content(&window.title, &window.app_id, &entry.name);
        let context = Context {
            window: window.clone(),
            app_name: entry.name.unwrap_or_default(),
            categories: entry.categories,
            title_content,
            sole_owner: owner_counts.get(&window.pid) == Some(&1),
            tree: process_snapshot.tree(window.pid),
        };
        contexts.insert(window.id, context);
    }

    let mut ids = requested.to_vec();
    ids.sort_unstable();
    ids.dedup();
    let mut command_cache = CommandCache::default();
    let mut mpris_cache: Option<(Vec<Player>, u64)> = None;
    let mut named = BTreeMap::new();
    for id in ids {
        let Some(context) = contexts.get(&id) else {
            named.insert(id, None);
            continue;
        };
        let owner_app_id_count = app_id_counts
            .get(&context.window.app_id.to_lowercase())
            .copied()
            .unwrap_or_default();
        let output = name_one(
            context,
            owner_app_id_count,
            recipes,
            bus_address,
            command_environment,
            &mut command_cache,
            &mut mpris_cache,
            explain,
        );
        named.insert(id, Some(output));
    }

    requested
        .iter()
        .map(|id| match named.get(id).and_then(Option::as_ref) {
            Some(output) => output.clone(),
            None => Output {
                window: *id,
                missing: true,
                name: None,
                basis: String::new(),
                recipe: None,
                fields: BTreeMap::new(),
                warnings: Vec::new(),
                tried: None,
            },
        })
        .collect()
}

fn name_one(
    context: &Context,
    owner_app_id_count: usize,
    recipes: &RecipeSet,
    bus_address: Option<&OsStr>,
    command_environment: &Environment,
    command_cache: &mut CommandCache,
    mpris_cache: &mut Option<(Vec<Player>, u64)>,
    explain: bool,
) -> Output {
    let mut tried = Vec::new();
    for recipe in &recipes.recipes {
        let captures = match recipe.matches(
            &context.window.app_id,
            &context.window.title,
            &context.categories,
            context.sole_owner,
            &context.tree.executable_names,
        ) {
            MatchOutcome::NoMatch(reason) => {
                tried.push((recipe.id.clone(), reason));
                continue;
            }
            MatchOutcome::Match(captures) => captures,
        };

        match evaluate(
            recipe,
            context,
            &captures,
            owner_app_id_count,
            bus_address,
            command_environment,
            command_cache,
            mpris_cache,
        ) {
            Ok((name, basis, fields)) => {
                tried.push((recipe.id.clone(), "selected".to_string()));
                return Output {
                    window: context.window.id,
                    missing: false,
                    name: Some(name),
                    basis,
                    recipe: Some(recipe.id.clone()),
                    fields,
                    warnings: recipes.warnings.clone(),
                    tried: explain.then_some(tried),
                };
            }
            Err(reason) => tried.push((recipe.id.clone(), reason)),
        }
    }

    let title = normalize_name(&context.window.title);
    let app_name = normalize_name(&context.app_name);
    let app_id = normalize_name(&context.window.app_id);
    let (name, basis) = if !title.is_empty() {
        (Some(title), "title")
    } else if !app_name.is_empty() {
        (Some(app_name), "app")
    } else if !app_id.is_empty() {
        (Some(app_id), "app")
    } else {
        (None, "none")
    };
    Output {
        window: context.window.id,
        missing: false,
        name,
        basis: basis.to_string(),
        recipe: None,
        fields: BTreeMap::new(),
        warnings: recipes.warnings.clone(),
        tried: explain.then_some(tried),
    }
}

#[allow(clippy::too_many_arguments)]
fn evaluate(
    recipe: &Recipe,
    context: &Context,
    captures: &BTreeMap<String, String>,
    owner_app_id_count: usize,
    bus_address: Option<&OsStr>,
    command_environment: &Environment,
    command_cache: &mut CommandCache,
    mpris_cache: &mut Option<(Vec<Player>, u64)>,
) -> Result<(String, String, BTreeMap<String, String>), String> {
    let window_fields = context.window_fields();
    let mut values = window_fields.clone();
    values.extend(captures.clone());
    let mut source_basis = None;

    match &recipe.source {
        None => {}
        Some(Source::Mpris { timeout_ms }) => {
            if !matches!(mpris_cache.as_ref(), Some((_, used)) if used >= timeout_ms) {
                let players = mpris::read(bus_address, *timeout_ms)?;
                *mpris_cache = Some((players, *timeout_ms));
            }
            let players = &mpris_cache
                .as_ref()
                .expect("MPRIS cache was just populated")
                .0;
            let fields = associated_mpris_fields(context, owner_app_id_count, players)?;
            source_basis = Some("current".to_string());
            values.extend(fields);
        }
        Some(Source::Command {
            argv,
            output,
            fields,
            basis,
            timeout_ms,
        }) => {
            let argv = argv
                .iter()
                .map(|argument| substitute(argument, &window_fields))
                .collect::<Result<Vec<_>, _>>()?;
            let result = command_cache.run(
                &argv,
                &context.command_input(),
                *timeout_ms,
                command_environment,
            )?;
            let (fields, basis) = decode_command_result(
                &result,
                *output,
                fields.as_ref(),
                basis.as_deref(),
                &recipe.title_capture_names,
            )?;
            source_basis = Some(basis);
            values.extend(fields);
        }
    }

    let mut used_fields = BTreeMap::new();
    for field in &recipe.name.fields {
        let Some(value) = values.get(field) else {
            return Err(format!("template field {field:?} is unavailable"));
        };
        if value.is_empty() {
            return Err(format!("template field {field:?} is empty"));
        }
        used_fields.insert(field.clone(), value.clone());
    }

    let mut rendered = String::new();
    for token in &recipe.name.tokens {
        match token {
            Token::Text(text) => rendered.push_str(text),
            Token::Field(field) => {
                let value = values
                    .get(field)
                    .ok_or_else(|| format!("template field {field:?} is unavailable"))?;
                rendered.push_str(value);
            }
        }
    }
    let rendered = normalize_name(&rendered);
    if rendered.is_empty() {
        return Err("rendered name is empty".to_string());
    }

    let basis = match source_basis {
        Some(basis) => basis,
        None if recipe.name.fields.iter().any(|field| {
            field == "title"
                || field == "title_content"
                || recipe.title_capture_names.contains(field)
        }) =>
        {
            "title".to_string()
        }
        None => "app".to_string(),
    };
    Ok((rendered, basis, used_fields))
}

fn associated_mpris_fields(
    context: &Context,
    owner_app_id_count: usize,
    players: &[Player],
) -> Result<BTreeMap<String, String>, String> {
    let desktop_entry_matches = owner_app_id_count == 1;
    let associated: Vec<_> = players
        .iter()
        .filter(|player| {
            let process_match = context.sole_owner && context.tree.pids.contains(&player.owner_pid);
            let app_id_match = desktop_entry_matches
                && desktop_entry_matches_app_id(&player.desktop_entry, &context.window.app_id);
            process_match || app_id_match
        })
        .collect();
    let selected = match associated.as_slice() {
        [player] => *player,
        [] => return Err("no MPRIS player is associated with this window".to_string()),
        many => {
            let playing: Vec<_> = many
                .iter()
                .copied()
                .filter(|player| player.status.as_deref() == Some("Playing"))
                .collect();
            if let [player] = playing.as_slice() {
                *player
            } else {
                return Err(
                    "multiple associated MPRIS players without one unique Playing player"
                        .to_string(),
                );
            }
        }
    };
    if !selected.fields.contains_key("track") {
        return Ok(BTreeMap::new());
    }
    Ok(selected.fields.clone())
}

fn desktop_entry_matches_app_id(desktop_entry: &str, app_id: &str) -> bool {
    !desktop_entry.is_empty() && !app_id.is_empty() && desktop_entry.eq_ignore_ascii_case(app_id)
}

fn decode_command_result(
    result: &CommandResult,
    output: CommandOutput,
    mapping: Option<&BTreeMap<String, String>>,
    basis_pointer: Option<&str>,
    title_captures: &BTreeSet<String>,
) -> Result<(BTreeMap<String, String>, String), String> {
    let stdout = std::str::from_utf8(&result.stdout)
        .map_err(|_| "command output is not UTF-8".to_string())?;
    if output == CommandOutput::Text {
        let first = stdout.lines().next().unwrap_or_default();
        let mut fields = BTreeMap::new();
        if !first.is_empty() {
            fields.insert("text".to_string(), first.to_string());
        }
        return Ok((fields, "current".to_string()));
    }

    let document: Value = serde_json::from_str(stdout)
        .map_err(|error| format!("command output is not valid JSON: {error}"))?;
    let mut fields = BTreeMap::new();
    if let Some(mapping) = mapping {
        for (field, pointer) in mapping {
            if let Some(value) = document.pointer(pointer).and_then(Value::as_str) {
                fields.insert(field.clone(), value.to_string());
            }
        }
    } else if let Some(object) = document.as_object() {
        for (field, value) in object {
            if is_window_field(field) || title_captures.contains(field) {
                continue;
            }
            if let Some(value) = value.as_str() {
                fields.insert(field.clone(), value.to_string());
            }
        }
    }
    let basis = match basis_pointer {
        None => "current".to_string(),
        Some(pointer) => {
            let value = document
                .pointer(pointer)
                .and_then(Value::as_str)
                .ok_or_else(|| "command output has no string at source.basis".to_string())?;
            match value {
                "current" | "launch" | "title" | "app" | "none" => value.to_string(),
                _ => return Err(format!("command output has unknown basis {value:?}")),
            }
        }
    };
    Ok((fields, basis))
}

fn is_window_field(field: &str) -> bool {
    matches!(
        field,
        "window" | "pid" | "app_id" | "title" | "app_name" | "title_content"
    )
}

fn substitute(argument: &str, fields: &BTreeMap<String, String>) -> Result<String, String> {
    let template = crate::recipe::parse_template_for_command(argument)?;
    let mut rendered = String::new();
    for token in template {
        match token {
            Token::Text(text) => rendered.push_str(&text),
            Token::Field(field) => {
                rendered.push_str(
                    fields.get(&field).ok_or_else(|| {
                        format!("command argument field {field:?} is unavailable")
                    })?,
                );
            }
        }
    }
    Ok(rendered)
}

pub fn title_content(title: &str, app_id: &str, desktop_name: &Option<String>) -> String {
    let mut last = None;
    for separator in [" - ", " – ", " — ", " | "] {
        if let Some(index) = title.rfind(separator) {
            if last.is_none_or(|(previous, _)| index > previous) {
                last = Some((index, separator.len()));
            }
        }
    }
    let Some((index, length)) = last else {
        return String::new();
    };
    let suffix = &title[index + length..];
    let short_app_id = app_id.rsplit('.').next().unwrap_or_default();
    let suffix_lower = suffix.to_lowercase();
    let name_matches = desktop_name
        .as_deref()
        .is_some_and(|name| !name.is_empty() && suffix_lower.contains(&name.to_lowercase()));
    let app_id_matches =
        !short_app_id.is_empty() && suffix_lower.contains(&short_app_id.to_lowercase());
    if name_matches || app_id_matches {
        title[..index].to_string()
    } else {
        String::new()
    }
}

pub fn normalize_name(value: &str) -> String {
    let normalized: String = value
        .chars()
        .map(|character| {
            if character.is_control() {
                ' '
            } else {
                character
            }
        })
        .collect();
    normalized.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub fn json_value(output: &Output, explain: bool) -> Value {
    if output.missing {
        return json!({ "window": output.window, "error": "no-such-window" });
    }
    let warnings = output
        .warnings
        .iter()
        .map(|warning| json!({ "recipe": warning.recipe, "reason": warning.reason }))
        .collect::<Vec<_>>();
    let mut value = json!({
        "window": output.window,
        "name": output.name,
        "basis": output.basis,
        "recipe": output.recipe,
        "fields": output.fields,
        "warnings": warnings
    });
    if explain {
        value["tried"] = json!(
            output
                .tried
                .iter()
                .flatten()
                .map(|(recipe, reason)| json!({"recipe": recipe, "reason": reason}))
                .collect::<Vec<_>>()
        );
    }
    value
}

#[cfg(test)]
mod tests {
    use super::{
        Context, associated_mpris_fields, desktop_entry_matches_app_id, name, normalize_name,
        title_content,
    };
    use crate::desktop::Index;
    use crate::ipc::Window;
    use crate::mpris::Player;
    use crate::process::{Snapshot, Tree};
    use crate::recipe::Set;
    use scottland::session::Environment;
    use std::collections::{BTreeMap, BTreeSet};

    #[test]
    fn title_content_removes_only_a_matching_final_segment() {
        assert_eq!(
            title_content(
                "Pull request 12 - repo — Example Browser",
                "org.example.browser",
                &Some("Browser".to_string())
            ),
            "Pull request 12 - repo"
        );
        assert_eq!(
            title_content(
                "Downloads - unrelated",
                "org.example.files",
                &Some("Files".into())
            ),
            ""
        );
    }

    #[test]
    fn output_names_are_single_line_and_untruncated() {
        let long = "x".repeat(20_000);
        assert_eq!(
            normalize_name(&format!(" \talpha\n {long}\r")),
            format!("alpha {long}")
        );
    }

    #[test]
    fn desktop_entry_matches_the_exact_app_id_ignoring_case() {
        assert!(desktop_entry_matches_app_id(
            "Org.Example.Reader",
            "org.example.reader"
        ));
        assert!(!desktop_entry_matches_app_id(
            "reader",
            "org.example.reader"
        ));
        assert!(!desktop_entry_matches_app_id(
            "org.example.reader.desktop",
            "org.example.reader"
        ));
        assert!(!desktop_entry_matches_app_id("browser", "org.example.mail"));
        assert!(!desktop_entry_matches_app_id("", "org.example.reader"));
    }

    #[test]
    fn floor_uses_title_then_app_id_and_reports_missing_windows() {
        let windows = vec![
            Window {
                id: 7,
                pid: 40,
                app_id: "org.example.writer".to_string(),
                title: "  Draft\n  one  ".to_string(),
            },
            Window {
                id: 8,
                pid: 41,
                app_id: "org.example.reader".to_string(),
                title: String::new(),
            },
        ];
        let no_environment = |_: &str| -> Option<std::ffi::OsString> { None };
        let desktop_index = Index::read_from(&no_environment);
        let process_snapshot = Snapshot::default();
        let recipes = Set::default();
        let output = name(
            &[7, 8, 99],
            &windows,
            &desktop_index,
            &process_snapshot,
            &recipes,
            None,
            &Environment::default(),
            true,
        );
        assert_eq!(output[0].name.as_deref(), Some("Draft one"));
        assert_eq!(output[0].basis, "title");
        assert_eq!(output[1].name.as_deref(), Some("org.example.reader"));
        assert_eq!(output[1].basis, "app");
        assert!(output[2].missing);
    }

    #[test]
    fn mpris_uses_the_only_playing_associated_player() {
        let context = Context {
            window: Window {
                id: 7,
                pid: 40,
                app_id: "org.example.player".to_string(),
                title: "Track — Player".to_string(),
            },
            app_name: "Player".to_string(),
            categories: Vec::new(),
            title_content: "Track".to_string(),
            sole_owner: true,
            tree: Tree {
                pids: BTreeSet::from([40, 51, 52]),
                executable_names: BTreeSet::new(),
            },
        };
        let players = vec![
            Player {
                owner_pid: 51,
                desktop_entry: "other".to_string(),
                status: Some("Paused".to_string()),
                fields: BTreeMap::from([("track".to_string(), "Old".to_string())]),
            },
            Player {
                owner_pid: 52,
                desktop_entry: "other".to_string(),
                status: Some("Playing".to_string()),
                fields: BTreeMap::from([("track".to_string(), "Current".to_string())]),
            },
        ];
        let fields = associated_mpris_fields(&context, 1, &players).unwrap();
        assert_eq!(fields.get("track").map(String::as_str), Some("Current"));
    }

    #[test]
    fn mpris_exposes_no_fields_when_the_player_has_no_track_title() {
        let context = Context {
            window: Window {
                id: 7,
                pid: 40,
                app_id: "org.example.player".to_string(),
                title: "Player".to_string(),
            },
            app_name: "Player".to_string(),
            categories: Vec::new(),
            title_content: String::new(),
            sole_owner: true,
            tree: Tree {
                pids: BTreeSet::from([40]),
                executable_names: BTreeSet::new(),
            },
        };
        let players = vec![Player {
            owner_pid: 40,
            desktop_entry: "org.example.player".to_string(),
            status: Some("Playing".to_string()),
            fields: BTreeMap::from([
                ("artist".to_string(), "Artist".to_string()),
                ("status".to_string(), "Playing".to_string()),
            ]),
        }];
        assert!(
            associated_mpris_fields(&context, 1, &players)
                .unwrap()
                .is_empty()
        );
    }
}
