//! Shared display theme for the CLI; color tokens mirror the Python `cli.display` THEME.

use std::io::IsTerminal;

pub const RESET: &str = "\x1b[0m";
pub const SUCCESS: &str = "\x1b[1;32m"; // bold green
pub const WARNING: &str = "\x1b[1;33m"; // bold yellow
pub const ERROR: &str = "\x1b[1;31m"; // bold red
pub const INFO: &str = "\x1b[36m"; // cyan
pub const DIM: &str = "\x1b[2m";
pub const LABEL: &str = "\x1b[1m"; // bold
pub const NUMBER: &str = "\x1b[1;34m"; // bold blue

/// True iff stdout is a terminal and color is not disabled.
pub fn color_enabled() -> bool {
    std::env::var_os("NO_COLOR").is_none() && std::io::stdout().is_terminal()
}

/// Wrap `text` in `style` escapes; plain text when color is off.
pub fn paint(style: &str, text: &str) -> String {
    paint_with(color_enabled(), style, text)
}

fn paint_with(color: bool, style: &str, text: &str) -> String {
    if color {
        format!("{style}{text}{RESET}")
    } else {
        text.to_string()
    }
}

pub fn success(msg: &str) {
    println!("{} {msg}", paint(SUCCESS, "✓"));
}

pub fn warning(msg: &str) {
    println!("{} {msg}", paint(WARNING, "!"));
}

pub fn error(msg: &str) {
    println!("{} {msg}", paint(ERROR, "✗"));
}

pub fn info(msg: &str) {
    println!("{} {msg}", paint(INFO, "⠿"));
}

pub fn dim(msg: &str) {
    println!("{}", paint(DIM, msg));
}

/// Print a styled `label  value` line with the label padded to 14 columns.
pub fn kv(label: &str, value: &str) {
    println!(
        "{} {}",
        paint(LABEL, &format!("{label:14}")),
        paint(NUMBER, value)
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn theme_tokens_match_the_python_display_theme() {
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
}
