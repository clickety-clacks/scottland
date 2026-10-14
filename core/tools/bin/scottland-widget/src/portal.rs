use async_io::Timer;
use futures_lite::{StreamExt, future};
use std::collections::HashMap;
use std::os::unix::ffi::OsStringExt;
use std::path::PathBuf;
use std::time::Duration;
use zbus::zvariant::{OwnedObjectPath, OwnedValue, Str, Value};
use zbus::{Connection, Proxy};

const PORTAL: &str = "org.freedesktop.portal.Desktop";
const PORTAL_PATH: &str = "/org/freedesktop/portal/desktop";
const FILE_CHOOSER: &str = "org.freedesktop.portal.FileChooser";
const REQUEST: &str = "org.freedesktop.portal.Request";
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(600);

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FileChooserRequest {
    pub title: String,
    pub multiple: bool,
    pub directory: bool,
    pub extensions: Vec<String>,
}

impl FileChooserRequest {
    pub fn parse(args: &[String]) -> Result<Self, String> {
        let mut request = Self {
            title: "Select file".to_string(),
            multiple: false,
            directory: false,
            extensions: Vec::new(),
        };
        let mut index = 0;
        while index < args.len() {
            match args[index].as_str() {
                "--title" => {
                    index += 1;
                    let Some(title) = args.get(index).filter(|value| !value.is_empty()) else {
                        return Err("--title requires non-empty text".to_string());
                    };
                    request.title.clone_from(title);
                }
                "--multiple" => request.multiple = true,
                "--directory" => request.directory = true,
                "--extensions" => {
                    index += 1;
                    let Some(extensions) = args.get(index) else {
                        return Err("--extensions requires a list".to_string());
                    };
                    request.extensions = extensions
                        .split_ascii_whitespace()
                        .map(|extension| extension.trim_start_matches('.').to_ascii_lowercase())
                        .filter(|extension| !extension.is_empty())
                        .collect();
                }
                option => return Err(format!("unknown file-select option {option}")),
            }
            index += 1;
        }
        Ok(request)
    }

    fn portal_options(&self, token: &str) -> Result<HashMap<String, OwnedValue>, String> {
        let mut options = HashMap::new();
        options.insert(
            "handle_token".to_string(),
            OwnedValue::from(Str::from(token)),
        );
        options.insert("multiple".to_string(), OwnedValue::from(self.multiple));
        if self.directory {
            options.insert("directory".to_string(), OwnedValue::from(true));
        }
        if !self.extensions.is_empty() && !self.directory {
            let patterns = self
                .extensions
                .iter()
                .flat_map(|extension| {
                    [
                        extension.to_ascii_lowercase(),
                        extension.to_ascii_uppercase(),
                    ]
                    .into_iter()
                    .map(|extension| (0_u32, format!("*.{extension}")))
                })
                .collect::<Vec<_>>();
            let label = self
                .extensions
                .iter()
                .map(|extension| format!("*.{extension}"))
                .collect::<Vec<_>>()
                .join(" ");
            let filter = (label, patterns);
            let filters = vec![filter.clone()];
            let filters = OwnedValue::try_from(Value::new(filters))
                .map_err(|error| format!("could not encode file type filters: {error}"))?;
            let current_filter = OwnedValue::try_from(Value::new(filter)).map_err(|error| {
                format!("could not encode the current file type filter: {error}")
            })?;
            options.insert("filters".to_string(), filters);
            options.insert("current_filter".to_string(), current_filter);
        }
        Ok(options)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum FileChooserResult {
    Cancelled,
    Selected(Vec<PathBuf>),
}

#[derive(Default)]
pub struct SystemFileChooser;

impl SystemFileChooser {
    pub fn choose(&mut self, request: &FileChooserRequest) -> Result<FileChooserResult, String> {
        futures_lite::future::block_on(choose(request))
    }
}

async fn choose(request: &FileChooserRequest) -> Result<FileChooserResult, String> {
    let connection = Connection::session()
        .await
        .map_err(|error| format!("could not connect to the session bus: {error}"))?;
    let unique_name = connection
        .unique_name()
        .ok_or_else(|| "session bus did not assign a unique name".to_string())?
        .to_string();
    let token = format!("scottland{}", std::process::id());
    let predicted_path = format!(
        "/org/freedesktop/portal/desktop/request/{}/{}",
        unique_name.trim_start_matches(':').replace('.', "_"),
        token
    );
    let predicted_proxy = Proxy::new(&connection, PORTAL, predicted_path.as_str(), REQUEST)
        .await
        .map_err(|error| {
            format!("could not prepare to receive the file chooser response: {error}")
        })?;
    let mut responses = predicted_proxy
        .receive_signal("Response")
        .await
        .map_err(|error| format!("could not subscribe to the file chooser response: {error}"))?;

    let options = request.portal_options(&token)?;
    let portal = Proxy::new(&connection, PORTAL, PORTAL_PATH, FILE_CHOOSER)
        .await
        .map_err(|error| format!("could not address the desktop file chooser: {error}"))?;
    let returned_path: OwnedObjectPath = portal
        .call("OpenFile", &("", request.title.as_str(), &options))
        .await
        .map_err(|error| format!("could not open the desktop file chooser: {error}"))?;

    if returned_path.as_str() != predicted_path {
        let response_proxy = Proxy::new(&connection, PORTAL, returned_path.as_str(), REQUEST)
            .await
            .map_err(|error| format!("could not address the file chooser response: {error}"))?;
        responses = response_proxy
            .receive_signal("Response")
            .await
            .map_err(|error| {
                format!("could not subscribe to the file chooser response: {error}")
            })?;
    }

    let response = future::race(
        async move {
            responses
                .next()
                .await
                .ok_or_else(|| "file chooser response stream closed".to_string())?
                .body()
                .deserialize::<(u32, HashMap<String, OwnedValue>)>()
                .map_err(|error| format!("could not read the file chooser response: {error}"))
        },
        async {
            Timer::after(RESPONSE_TIMEOUT).await;
            Err("file chooser did not answer within 600 seconds".to_string())
        },
    )
    .await;

    match response {
        Ok((0, results)) => {
            let uris = results
                .get("uris")
                .ok_or_else(|| "file chooser response did not contain selected files".to_string())?
                .try_clone()
                .and_then(Vec::<String>::try_from)
                .map_err(|error| format!("could not read selected file URIs: {error}"))?;
            uris.into_iter()
                .map(|uri| file_uri_to_path(&uri))
                .collect::<Result<Vec<_>, _>>()
                .map(FileChooserResult::Selected)
        }
        Ok((1, _)) => Ok(FileChooserResult::Cancelled),
        Ok((code, _)) => Err(format!("file chooser failed with response code {code}")),
        Err(error) => Err(error),
    }
}

fn file_uri_to_path(uri: &str) -> Result<PathBuf, String> {
    let rest = uri
        .strip_prefix("file://")
        .ok_or_else(|| "file chooser returned a non-file URI".to_string())?;
    let path = if let Some(local_path) = rest.strip_prefix("localhost/") {
        format!("/{local_path}")
    } else if rest.starts_with('/') {
        rest.to_string()
    } else {
        return Err("file chooser returned a non-local file URI".to_string());
    };
    let bytes = percent_decode(path.as_bytes())?;
    if bytes.first() != Some(&b'/') {
        return Err("file chooser returned a path that is not absolute".to_string());
    }
    Ok(PathBuf::from(std::ffi::OsString::from_vec(bytes)))
}

fn percent_decode(input: &[u8]) -> Result<Vec<u8>, String> {
    let mut output = Vec::with_capacity(input.len());
    let mut index = 0;
    while index < input.len() {
        if input[index] != b'%' {
            output.push(input[index]);
            index += 1;
            continue;
        }
        let Some((&high, &low)) = input.get(index + 1).zip(input.get(index + 2)) else {
            return Err("file chooser returned an invalid escaped path".to_string());
        };
        output.push((hex(high)? << 4) | hex(low)?);
        index += 3;
    }
    Ok(output)
}

fn hex(value: u8) -> Result<u8, String> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        b'A'..=b'F' => Ok(value - b'A' + 10),
        _ => Err("file chooser returned an invalid escaped path".to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::ffi::OsStrExt;

    #[test]
    fn parses_file_select_options_and_builds_portal_filters() {
        let request = FileChooserRequest::parse(&[
            "--title".to_string(),
            "Choose images".to_string(),
            "--multiple".to_string(),
            "--extensions".to_string(),
            ".png jpg".to_string(),
        ])
        .unwrap();
        assert_eq!(request.title, "Choose images");
        assert!(request.multiple);
        let options = request.portal_options("request1").unwrap();
        assert_eq!(
            bool::try_from(options["multiple"].try_clone().unwrap()).unwrap(),
            true
        );
        assert!(options.contains_key("filters"));
        assert!(options.contains_key("current_filter"));
    }

    #[test]
    fn directory_selection_omits_file_type_filters() {
        let request = FileChooserRequest::parse(&[
            "--directory".to_string(),
            "--extensions".to_string(),
            "png".to_string(),
        ])
        .unwrap();
        let options = request.portal_options("request2").unwrap();
        assert_eq!(
            bool::try_from(options["directory"].try_clone().unwrap()).unwrap(),
            true
        );
        assert!(!options.contains_key("filters"));
    }

    #[test]
    fn file_uri_decoding_preserves_spaces_and_non_utf8_path_bytes() {
        let path = file_uri_to_path("file:///tmp/a%20b%FF").unwrap();
        assert_eq!(path.as_os_str().as_bytes(), b"/tmp/a b\xff");
        assert_eq!(
            file_uri_to_path("file://localhost/tmp/a").unwrap(),
            PathBuf::from("/tmp/a")
        );
    }

    #[test]
    fn rejects_remote_and_malformed_file_uris() {
        assert!(file_uri_to_path("https://example.invalid/file").is_err());
        assert!(file_uri_to_path("file://server/share/file").is_err());
        assert!(file_uri_to_path("file:///tmp/%Q0").is_err());
    }

    #[test]
    fn rejects_unknown_and_missing_file_select_options() {
        assert!(FileChooserRequest::parse(&["--unknown".to_string()]).is_err());
        assert!(FileChooserRequest::parse(&["--title".to_string()]).is_err());
        assert!(FileChooserRequest::parse(&["--extensions".to_string()]).is_err());
    }
}
