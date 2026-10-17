use std::env;
use std::process::ExitCode;

fn main() -> ExitCode {
    scottland_source::run(
        "scottland",
        env!("PRODUCT_SOURCE_BUILD_COMMIT"),
        env::args_os().skip(1),
    )
}
