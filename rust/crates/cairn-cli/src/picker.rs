//! Interactive inline terminal picker: single-select and multi-select via keys and mouse.

use std::io::{self, IsTerminal, Write as _};

use crossterm::cursor::{Hide, MoveToColumn, MoveUp, Show, position};
use crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind,
    KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use crossterm::style::Print;
use crossterm::terminal::{self, Clear, ClearType};
use crossterm::{execute, queue};

use crate::display::{DIM, INFO, LABEL, SUCCESS, paint};

/// One selectable row: display label, optional dimmed detail, and initial state.
pub struct PickerItem {
    pub label: String,
    pub detail: String,
    pub preselected: bool,
}

impl PickerItem {
    /// Build a row with a label, detail description, and initial selection flag.
    pub fn new(label: impl Into<String>, detail: impl Into<String>, preselected: bool) -> Self {
        Self {
            label: label.into(),
            detail: detail.into(),
            preselected,
        }
    }
}

/// Completed multi-select pick: confirmed indices (item order) or user abort.
pub enum PickOutcome {
    Confirmed(Vec<usize>),
    Aborted,
}

/// Completed single-select pick: chosen item index or user abort.
pub enum PickOneOutcome {
    Selected(usize),
    Aborted,
}

/// Raw-mode + mouse-capture inline guard; restores the terminal on drop.
struct RawGuard;

impl RawGuard {
    fn enable() -> io::Result<Self> {
        terminal::enable_raw_mode()?;
        let mounted = execute!(io::stdout(), EnableMouseCapture, Hide);
        if let Err(e) = mounted {
            let _ = terminal::disable_raw_mode();
            return Err(e);
        }
        Ok(Self)
    }
}

impl Drop for RawGuard {
    fn drop(&mut self) {
        let _ = execute!(io::stdout(), DisableMouseCapture, Show);
        let _ = terminal::disable_raw_mode();
    }
}

/// Static layout of one rendered picker block.
struct View<'a> {
    header: &'a str,
    hint: &'a str,
    items: &'a [PickerItem],
    width: usize,
}

enum PickerMode {
    Multi(Vec<bool>),
    Single,
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

/// Interactive inline multi-select over `items`; None when fallback is needed.
pub fn pick(header: &str, items: &[PickerItem], hint: &str) -> Option<PickOutcome> {
    let mode = PickerMode::Multi(items.iter().map(|i| i.preselected).collect());
    run_picker(
        header,
        items,
        hint,
        0,
        mode,
        PickOutcome::Aborted,
        |ev, mode, cursor, top_row| {
            let PickerMode::Multi(selected) = mode else {
                return None;
            };
            match ev {
                Event::Key(key) if key.kind == KeyEventKind::Press => {
                    handle_key(key, items.len(), selected, cursor)
                }
                Event::Mouse(mouse) => {
                    if let Some(row) = top_row
                        && let Some(index) = handle_mouse(mouse, row, items.len(), cursor)
                    {
                        selected[index] = !selected[index];
                    }
                    None
                }
                _ => None,
            }
        },
    )
}

/// Interactive inline single-select over `items`; None when fallback is needed.
pub fn pick_one(
    header: &str,
    items: &[PickerItem],
    default_idx: usize,
    hint: &str,
) -> Option<PickOneOutcome> {
    let start = default_idx.min(items.len().saturating_sub(1));
    run_picker(
        header,
        items,
        hint,
        start,
        PickerMode::Single,
        PickOneOutcome::Aborted,
        |ev, _, cursor, top_row| match ev {
            Event::Key(key) if key.kind == KeyEventKind::Press => {
                handle_key_one(key, items.len(), cursor)
            }
            Event::Mouse(mouse) => {
                let row = top_row?;
                handle_mouse(mouse, row, items.len(), cursor).map(PickOneOutcome::Selected)
            }
            _ => None,
        },
    )
}

fn run_picker<T, F>(
    header: &str,
    items: &[PickerItem],
    hint: &str,
    start_idx: usize,
    mut mode: PickerMode,
    aborted: T,
    mut on_event: F,
) -> Option<T>
where
    F: FnMut(Event, &mut PickerMode, &mut usize, Option<u16>) -> Option<T>,
{
    if !interactive_capable() || items.is_empty() {
        return None;
    }
    let (width, height) = terminal::size().ok()?;
    let total_rows = items.len() + 2;
    if total_rows > height as usize {
        return None;
    }
    let _raw = RawGuard::enable().ok()?;
    let total_u16 = total_rows as u16;
    let mut top_row = position()
        .ok()
        .map(|(_, row)| row.min(height.saturating_sub(total_u16)));
    let mut out = io::stdout();
    let mut view = View {
        header,
        hint,
        items,
        width: width as usize,
    };

    let mut cursor_idx = start_idx;
    render(&mut out, &view, &mode, cursor_idx, false);

    let mut result = aborted;
    while let Ok(ev) = event::read() {
        if let Event::Resize(cols, rows) = ev {
            view.width = cols as usize;
            if total_rows > rows as usize {
                break;
            }
            if let Some(top) = top_row.as_mut() {
                *top = (*top).min(rows.saturating_sub(total_u16));
            }
            render(&mut out, &view, &mode, cursor_idx, true);
            continue;
        }
        if let Some(outcome) = on_event(ev, &mut mode, &mut cursor_idx, top_row) {
            result = outcome;
            break;
        }
        render(&mut out, &view, &mode, cursor_idx, true);
    }

    clear_inline(&mut out, items.len());
    drop(_raw);
    Some(result)
}

fn step_cursor(key: &KeyEvent, count: usize, cursor_idx: &mut usize) -> bool {
    let plain = key.modifiers == KeyModifiers::NONE || key.modifiers == KeyModifiers::SHIFT;
    if !plain {
        return false;
    }
    match key.code {
        KeyCode::Up | KeyCode::Char('k') => {
            *cursor_idx = cursor_idx.checked_sub(1).unwrap_or(count - 1);
            true
        }
        KeyCode::Down | KeyCode::Char('j') => {
            *cursor_idx = (*cursor_idx + 1) % count;
            true
        }
        _ => false,
    }
}

/// Apply one multi-select key press; Some(outcome) ends the interaction.
fn handle_key(
    key: KeyEvent,
    count: usize,
    selected: &mut [bool],
    cursor_idx: &mut usize,
) -> Option<PickOutcome> {
    if step_cursor(&key, count, cursor_idx) {
        return None;
    }
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

/// Apply one single-select key press; Some(outcome) ends the interaction.
fn handle_key_one(key: KeyEvent, count: usize, cursor_idx: &mut usize) -> Option<PickOneOutcome> {
    if step_cursor(&key, count, cursor_idx) {
        return None;
    }
    let plain = key.modifiers == KeyModifiers::NONE || key.modifiers == KeyModifiers::SHIFT;
    match (key.code, key.modifiers) {
        (KeyCode::Enter, _) => Some(PickOneOutcome::Selected(*cursor_idx)),
        (KeyCode::Char(' '), _) if plain => Some(PickOneOutcome::Selected(*cursor_idx)),
        (KeyCode::Esc, _) | (KeyCode::Char('c'), KeyModifiers::CONTROL) => {
            Some(PickOneOutcome::Aborted)
        }
        (KeyCode::Char(digit @ '1'..='9'), _) if plain => {
            let index = digit.to_digit(10).unwrap_or(1) as usize - 1;
            if index < count {
                *cursor_idx = index;
                Some(PickOneOutcome::Selected(index))
            } else {
                None
            }
        }
        _ => None,
    }
}

/// Apply one mouse event relative to `top_row`; Some(index) targets that row.
fn handle_mouse(
    mouse: MouseEvent,
    top_row: u16,
    count: usize,
    cursor_idx: &mut usize,
) -> Option<usize> {
    match mouse.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            let rel = mouse.row.checked_sub(top_row)?;
            let index = usize::from(rel.checked_sub(1)?);
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

fn format_row(
    index: usize,
    item: &PickerItem,
    mode: &PickerMode,
    cursor_idx: usize,
    max_w: usize,
) -> String {
    let is_cur = index == cursor_idx;
    let (mark_char, mark_style) = match mode {
        PickerMode::Multi(selected) => {
            if selected[index] {
                ("●", SUCCESS)
            } else {
                ("○", DIM)
            }
        }
        PickerMode::Single => {
            if is_cur {
                ("◉", SUCCESS)
            } else {
                ("◯", DIM)
            }
        }
    };
    let num_prefix = format!("{}. ", index + 1);
    let fixed_chars = 5 + num_prefix.chars().count();
    let avail = max_w.saturating_sub(fixed_chars);
    let label_len = item.label.chars().count();
    let (label_text, detail_text) = if avail == 0 {
        (String::new(), String::new())
    } else if item.detail.is_empty() || label_len >= avail {
        (truncate(&item.label, avail), String::new())
    } else if avail > label_len + 4 {
        (
            item.label.clone(),
            truncate(&item.detail, avail - label_len - 4),
        )
    } else {
        (item.label.clone(), String::new())
    };
    let pointer = if is_cur {
        paint(INFO, "❯")
    } else {
        " ".into()
    };
    let mark = paint(mark_style, mark_char);
    let main_part = format!("{num_prefix}{label_text}");
    let main_styled = if is_cur {
        paint(LABEL, &main_part)
    } else {
        main_part
    };
    let detail_styled = if detail_text.is_empty() {
        String::new()
    } else {
        paint(DIM, &format!("  — {detail_text}"))
    };
    format!(" {pointer} {mark} {main_styled}{detail_styled}")
}

fn clear_inline(out: &mut io::Stdout, item_count: usize) {
    let up = (item_count + 1) as u16;
    let _ = execute!(
        out,
        MoveUp(up),
        MoveToColumn(0),
        Clear(ClearType::FromCursorDown)
    );
    let _ = out.flush();
}

/// Redraw the inline block in place without entering an alternate screen.
fn render(out: &mut io::Stdout, view: &View, mode: &PickerMode, cursor_idx: usize, redraw: bool) {
    let max_w = view.width.saturating_sub(1).max(1);
    if redraw {
        let up = (view.items.len() + 1) as u16;
        let _ = queue!(out, MoveUp(up));
    }
    let _ = queue!(
        out,
        MoveToColumn(0),
        Clear(ClearType::FromCursorDown),
        Print(format!(
            "{}\r\n",
            paint(LABEL, &truncate(view.header, max_w))
        ))
    );
    for (index, item) in view.items.iter().enumerate() {
        let row = format_row(index, item, mode, cursor_idx, max_w);
        let _ = queue!(out, Print(format!("{row}\r\n")));
    }
    let _ = queue!(out, Print(paint(DIM, &truncate(view.hint, max_w))));
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
        assert_eq!(handle_mouse(click(1), 0, count, &mut cursor), Some(0));
        assert_eq!(handle_mouse(click(3), 0, count, &mut cursor), Some(2));
        assert_eq!(cursor, 2);
        assert_eq!(
            handle_mouse(click(0), 0, count, &mut cursor),
            None,
            "a click on the header row must not map to an item"
        );
        assert_eq!(
            handle_mouse(click(9), 0, count, &mut cursor),
            None,
            "a click below the list must not map to an item"
        );
        // Inline picker offset below top_row=12: header=12, items=13..18, hint=19.
        assert_eq!(handle_mouse(click(12), 12, count, &mut cursor), None);
        assert_eq!(handle_mouse(click(13), 12, count, &mut cursor), Some(0));
        assert_eq!(handle_mouse(click(18), 12, count, &mut cursor), Some(5));
        assert_eq!(handle_mouse(click(19), 12, count, &mut cursor), None);
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
    fn single_select_keys_navigate_and_confirm() {
        let mut cursor = 0;
        let key = |code| KeyEvent::new(code, KeyModifiers::NONE);
        assert!(handle_key_one(key(KeyCode::Down), 3, &mut cursor).is_none());
        assert_eq!(cursor, 1);
        assert!(handle_key_one(key(KeyCode::Up), 3, &mut cursor).is_none());
        assert_eq!(cursor, 0);
        assert!(handle_key_one(key(KeyCode::Up), 3, &mut cursor).is_none());
        assert_eq!(cursor, 2);
        assert!(matches!(
            handle_key_one(key(KeyCode::Enter), 3, &mut cursor),
            Some(PickOneOutcome::Selected(2))
        ));
        assert!(matches!(
            handle_key_one(key(KeyCode::Char(' ')), 3, &mut cursor),
            Some(PickOneOutcome::Selected(2))
        ));
        assert!(matches!(
            handle_key_one(key(KeyCode::Char('1')), 3, &mut cursor),
            Some(PickOneOutcome::Selected(0))
        ));
        assert!(handle_key_one(key(KeyCode::Char('9')), 3, &mut cursor).is_none());
        assert!(matches!(
            handle_key_one(key(KeyCode::Esc), 3, &mut cursor),
            Some(PickOneOutcome::Aborted)
        ));
    }

    #[test]
    fn truncate_marks_cuts_and_respects_char_boundaries() {
        assert_eq!(truncate("abc", 5), "abc");
        assert_eq!(truncate("abcdef", 5), "abcd…");
        assert_eq!(truncate("αβγδε", 4), "αβγ…");
    }
}
