//! Shared display theme and color resolution for the CLI.

use std::io::IsTerminal;
use std::sync::OnceLock;

use clap::ValueEnum;

pub const RESET: &str = "\x1b[0m";
pub const SUCCESS: &str = "\x1b[1;32m"; // bold green
pub const WARNING: &str = "\x1b[1;33m"; // bold yellow
pub const ERROR: &str = "\x1b[1;31m"; // bold red
pub const INFO: &str = "\x1b[36m"; // cyan
pub const DIM: &str = "\x1b[2m";
pub const LABEL: &str = "\x1b[1m"; // bold
pub const NUMBER: &str = "\x1b[1;34m"; // bold blue

/// The `--color` flag's tri-state.
#[derive(Clone, Copy, Debug, PartialEq, Eq, ValueEnum)]
pub enum ColorMode {
    Auto,
    Always,
    Never,
}

impl ColorMode {
    /// Parse a `--color` value ("auto" | "always" | "never", case-insensitive).
    pub fn parse_value(value: &str) -> Option<Self> {
        Self::value_variants().iter().copied().find(|mode| {
            mode.to_possible_value()
                .is_some_and(|pv| pv.matches(value, true))
        })
    }
}

static COLOR: OnceLock<bool> = OnceLock::new();

/// Pin the invocation's color decision; the first call wins.
pub fn init_color_mode(color: bool) {
    let _ = COLOR.set(color);
}

/// Resolve the color decision for one invocation (flag, then env, then tty).
pub fn resolve_color_mode(mode: ColorMode, tty: bool) -> bool {
    match mode {
        ColorMode::Always => true,
        ColorMode::Never => false,
        ColorMode::Auto => color_auto(
            env_value("NO_COLOR"),
            env_value("CLICOLOR_FORCE"),
            env_value("CLICOLOR"),
            tty,
        ),
    }
}

fn env_value(key: &str) -> Option<String> {
    std::env::var_os(key).map(|v| v.to_string_lossy().into_owned())
}

/// Precedence: NO_COLOR (non-empty) > CLICOLOR_FORCE (non-empty, != 0) > CLICOLOR=0 > tty.
fn color_auto(
    no_color: Option<String>,
    force: Option<String>,
    clicolor: Option<String>,
    tty: bool,
) -> bool {
    let set_nonempty = |v: &Option<String>| v.as_deref().is_some_and(|v| !v.is_empty());
    if set_nonempty(&no_color) {
        return false;
    }
    if force.as_deref().is_some_and(|v| !v.is_empty() && v != "0") {
        return true;
    }
    if clicolor.as_deref() == Some("0") {
        return false;
    }
    tty
}

/// True iff color is on for stdout under the resolved color mode.
pub fn color_enabled() -> bool {
    *COLOR.get_or_init(|| resolve_color_mode(ColorMode::Auto, std::io::stdout().is_terminal()))
}

/// Wrap `text` in `style` escapes; plain text when color is off.
pub fn paint(style: &str, text: &str) -> String {
    paint_with(color_enabled(), style, text)
}

/// Wrap `text` in `style` escapes; plain text when color is off.
pub(crate) fn paint_with(color: bool, style: &str, text: &str) -> String {
    if color {
        format!("{style}{text}{RESET}")
    } else {
        text.to_string()
    }
}

/// Console width text wraps at (non-TTY default).
pub const CONSOLE_WIDTH: usize = 80;

/// Print through the rich-console fold: greedy word wrap at the console width,
/// trailing whitespace retained at each break (rich's non-TTY shape).
pub fn console_print(line: &str) {
    if line.chars().count() <= CONSOLE_WIDTH {
        println!("{line}");
        return;
    }
    let mut current = String::new();
    let mut chunk = String::new();
    for ch in line.chars() {
        chunk.push(ch);
        if ch == ' ' {
            flush_chunk(&mut current, &chunk);
            chunk.clear();
        }
    }
    if !chunk.is_empty() {
        flush_chunk(&mut current, &chunk);
    }
    if !current.is_empty() {
        println!("{current}");
    }
}

/// Greedy word placement; a word longer than the width hard-breaks at the
/// boundary (rich's fold behavior).
fn flush_chunk(current: &mut String, chunk: &str) {
    if current.chars().count() + chunk.chars().count() > CONSOLE_WIDTH && !current.is_empty() {
        println!("{current}");
        current.clear();
    }
    if current.chars().count() + chunk.chars().count() <= CONSOLE_WIDTH {
        current.push_str(chunk);
        return;
    }
    // The chunk alone exceeds the width: split it at the boundary.
    let mut rest = chunk;
    while rest.chars().count() > CONSOLE_WIDTH - current.chars().count() {
        let take = CONSOLE_WIDTH - current.chars().count();
        let head: String = rest.chars().take(take).collect();
        println!("{current}{head}");
        current.clear();
        rest = &rest[head.len()..];
    }
    current.push_str(rest);
}

pub fn success(msg: &str) {
    console_print(&format!("{} {msg}", paint(SUCCESS, "✓")));
}

pub fn warning(msg: &str) {
    console_print(&format!("{} {msg}", paint(WARNING, "!")));
}

pub fn error(msg: &str) {
    console_print(&format!("{} {msg}", paint(ERROR, "✗")));
}

pub fn info(msg: &str) {
    console_print(&format!("{} {msg}", paint(INFO, "⠿")));
}

pub fn dim(msg: &str) {
    console_print(&paint(DIM, msg));
}

/// Print a styled `label  value` line with the label padded to 14 columns.
pub fn kv(label: &str, value: &str) {
    console_print(&format!(
        "{} {}",
        paint(LABEL, &format!("{label:14}")),
        paint(NUMBER, value)
    ));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn theme_tokens_match_the_display_theme() {
        assert_eq!(SUCCESS, "\x1b[1;32m");
        assert_eq!(WARNING, "\x1b[1;33m");
        assert_eq!(ERROR, "\x1b[1;31m");
        assert_eq!(INFO, "\x1b[36m");
        assert_eq!(DIM, "\x1b[2m");
        assert_eq!(LABEL, "\x1b[1m");
        assert_eq!(NUMBER, "\x1b[1;34m");
    }

    #[test]
    fn paint_toggles_with_color() {
        assert_eq!(paint_with(false, INFO, "x"), "x");
        assert_eq!(paint_with(true, INFO, "x"), format!("{INFO}x{RESET}"));
    }

    #[test]
    fn kv_pads_the_raw_label_before_styling() {
        let line = format!(
            "{} {}",
            paint_with(false, LABEL, &format!("{:14}", "db")),
            paint_with(false, NUMBER, "store.kg")
        );
        assert_eq!(line, format!("{:14} store.kg", "db"));
    }

    #[test]
    fn color_auto_precedence_matches_the_contract() {
        let cases = [
            ((None, None, None, true), true),
            ((None, None, None, false), false),
            ((Some("1".into()), None, None, true), false),
            ((Some("1".into()), None, None, false), false),
            ((Some(String::new()), None, None, true), true),
            ((None, Some("1".into()), None, false), true),
            ((None, Some("0".into()), None, false), false),
            ((None, None, Some("0".into()), true), false),
            ((None, None, Some("1".into()), true), true),
            ((None, None, Some("1".into()), false), false),
            ((Some("1".into()), Some("1".into()), None, false), false),
            ((None, Some("1".into()), Some("0".into()), false), true),
        ];
        for ((no_color, force, clicolor, tty), expected) in cases {
            assert_eq!(
                color_auto(no_color, force, clicolor, tty),
                expected,
                "precedence case failed"
            );
        }
    }

    #[test]
    fn mode_overrides_the_environment_and_tty() {
        assert!(resolve_color_mode(ColorMode::Always, false));
        assert!(resolve_color_mode(ColorMode::Always, true));
        assert!(!resolve_color_mode(ColorMode::Never, true));
        assert!(!resolve_color_mode(ColorMode::Never, false));
    }

    #[test]
    fn parse_value_accepts_the_three_modes_case_insensitively() {
        assert_eq!(ColorMode::parse_value("auto"), Some(ColorMode::Auto));
        assert_eq!(ColorMode::parse_value("ALWAYS"), Some(ColorMode::Always));
        assert_eq!(ColorMode::parse_value("Never"), Some(ColorMode::Never));
        assert_eq!(ColorMode::parse_value("bogus"), None);
        assert_eq!(ColorMode::parse_value(""), None);
    }
}
