//! Permissions (design §8): reads run, edits and commands ask, plan mode only proposes.
//!
//! | mode         | read | edit in workspace | command |
//! | default      | run  | ask               | ask     |
//! | accept-edits | run  | run               | ask     |
//! | plan         | run  | refuse            | refuse  |
//! | bypass       | run  | run               | run     |
//!
//! Paths outside the workspace are refused by the tools themselves in every mode.

use std::collections::HashSet;

use crate::config::Permission;
use crate::tools::{Effect, effect};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    Allow,
    Ask,
    Deny(String),
}

#[derive(Debug, Clone)]
pub struct Policy {
    pub mode: Permission,
    /// Tools the user answered "a" (always) for in this session.
    pub always: HashSet<String>,
}

impl Policy {
    pub fn new(mode: Permission) -> Policy {
        Policy { mode, always: HashSet::new() }
    }

    pub fn check(&self, tool: &str) -> Verdict {
        let fx = effect(tool);
        match fx {
            Effect::Read | Effect::Session | Effect::Remote => Verdict::Allow,
            Effect::Edit | Effect::Command => {
                if self.mode == Permission::Plan {
                    return Verdict::Deny(
                        "計画モードです。変更やコマンドは実行せず、最終文にパッチ案と実行するつもりのコマンドを書いてください".into(),
                    );
                }
                if self.mode == Permission::Bypass || self.always.contains(tool) {
                    return Verdict::Allow;
                }
                if fx == Effect::Edit && self.mode == Permission::AcceptEdits {
                    return Verdict::Allow;
                }
                Verdict::Ask
            }
        }
    }

    pub fn remember(&mut self, tool: &str) {
        self.always.insert(tool.to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table() {
        let d = Policy::new(Permission::Default);
        assert_eq!(d.check("read_file"), Verdict::Allow);
        assert_eq!(d.check("grep"), Verdict::Allow);
        assert_eq!(d.check("edit_file"), Verdict::Ask);
        assert_eq!(d.check("bash"), Verdict::Ask);
        let a = Policy::new(Permission::AcceptEdits);
        assert_eq!(a.check("write_file"), Verdict::Allow);
        assert_eq!(a.check("bash"), Verdict::Ask);
        let p = Policy::new(Permission::Plan);
        assert!(matches!(p.check("edit_file"), Verdict::Deny(_)));
        assert!(matches!(p.check("bash"), Verdict::Deny(_)));
        assert_eq!(p.check("read_file"), Verdict::Allow);
        let b = Policy::new(Permission::Bypass);
        assert_eq!(b.check("bash"), Verdict::Allow);
    }

    #[test]
    fn always_is_per_tool() {
        let mut d = Policy::new(Permission::Default);
        d.remember("bash");
        assert_eq!(d.check("bash"), Verdict::Allow);
        assert_eq!(d.check("edit_file"), Verdict::Ask);
    }
}
