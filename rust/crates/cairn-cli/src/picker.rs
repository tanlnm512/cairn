//! Interactive terminal multi-select: keyboard, number keys, and mouse clicks.

use std::io::{self, IsTerminal, Write as _};

use crossterm::cursor::{Hide, MoveTo, Show};
use crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind,
    KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use crossterm::style::Print;
use crossterm::terminal::{self, Clear, ClearType, EnterAlternateScreen, LeaveAlternateScreen};
use crossterm::{execute, queue};

/// One selectable row: display label and initial selection state.
pub struct PickerItem {
    pub label: String,
    pub preselected: bool,
}

/// Completed pick: confirmed selection indices (item order) or user abort.
pub enum PickOutcome {
    Confirmed(Vec<usize>),
    Aborted,
}

/// Raw-mode + alternate-screen + mouse-capture scope; restores the terminal on drop.
struct RawGuard;

impl RawGuard {
    fn enable() -> io::Result<Self> {
        terminal::enable_raw_mode()?;
        let mounted = execute!(io::stdout(), EnterAlternateScreen, EnableMouseCapture, Hide);
        if let Err(e) = mounted {
            let _ = terminal::disable_raw_mode();
            return Err(e);
        }
        Ok(Self)
    }
}

impl Drop for RawGuard {
    fn drop(&mut self) {
        let _ = execute!(
            io::stdout(),
            DisableMouseCapture,
            Show,
            LeaveAlternateScreen
        );
        let _ = terminal::disable_raw_mode();
    }
}

/// Static layout of one rendered picker: header at row 0, item i at row i+1.
struct View<'a> {
    header: &'a str,
    hint: &'a str,
    items: &'a [PickerItem],
    width: usize,
}

/// Whether the environment can render the interactive picker.
fn interactive_capable() -> bool {
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        return false;
    }
    #[cfg(unix)]
    match std::env::var("TERM") {
        Ok(term) if term != "dumb" => {}
        _ => return false,
    }
    true
}

/// Truncate to `width` chars on a char boundary, marking cuts with '…'.
fn truncate(text: &str, width: usize) -> String {
    if text.chars().count() <= width {
        return text.to_string();
    }
    let mut out: String = text.chars().take(width.saturating_sub(1)).collect();
    out.push('…');
    out
}

/// Interactive multi-select over `items`; None when the terminal cannot
/// render it, so the caller falls back to its line-based prompt.
pub fn pick(header: &str, items: &[PickerItem], hint: &str) -> Option<PickOutcome> {
    if !interactive_capable() || items.is_empty() {
        return None;
    }
    let (width, height) = terminal::size().ok()?;
    if items.len() + 2 > height as usize {
        return None;
    }
    let _raw = RawGuard::enable().ok()?;
    let mut out = io::stdout();
    let mut view = View {
        header,
        hint,
        items,
        width: width as usize,
    };

    let mut selected: Vec<bool> = items.iter().map(|i| i.preselected).collect();
    let mut cursor_idx = 0usize;
    render(&mut out, &view, &selected, cursor_idx);

    let outcome = loop {
        let Ok(ev) = event::read() else {
            break PickOutcome::Aborted;
        };
        match ev {
            Event::Key(key) if key.kind == KeyEventKind::Press => {
                if let Some(result) = handle_key(key, items.len(), &mut selected, &mut cursor_idx) {
                    break result;
                }
            }
            Event::Mouse(mouse) => {
                if let Some(index) = handle_mouse(mouse, items.len(), &mut cursor_idx) {
                    selected[index] = !selected[index];
                }
            }
            Event::Resize(cols, rows) => {
                view.width = cols as usize;
                if items.len() + 2 > rows as usize {
                    break PickOutcome::Aborted;
                }
            }
            _ => {}
        }
        render(&mut out, &view, &selected, cursor_idx);
    };

    drop(_raw);
    Some(outcome)
}

/// Apply one key press; Some(outcome) ends the interaction.
fn handle_key(
    key: KeyEvent,
    count: usize,
    selected: &mut [bool],
    cursor_idx: &mut usize,
) -> Option<PickOutcome> {
    let plain = key.modifiers == KeyModifiers::NONE || key.modifiers == KeyModifiers::SHIFT;
    match (key.code, key.modifiers) {
        (KeyCode::Enter, _) => {
            let picked: Vec<usize> = selected
                .iter()
                .enumerate()
                .filter(|(_, on)| **on)
                .map(|(i, _)| i)
                .collect();
            Some(PickOutcome::Confirmed(picked))
        }
        (KeyCode::Esc, _) | (KeyCode::Char('c'), KeyModifiers::CONTROL) => {
            Some(PickOutcome::Aborted)
        }
        (KeyCode::Up, _) | (KeyCode::Char('k'), _) if plain => {
            *cursor_idx = cursor_idx.checked_sub(1).unwrap_or(count - 1);
            None
        }
        (KeyCode::Down, _) | (KeyCode::Char('j'), _) if plain => {
            *cursor_idx = (*cursor_idx + 1) % count;
            None
        }
        (KeyCode::Char(' '), _) if plain => {
            selected[*cursor_idx] = !selected[*cursor_idx];
            None
        }
        (KeyCode::Char('a'), _) if plain => {
            let all_on = selected.iter().all(|on| *on);
            selected.iter_mut().for_each(|on| *on = !all_on);
            None
        }
        (KeyCode::Char(digit @ '1'..='9'), _) if plain => {
            let index = digit.to_digit(10).unwrap_or(1) as usize - 1;
            if index < count {
                *cursor_idx = index;
                selected[index] = !selected[index];
            }
            None
        }
        _ => None,
    }
}

/// Apply one mouse event; Some(index) toggles that item.
fn handle_mouse(mouse: MouseEvent, count: usize, cursor_idx: &mut usize) -> Option<usize> {
    match mouse.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            let index = usize::from(mouse.row.checked_sub(1)?);
            if index < count {
                *cursor_idx = index;
                Some(index)
            } else {
                None
            }
        }
        MouseEventKind::ScrollUp => {
            *cursor_idx = cursor_idx.checked_sub(1).unwrap_or(count - 1);
            None
        }
        MouseEventKind::ScrollDown => {
            *cursor_idx = (*cursor_idx + 1) % count;
            None
        }
        _ => None,
    }
}

/// Redraw the block; every line is one terminal row.
fn render(out: &mut io::Stdout, view: &View, selected: &[bool], cursor_idx: usize) {
    let _ = queue!(
        out,
        MoveTo(0, 0),
        Clear(ClearType::All),
        Print(format!("{}\r\n", truncate(view.header, view.width)))
    );
    for (index, item) in view.items.iter().enumerate() {
        let mark = if selected[index] { "●" } else { "○" };
        let point = if index == cursor_idx { "❯" } else { " " };
        let row = format!(" {point} {mark} {}. {}", index + 1, item.label);
        let _ = queue!(out, Print(format!("{}\r\n", truncate(&row, view.width))));
    }
    let _ = queue!(
        out,
        Print(format!("{}\r\n", truncate(view.hint, view.width)))
    );
    let _ = out.flush();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn click(row: u16) -> MouseEvent {
        MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 4,
            row,
            modifiers: KeyModifiers::NONE,
        }
    }

    #[test]
    fn mouse_clicks_toggle_the_clicked_row_only() {
        let count = 6;
        let mut cursor = 0;
        assert_eq!(handle_mouse(click(1), count, &mut cursor), Some(0));
        assert_eq!(handle_mouse(click(3), count, &mut cursor), Some(2));
        assert_eq!(cursor, 2);
        assert_eq!(
            handle_mouse(click(0), count, &mut cursor),
            None,
            "a click on the header row must not map to an item"
        );
        assert_eq!(
            handle_mouse(click(9), count, &mut cursor),
            None,
            "a click below the list must not map to an item"
        );
    }

    #[test]
    fn digit_keys_toggle_items_and_out_of_range_digits_are_ignored() {
        let mut selected = vec![false; 4];
        let mut cursor = 0;
        let key = |c: char| KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE);
        assert!(handle_key(key('3'), 4, &mut selected, &mut cursor).is_none());
        assert!(selected[2] && cursor == 2);
        assert!(handle_key(key('9'), 4, &mut selected, &mut cursor).is_none());
        assert_eq!(selected, vec![false, false, true, false]);
    }

    #[test]
    fn enter_collects_selection_and_escape_aborts() {
        let mut selected = vec![true, false, true];
        let mut cursor = 0;
        let Some(PickOutcome::Confirmed(picked)) = handle_key(
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE),
            3,
            &mut selected,
            &mut cursor,
        ) else {
            panic!("enter must confirm");
        };
        assert_eq!(picked, vec![0, 2]);
        let mut selected = vec![true];
        assert!(matches!(
            handle_key(
                KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE),
                1,
                &mut selected,
                &mut cursor
            ),
            Some(PickOutcome::Aborted)
        ));
    }

    #[test]
    fn truncate_marks_cuts_and_respects_char_boundaries() {
        assert_eq!(truncate("abc", 5), "abc");
        assert_eq!(truncate("abcdef", 5), "abcd…");
        assert_eq!(truncate("αβγδε", 4), "αβγ…");
    }
}
