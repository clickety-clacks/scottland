//! Shared code for Scottland's system tools.
//!
//! Every `scottland-*` binary in this workspace builds on this crate rather than carrying its own
//! copy of the same rules. It holds what the shell and Python helpers already agree on, so a Rust
//! tool and a script look in the same places:
//!
//! - [`dirs`]: where Scottland keeps its runtime, state and config files, and which hooks
//!   directory (dev snapshot or package) a session uses.
//! - [`session`]: the environments running sessions record at startup, so a tool acts on a
//!   session with that session's own environment, never the caller's.
//!
//! Scottland core runs on any distro: nothing here may assume one.

pub mod dirs;
pub mod session;
