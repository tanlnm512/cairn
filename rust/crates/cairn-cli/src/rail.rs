//! Terminal rendering: rail, summary panel, and tables (cli/display.py port).

use crate::display::{DIM, INFO, LABEL, SUCCESS, WARNING, paint};

pub const WIDTH: usize = 80;

const OPEN: &str = "┌";
const RAIL: &str = "│";
const CLOSE: &str = "└";
const STEP: &str = "◆";
const STAT: &str = "●";
const FAILED: &str = "✗";
const WARN: &str = "!";
const SEPARATOR: &str = "—";

/// Imperative vertical-rail renderer; `└` is written on every exit path.
pub struct Rail {
    lines: Vec<String>,
    active: Option<(String, String)>,
    prev_depth: Option<usize>,
    closed: bool,
    emit: bool,
}

impl Rail {
    /// Open the rail on stdout; the title line prints immediately.
    pub fn new(title: &str) -> Self {
        let mut rail = Self::buffered(title);
        rail.emit = true;
        for line in &rail.lines {
            crate::display::console_print(line);
        }
        rail
    }

    fn buffered(title: &str) -> Self {
        let mut rail = Self {
            lines: Vec::new(),
            active: None,
            prev_depth: None,
            closed: false,
            emit: false,
        };
        rail.push(format!("{OPEN} {title}"));
        rail
    }

    fn push(&mut self, text: String) {
        self.lines.push(text);
    }

    fn write(&mut self, text: String) {
        self.push(text);
        if self.emit
            && let Some(line) = self.lines.last()
        {
            crate::display::console_print(line);
        }
    }

    fn spacer_if_needed(&mut self, depth: usize) {
        if depth == 0 || self.prev_depth != Some(depth) {
            self.write(RAIL.to_string());
        }
    }

    fn settle_active(&mut self) {
        let Some((label, value)) = self.active.take() else {
            return;
        };
        let value = if value.is_empty() { "done" } else { &value };
        self.write(format!(
            "{}  {} {} {SEPARATOR} {}",
            paint(DIM, RAIL),
            paint(SUCCESS, STEP),
            label,
            value
        ));
        self.prev_depth = Some(1);
    }

    /// Depth-0 success line: `◆ text — value`.
    pub fn step(&mut self, text: &str, value: Option<&str>) {
        self.settle_active();
        self.spacer_if_needed(0);
        let mut line = format!("{} {}", paint(SUCCESS, STEP), text);
        if let Some(value) = value {
            line.push_str(&format!(" {} {}", paint(DIM, SEPARATOR), value));
        }
        self.write(line);
        self.prev_depth = Some(0);
    }

    /// Dim continuation line under the previous step.
    pub fn detail(&mut self, label: &str, value: &str) {
        self.write(format!("{}    {:12}{}", paint(DIM, RAIL), label, value));
    }

    /// Depth-0 warning line: `! text`.
    pub fn warn(&mut self, text: &str) {
        self.settle_active();
        self.spacer_if_needed(0);
        self.write(format!("{} {}", paint(WARNING, WARN), text));
        self.prev_depth = Some(0);
    }

    /// Depth-0 info line: `● text`.
    pub fn stat(&mut self, text: &str) {
        self.settle_active();
        self.spacer_if_needed(0);
        self.write(format!("{} {}", paint(INFO, STAT), text));
        self.prev_depth = Some(0);
    }

    /// Open an indented sub-step; settles any open one.
    pub fn start(&mut self, label: &str) {
        self.settle_active();
        self.spacer_if_needed(1);
        self.active = Some((label.to_string(), String::new()));
        self.prev_depth = Some(1);
    }

    /// Update the active sub-step's value.
    pub fn tick(&mut self, value: &str) {
        if let Some((_, active)) = self.active.as_mut() {
            *active = value.to_string();
        }
    }

    /// Settle the active sub-step as `◆ label — value`.
    pub fn finish(&mut self, value: &str) {
        if let Some((_, active)) = self.active.as_mut() {
            *active = value.to_string();
        }
        self.settle_active();
    }

    /// Settle the open step as failed, then close with `└ Failed`.
    pub fn fail_close(&mut self) {
        if let Some((label, _)) = self.active.take() {
            self.write(format!(
                "{}  {FAILED} {label} {SEPARATOR} {}",
                paint(DIM, RAIL),
                paint(crate::display::ERROR, "failed")
            ));
        }
        self.close("Failed");
    }

    /// Close the rail; unsettled active steps settle as done.
    pub fn close(&mut self, label: &str) {
        if self.closed {
            return;
        }
        self.closed = true;
        self.settle_active();
        self.write(RAIL.to_string());
        let close = format!("{CLOSE} {label}");
        if self.emit {
            crate::display::console_print(&paint(SUCCESS, &close));
        } else {
            self.lines.push(close);
        }
    }
}

impl Drop for Rail {
    fn drop(&mut self) {
        self.close("Done");
    }
}

/// The `label:14 value` panel line (shared by kv output and the panel body).
pub fn kv_line(label: &str, value: &str) -> String {
    format!("{} {}", paint(LABEL, &format!("{label:14}")), value)
}

fn pad_cell(text: &str, width: usize, right: bool) -> String {
    if right {
        format!("{text:>width$}")
    } else {
        format!("{text:<width$}")
    }
}

fn table_lines(title: &str, columns: [&str; 2], rows: &[[String; 2]]) -> Vec<String> {
    let right = columns.map(|c| {
        matches!(
            c.to_lowercase().as_str(),
            "count" | "n" | "calls" | "symbols" | "edges" | "files" | "repos"
        )
    });
    let mut widths = [columns[0].len(), columns[1].len()];
    for row in rows {
        for (i, cell) in row.iter().enumerate() {
            widths[i] = widths[i].max(cell.chars().count());
        }
    }
    let total = widths.iter().map(|w| w + 2).sum::<usize>() + widths.len() + 1;
    let title_len = title.chars().count();
    let left = total.saturating_sub(title_len) / 2;
    let mut out = vec![format!(
        "{}{}{}",
        " ".repeat(left),
        title,
        " ".repeat(total.saturating_sub(left + title_len))
    )];
    let line = |l: &str, m: &str, r: &str, heavy: bool| {
        let rule = if heavy { "━" } else { "─" };
        let mut s = String::from(l);
        for (i, w) in widths.iter().enumerate() {
            if i != 0 {
                s.push_str(m);
            }
            s.push_str(&rule.repeat(w + 2));
        }
        s.push_str(r);
        s
    };
    out.push(line("┏", "┳", "┓", true));
    let header = columns
        .iter()
        .enumerate()
        .map(|(i, c)| format!(" {} ", pad_cell(c, widths[i], right[i])))
        .collect::<Vec<_>>()
        .join("┃");
    out.push(format!("┃{header}┃"));
    out.push(line("┡", "╇", "┩", true));
    for row in rows {
        let cells = row
            .iter()
            .enumerate()
            .map(|(i, cell)| format!(" {} ", pad_cell(cell, widths[i], right[i])))
            .collect::<Vec<_>>()
            .join("│");
        out.push(format!("│{cells}│"));
    }
    out.push(line("└", "┴", "┘", false));
    out
}

/// Render a titled two-column table (rich grid parity, NO_COLOR shapes).
pub fn print_table(title: &str, columns: [&str; 2], rows: &[[String; 2]]) {
    for line in table_lines(title, columns, rows) {
        crate::display::console_print(&line);
    }
}

fn panel_lines(title: &str, kv_pairs: &[(&str, String)], subtitle: Option<&str>) -> Vec<String> {
    let mut out = vec![format!(
        "╭─ {} {}╮",
        title,
        "─".repeat(WIDTH.saturating_sub(title.chars().count() + 5))
    )];
    for (label, value) in kv_pairs {
        out.push(format!("│ {:<77}│", kv_line(label, value)));
    }
    if let Some(subtitle) = subtitle {
        out.push(format!("│ {:<77}│", ""));
        out.push(format!("│ {:<77}│", paint(DIM, subtitle)));
    }
    out.push(format!("╰{}╯", "─".repeat(WIDTH - 2)));
    out
}

/// Print the final build summary panel (title, kv lines, optional subtitle).
pub fn summary_panel(title: &str, kv_pairs: &[(&str, String)], subtitle: Option<&str>) {
    for line in panel_lines(title, kv_pairs, subtitle) {
        crate::display::console_print(&line);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain_rail() -> Rail {
        // Buffered rail: color-independent settled lines, no stdout.
        Rail::buffered("Initializing cairn")
    }

    #[test]
    fn rail_shape_matches_the_python_capture() {
        let mut rail = plain_rail();
        rail.step("Initialized in", Some("/tmp/ws"));
        rail.detail(".kg", "/tmp/x/.kg");
        rail.start("Scanning files");
        rail.tick("10 found");
        rail.finish("10 found");
        rail.start("Parsing code");
        rail.finish("done");
        rail.warn("Graph already present. Use `cairn build` to rebuild.");
        rail.stat("1,234 nodes, 5,678 edges in 3.2s");
        rail.close("Done");
        assert_eq!(
            rail.lines.join("\n"),
            [
                "┌ Initializing cairn",
                "│",
                "◆ Initialized in — /tmp/ws",
                "│    .kg         /tmp/x/.kg",
                "│",
                "│  ◆ Scanning files — 10 found",
                "│  ◆ Parsing code — done",
                "│",
                "! Graph already present. Use `cairn build` to rebuild.",
                "│",
                "● 1,234 nodes, 5,678 edges in 3.2s",
                "│",
                "└ Done",
            ]
            .join("\n")
        );
    }

    #[test]
    fn kv_line_matches_the_python_padding() {
        assert_eq!(kv_line("repos", "3"), "repos          3");
    }

    #[test]
    fn summary_panel_matches_the_python_grid() {
        // python: display.summary_panel("Built graph in 3.2s", [("files", "1,234")],
        //     "edges resolved: 1,000 exact (10%)  ·  5 ambiguous  ·  0 unresolved")
        let lines = panel_lines(
            "Built graph in 3.2s",
            &[("files", "1,234".into())],
            Some("edges resolved: 1,000 exact (10%)  ·  5 ambiguous  ·  0 unresolved"),
        );
        assert_eq!(lines[0].chars().count(), 80);
        assert!(lines[0].starts_with("╭─ Built graph in 3.2s "));
        assert!(lines[0].ends_with('╮'));
        assert_eq!(lines[1], format!("│ {:<77}│", kv_line("files", "1,234")));
        assert_eq!(lines[2], format!("│ {:<77}│", ""));
        assert_eq!(
            lines.last().expect("bottom"),
            &format!("╰{}╯", "─".repeat(78))
        );
    }

    #[test]
    fn table_matches_the_python_grid() {
        let table = table_lines(
            "By repo",
            ["repo", "count"],
            &[["cairn".into(), "123".into()], ["yarl".into(), "45".into()]],
        );
        assert_eq!(
            table.join("\n"),
            "     By repo     \n\
             ┏━━━━━━━┳━━━━━━━┓\n\
             ┃ repo  ┃ count ┃\n\
             ┡━━━━━━━╇━━━━━━━┩\n\
             │ cairn │   123 │\n\
             │ yarl  │    45 │\n\
             └───────┴───────┘"
        );
    }
}
