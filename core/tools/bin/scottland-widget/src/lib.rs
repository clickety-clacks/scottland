mod portal;
mod session_osd;

use std::ffi::OsString;

pub use portal::{FileChooserRequest, FileChooserResult, SystemFileChooser};
pub use session_osd::{OsdRequest, SystemOsdPresenter};

const USAGE: &str = "usage: scottland-widget <file-select [--title TITLE] [--multiple] [--directory] [--extensions \"EXT EXT...\"]|osd --message TEXT [--icon NAME] [--progress N] [--duration MS]>";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CommandOutput {
    pub status: u8,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

impl CommandOutput {
    fn success(stdout: Vec<u8>) -> Self {
        Self {
            status: 0,
            stdout,
            stderr: Vec::new(),
        }
    }

    fn failure(status: u8, message: impl AsRef<str>) -> Self {
        let mut stderr = format!("scottland-widget: {}\n", message.as_ref()).into_bytes();
        if status == 2 && message.as_ref() == USAGE {
            stderr.clear();
            stderr.extend_from_slice(format!("{USAGE}\n").as_bytes());
        }
        Self {
            status,
            stdout: Vec::new(),
            stderr,
        }
    }
}

pub trait FileChooser {
    fn choose(&mut self, request: &FileChooserRequest) -> Result<FileChooserResult, String>;
}

pub trait OsdPresenter {
    fn present(&mut self, request: &OsdRequest) -> Result<(), String>;
}

#[derive(Default)]
pub struct SystemBackend {
    pub file_chooser: SystemFileChooser,
    pub osd_presenter: SystemOsdPresenter,
}

impl FileChooser for SystemBackend {
    fn choose(&mut self, request: &FileChooserRequest) -> Result<FileChooserResult, String> {
        self.file_chooser.choose(request)
    }
}

impl OsdPresenter for SystemBackend {
    fn present(&mut self, request: &OsdRequest) -> Result<(), String> {
        self.osd_presenter.present(request)
    }
}

pub fn run(
    args: impl IntoIterator<Item = OsString>,
    backend: &mut (impl FileChooser + OsdPresenter),
) -> CommandOutput {
    let args = match args
        .into_iter()
        .map(|arg| {
            arg.into_string()
                .map_err(|_| "arguments must be valid UTF-8".to_string())
        })
        .collect::<Result<Vec<_>, _>>()
    {
        Ok(args) => args,
        Err(error) => return CommandOutput::failure(2, error),
    };

    match args.first().map(String::as_str) {
        Some("file-select") => run_file_select(&args[1..], backend),
        Some("osd") => run_osd(&args[1..], backend),
        Some("__focused-output") if args.len() == 1 => match session_osd::focused_output_name() {
            Ok(name) => CommandOutput::success(format!("{name}\n").into_bytes()),
            Err(error) => CommandOutput::failure(1, error),
        },
        Some("--help" | "-h" | "help") if args.len() == 1 => {
            CommandOutput::success(format!("{USAGE}\n").into_bytes())
        }
        Some("--version" | "-V") if args.len() == 1 => CommandOutput::success(
            format!("scottland-widget {}\n", env!("CARGO_PKG_VERSION")).into_bytes(),
        ),
        _ => CommandOutput::failure(2, USAGE),
    }
}

fn run_file_select(
    args: &[String],
    backend: &mut (impl FileChooser + OsdPresenter),
) -> CommandOutput {
    let request = match FileChooserRequest::parse(args) {
        Ok(request) => request,
        Err(error) => return CommandOutput::failure(2, error),
    };
    match backend.choose(&request) {
        Ok(FileChooserResult::Cancelled) => CommandOutput {
            status: 1,
            stdout: Vec::new(),
            stderr: Vec::new(),
        },
        Ok(FileChooserResult::Selected(paths)) if paths.is_empty() => CommandOutput {
            status: 1,
            stdout: Vec::new(),
            stderr: Vec::new(),
        },
        Ok(FileChooserResult::Selected(paths)) => {
            let mut stdout = Vec::new();
            for path in paths {
                use std::os::unix::ffi::OsStrExt;
                stdout.extend_from_slice(path.as_os_str().as_bytes());
                stdout.push(b'\n');
            }
            CommandOutput::success(stdout)
        }
        Err(error) => CommandOutput::failure(2, error),
    }
}

fn run_osd(args: &[String], backend: &mut (impl FileChooser + OsdPresenter)) -> CommandOutput {
    let request = match OsdRequest::parse(args) {
        Ok(request) => request,
        Err(error) => return CommandOutput::failure(2, error),
    };
    match backend.present(&request) {
        Ok(()) => CommandOutput::success(Vec::new()),
        Err(error) => CommandOutput::failure(1, error),
    }
}

#[cfg(test)]
struct TestBackend {
    chosen: Option<Result<FileChooserResult, String>>,
    shown: Result<(), String>,
    file_requests: Vec<FileChooserRequest>,
    osd_requests: Vec<OsdRequest>,
}

#[cfg(test)]
impl Default for TestBackend {
    fn default() -> Self {
        Self {
            chosen: None,
            shown: Ok(()),
            file_requests: Vec::new(),
            osd_requests: Vec::new(),
        }
    }
}

#[cfg(test)]
impl FileChooser for TestBackend {
    fn choose(&mut self, request: &FileChooserRequest) -> Result<FileChooserResult, String> {
        self.file_requests.push(request.clone());
        self.chosen
            .take()
            .unwrap_or(Ok(FileChooserResult::Cancelled))
    }
}

#[cfg(test)]
impl OsdPresenter for TestBackend {
    fn present(&mut self, request: &OsdRequest) -> Result<(), String> {
        self.osd_requests.push(request.clone());
        self.shown.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn args(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    #[test]
    fn file_select_returns_selected_paths_and_requests_once() {
        let mut backend = TestBackend {
            chosen: Some(Ok(FileChooserResult::Selected(vec![
                PathBuf::from("/tmp/one file.txt"),
                PathBuf::from("/tmp/second.txt"),
            ]))),
            ..TestBackend::default()
        };

        let output = run(args(&["file-select", "--multiple"]), &mut backend);

        assert_eq!(output.status, 0);
        assert_eq!(output.stdout, b"/tmp/one file.txt\n/tmp/second.txt\n");
        assert!(output.stderr.is_empty());
        assert_eq!(backend.file_requests.len(), 1);
        assert!(backend.file_requests[0].multiple);
    }

    #[test]
    fn file_select_forwards_picker_configuration_to_the_portal() {
        let mut backend = TestBackend::default();
        let output = run(
            args(&[
                "file-select",
                "--title",
                "Choose an image",
                "--multiple",
                "--extensions",
                "png jpg",
            ]),
            &mut backend,
        );

        assert_eq!(output.status, 1);
        assert_eq!(backend.file_requests.len(), 1);
        assert_eq!(
            backend.file_requests[0],
            FileChooserRequest {
                title: "Choose an image".to_string(),
                multiple: true,
                directory: false,
                extensions: vec!["png".to_string(), "jpg".to_string()],
            }
        );
    }

    #[test]
    fn file_select_cancellation_is_distinct_from_portal_failure() {
        let mut cancelled = TestBackend::default();
        let output = run(args(&["file-select"]), &mut cancelled);
        assert_eq!(output.status, 1);
        assert!(output.stdout.is_empty());
        assert!(output.stderr.is_empty());

        let mut failed = TestBackend {
            chosen: Some(Err("portal unavailable".to_string())),
            ..TestBackend::default()
        };
        let output = run(args(&["file-select"]), &mut failed);
        assert_eq!(output.status, 2);
        assert!(output.stdout.is_empty());
        assert!(String::from_utf8_lossy(&output.stderr).contains("portal unavailable"));
    }

    #[test]
    fn osd_acknowledges_only_after_presenter_reports_shown() {
        let mut shown = TestBackend::default();
        let output = run(
            args(&[
                "osd",
                "--message",
                "Saving",
                "--icon",
                "document-save",
                "--progress",
                "0",
                "--duration",
                "900",
            ]),
            &mut shown,
        );
        assert_eq!(output.status, 0);
        assert!(output.stdout.is_empty());
        assert_eq!(shown.osd_requests.len(), 1);
        assert_eq!(
            shown.osd_requests[0],
            OsdRequest {
                message: "Saving".to_string(),
                icon: Some("document-save".to_string()),
                progress: Some(0),
                duration_ms: Some(900),
            }
        );

        let mut unavailable = TestBackend {
            shown: Err("session host did not acknowledge shown".to_string()),
            ..TestBackend::default()
        };
        let output = run(args(&["osd", "--message", "Saving"]), &mut unavailable);
        assert_eq!(output.status, 1);
        assert!(output.stdout.is_empty());
        assert!(String::from_utf8_lossy(&output.stderr).contains("shown"));
    }

    #[test]
    fn malformed_osd_arguments_do_not_call_the_host() {
        let mut backend = TestBackend::default();
        for input in [
            vec!["osd"],
            vec!["osd", "--message", ""],
            vec!["osd", "--message", "x", "--progress", "101"],
            vec!["osd", "--message", "x", "--duration", "0"],
            vec!["osd", "--message", "x", "--bogus"],
        ] {
            let output = run(args(&input), &mut backend);
            assert_eq!(output.status, 2, "{input:?}");
            assert!(output.stdout.is_empty(), "{input:?}");
        }
        assert!(backend.file_requests.is_empty());
        assert!(backend.osd_requests.is_empty());
    }
}
