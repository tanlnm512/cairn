//! Shared display theme for the CLI color tokens.

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
}
