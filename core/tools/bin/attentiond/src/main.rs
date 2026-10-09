fn main() {
    if let Err(error) = attentiond::run() {
        eprintln!("attentiond: {error}");
        std::process::exit(1);
    }
}
