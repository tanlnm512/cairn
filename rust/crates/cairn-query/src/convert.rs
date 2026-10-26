//! Native binary-document conversion: docx and PDF bytes to markdown.

use std::collections::HashMap;
use std::io::Read;

/// Skip reason when a document yields no extractable text.
pub const EMPTY_EXTRACTION: &str = "empty extraction";

/// Convert one supported binary document (by suffix) to markdown, or return the skip reason.
pub fn convert_document(suffix: &str, bytes: &[u8]) -> Result<String, String> {
    match suffix {
        ".docx" => docx_to_markdown(bytes),
        ".pdf" => pdf_to_markdown(bytes),
        other => Err(format!("unsupported binary type: {other}")),
    }
}

/// PDF text extraction (pdf-extract) normalized to markdown paragraphs.
fn pdf_to_markdown(bytes: &[u8]) -> Result<String, String> {
    let raw =
        pdf_extract::extract_text_from_mem(bytes).map_err(|e| format!("conversion failed: {e}"))?;
    let text = text_paragraphs(&raw).join("\n\n");
    if text.trim().is_empty() {
        return Err(EMPTY_EXTRACTION.to_string());
    }
    Ok(text)
}

/// Blank-line-separated line groups of `raw`, each collapsed to one line.
fn text_paragraphs(raw: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut open = false;
    for line in raw.lines() {
        let collapsed = line.split_whitespace().collect::<Vec<_>>().join(" ");
        if collapsed.is_empty() {
            open = false;
            continue;
        }
        if open && let Some(prev) = out.last_mut() {
            prev.push(' ');
            prev.push_str(&collapsed);
        } else {
            out.push(collapsed);
            open = true;
        }
    }
    out
}

// --- docx (wordprocessingml -> markdown) ------------------------------------------

/// The local name of a possibly prefixed XML tag.
fn local_name(tag: &str) -> &str {
    tag.rsplit(':').next().unwrap_or_default()
}

struct DocxParts {
    styles: HashMap<String, u8>,
    num_to_abstract: HashMap<String, String>,
    abstract_formats: HashMap<String, HashMap<u32, String>>,
    rels: HashMap<String, String>,
    document: String,
}

fn zip_part(zip: &mut zip::ZipArchive<std::io::Cursor<&[u8]>>, name: &str) -> Option<String> {
    let mut entry = zip.by_name(name).ok()?;
    let mut text = String::new();
    entry.read_to_string(&mut text).ok()?;
    Some(text)
}

/// docx to markdown: headings, paragraphs, emphasis, lists, hyperlinks, tables.
fn docx_to_markdown(bytes: &[u8]) -> Result<String, String> {
    let cursor = std::io::Cursor::new(bytes);
    let mut zip = zip::ZipArchive::new(cursor).map_err(|e| format!("conversion failed: {e}"))?;
    let styles = zip_part(&mut zip, "word/styles.xml")
        .map(|xml| parse_styles(&xml))
        .unwrap_or_default();
    let (num_to_abstract, abstract_formats) = zip_part(&mut zip, "word/numbering.xml")
        .map(|xml| parse_numbering(&xml))
        .unwrap_or_default();
    let rels = zip_part(&mut zip, "word/_rels/document.xml.rels")
        .map(|xml| parse_rels(&xml))
        .unwrap_or_default();
    let document = zip_part(&mut zip, "word/document.xml")
        .ok_or_else(|| "conversion failed: word/document.xml missing".to_string())?;
    let blocks = scan_document(&DocxParts {
        styles,
        num_to_abstract,
        abstract_formats,
        rels,
        document,
    })?;
    let text = render_blocks(blocks);
    if text.trim().is_empty() {
        return Err(EMPTY_EXTRACTION.to_string());
    }
    Ok(text)
}

/// "heading N" (any case, optional space) -> N, clamped to 1..=6.
fn heading_level(name: &str) -> Option<u8> {
    let lowered = name.to_ascii_lowercase();
    let level: u8 = lowered.strip_prefix("heading")?.trim().parse().ok()?;
    (1..=6).contains(&level).then_some(level)
}

fn attr_map(e: &quick_xml::events::BytesStart<'_>) -> HashMap<String, String> {
    e.attributes()
        .filter_map(|a| a.ok())
        .filter_map(|a| {
            Some((
                local_name(a.key.as_ref()).to_string(),
                a.normalized_value(quick_xml::XmlVersion::Implicit1_0)
                    .ok()?
                    .into_owned(),
            ))
        })
        .collect()
}

/// styleId -> heading level for styles whose `w:name` is "heading N".
fn parse_styles(xml: &str) -> HashMap<String, u8> {
    let mut out = HashMap::new();
    let mut reader = quick_xml::Reader::from_str(xml);
    let mut current: Option<(String, Option<u8>)> = None;
    while let Ok(event) = reader.read_event() {
        match event {
            quick_xml::events::Event::Start(ref e) | quick_xml::events::Event::Empty(ref e) => {
                match local_name(e.name().as_ref()) {
                    "style" => {
                        current = attr_map(e).get("styleId").cloned().map(|id| (id, None));
                    }
                    "name" => {
                        if let Some((id, _)) = current.take() {
                            let level = attr_map(e).get("val").and_then(|v| heading_level(v));
                            current = Some((id, level));
                        }
                    }
                    _ => {}
                }
            }
            quick_xml::events::Event::End(ref e) => {
                if local_name(e.name().as_ref()) == "style"
                    && let Some((id, level)) = current.take()
                    && let Some(level) = level
                {
                    out.insert(id, level);
                }
            }
            quick_xml::events::Event::Eof => break,
            _ => {}
        }
    }
    out
}

/// (numId -> abstractNumId, abstractNumId -> ilvl -> numFmt) from numbering.xml.
fn parse_numbering(
    xml: &str,
) -> (
    HashMap<String, String>,
    HashMap<String, HashMap<u32, String>>,
) {
    let mut num_to_abstract = HashMap::new();
    let mut abstract_formats: HashMap<String, HashMap<u32, String>> = HashMap::new();
    let mut reader = quick_xml::Reader::from_str(xml);
    let mut abstract_id: Option<String> = None;
    let mut lvl_ilvl: Option<u32> = None;
    let mut num_id: Option<String> = None;
    while let Ok(event) = reader.read_event() {
        let (name, attrs) = match event {
            quick_xml::events::Event::Start(ref e) | quick_xml::events::Event::Empty(ref e) => {
                let name = local_name(e.name().as_ref()).to_string();
                (name, attr_map(e))
            }
            quick_xml::events::Event::End(ref e) => {
                match local_name(e.name().as_ref()) {
                    "abstractNum" => abstract_id = None,
                    "num" => num_id = None,
                    "lvl" => lvl_ilvl = None,
                    _ => {}
                }
                continue;
            }
            quick_xml::events::Event::Eof => break,
            _ => continue,
        };
        match name.as_str() {
            "abstractNum" => abstract_id = attrs.get("abstractNumId").cloned(),
            "num" => num_id = attrs.get("numId").cloned(),
            "lvl" => lvl_ilvl = attrs.get("ilvl").and_then(|v| v.parse().ok()),
            "numFmt" => {
                if let (Some(ilvl), Some(abstract_id)) = (lvl_ilvl, abstract_id.clone())
                    && let Some(fmt) = attrs.get("val")
                {
                    abstract_formats
                        .entry(abstract_id)
                        .or_default()
                        .insert(ilvl, fmt.clone());
                }
            }
            "abstractNumId" => {
                if let (Some(num), Some(abstract_ref)) = (num_id.clone(), attrs.get("val")) {
                    num_to_abstract.insert(num, abstract_ref.clone());
                }
            }
            _ => {}
        }
    }
    (num_to_abstract, abstract_formats)
}

/// Relationship Id -> Target from a .rels part.
fn parse_rels(xml: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    let mut reader = quick_xml::Reader::from_str(xml);
    while let Ok(event) = reader.read_event() {
        let (Event::Start(ref e) | Event::Empty(ref e)) = event else {
            if matches!(event, quick_xml::events::Event::Eof) {
                break;
            }
            continue;
        };
        if local_name(e.name().as_ref()) != "Relationship" {
            continue;
        }
        let attrs = attr_map(e);
        if let (Some(id), Some(target)) = (attrs.get("Id"), attrs.get("Target")) {
            out.insert(id.clone(), target.clone());
        }
    }
    out
}

/// A rendered top-level block; consecutive same-kind items merge into one list block.
enum Block {
    Para(String),
    Item { text: String, ordered: bool },
}

#[derive(Default)]
struct RunFmt {
    bold: bool,
    italic: bool,
}

struct ParaState {
    text: String,
    heading: Option<u8>,
    num: Option<(String, u32)>,
}

struct TableState {
    rows: Vec<Vec<String>>,
    row: Vec<String>,
}

struct DocxScan<'a> {
    parts: &'a DocxParts,
    blocks: Vec<Block>,
    para: Option<ParaState>,
    tables: Vec<TableState>,
    cell: Option<String>,
    counters: HashMap<(String, u32), u32>,
    hyperlink: Option<(usize, Option<String>)>,
    run_fmt: RunFmt,
    pending_ilvl: Option<u32>,
    in_run: bool,
    in_run_props: bool,
    in_para_props: bool,
}

use quick_xml::events::Event;

fn scan_document(parts: &DocxParts) -> Result<Vec<Block>, String> {
    let mut scan = DocxScan {
        parts,
        blocks: Vec::new(),
        para: None,
        tables: Vec::new(),
        cell: None,
        counters: HashMap::new(),
        hyperlink: None,
        run_fmt: RunFmt::default(),
        pending_ilvl: None,
        in_run: false,
        in_run_props: false,
        in_para_props: false,
    };
    let mut reader = quick_xml::Reader::from_str(&parts.document);
    loop {
        let event = reader
            .read_event()
            .map_err(|e| format!("conversion failed: {e}"))?;
        match event {
            Event::Start(e) => scan.start(e),
            Event::Empty(e) => {
                let name = local_name(e.name().as_ref()).to_string();
                scan.start(e);
                scan.end(&name);
            }
            Event::End(e) => scan.end(e.name().as_ref()),
            Event::Text(t) => scan.text(&t),
            Event::Eof => break,
            _ => {}
        }
    }
    Ok(scan.blocks)
}

/// `w:val` toggle semantics: absent/true/on/1 means on.
fn val_toggle(attrs: &HashMap<String, String>) -> bool {
    !matches!(
        attrs.get("val").map(String::as_str),
        Some("0" | "false" | "none" | "off")
    )
}

impl DocxScan<'_> {
    fn start(&mut self, e: quick_xml::events::BytesStart<'_>) {
        let attrs = attr_map(&e);
        match local_name(e.name().as_ref()) {
            "p" => {
                self.para = Some(ParaState {
                    text: String::new(),
                    heading: None,
                    num: None,
                });
            }
            "r" => {
                self.in_run = true;
                self.run_fmt = RunFmt::default();
            }
            "rPr" if self.in_run => self.in_run_props = true,
            "b" if self.in_run_props => self.run_fmt.bold = val_toggle(&attrs),
            "i" if self.in_run_props => self.run_fmt.italic = val_toggle(&attrs),
            "pPr" => self.in_para_props = true,
            "pStyle" if self.in_para_props => {
                let style = attrs.get("val").cloned().unwrap_or_default();
                let level = self
                    .parts
                    .styles
                    .get(&style)
                    .copied()
                    .or_else(|| heading_level(&style));
                if let Some(para) = self.para.as_mut() {
                    para.heading = level;
                }
            }
            "ilvl" if self.in_para_props => {
                let ilvl = attrs.get("val").and_then(|v| v.parse().ok());
                self.pending_ilvl = ilvl;
                if let (Some(para), Some(ilvl)) = (self.para.as_mut(), ilvl)
                    && let Some((_, level)) = para.num.as_mut()
                {
                    *level = ilvl;
                }
            }
            "numId" if self.in_para_props => {
                if let Some(num) = attrs.get("val").cloned()
                    && !num.is_empty()
                    && let Some(para) = self.para.as_mut()
                {
                    let ilvl = para.num.as_ref().map_or(0, |(_, l)| *l);
                    para.num = Some((num, ilvl));
                }
            }
            "hyperlink" => {
                let target = attrs
                    .get("id")
                    .and_then(|id| self.parts.rels.get(id).cloned());
                self.hyperlink = self.para.as_ref().map(|p| (p.text.len(), target));
            }
            "tbl" => self.tables.push(TableState {
                rows: Vec::new(),
                row: Vec::new(),
            }),
            "tr" => {
                if let Some(table) = self.tables.last_mut() {
                    table.row = Vec::new();
                }
            }
            "tc" => self.cell = Some(String::new()),
            "tab" => self.push_inline(" "),
            "br" => self.push_inline("\n"),
            _ => {}
        }
    }

    fn end(&mut self, name: &str) {
        match local_name(name) {
            "p" => self.close_para(),
            "r" => self.in_run = false,
            "rPr" if self.in_run => self.in_run_props = false,
            "pPr" => {
                self.in_para_props = false;
                if let (Some(para), Some(ilvl)) = (self.para.as_mut(), self.pending_ilvl.take())
                    && let Some((_, level)) = para.num.as_mut()
                {
                    *level = ilvl;
                }
            }
            "hyperlink" => self.close_hyperlink(),
            "tc" => self.close_cell(),
            "tr" => self.close_row(),
            "tbl" => self.close_table(),
            _ => {}
        }
    }

    /// Whitespace or literal text contributed by `w:tab`/`w:br`.
    fn push_inline(&mut self, s: &str) {
        if let Some(para) = self.para.as_mut() {
            para.text.push_str(s);
        }
    }

    fn text(&mut self, t: &quick_xml::events::BytesText<'_>) {
        let Ok(text) = quick_xml::escape::unescape(t.as_ref()) else {
            return;
        };
        let Some(para) = self.para.as_mut() else {
            return;
        };
        let core = text.trim();
        let lead_len = text.len() - text.trim_start().len();
        let lead = &text[..lead_len];
        let trail = &text[lead_len + core.len()..];
        para.text.push_str(lead);
        if !core.is_empty() {
            let marker = match (self.run_fmt.bold, self.run_fmt.italic) {
                (true, true) => "***",
                (true, false) => "**",
                (false, true) => "*",
                _ => "",
            };
            para.text.push_str(marker);
            para.text.push_str(core);
            para.text.push_str(marker);
        }
        para.text.push_str(trail);
    }

    fn close_hyperlink(&mut self) {
        let Some((at, target)) = self.hyperlink.take() else {
            return;
        };
        let Some(para) = self.para.as_mut() else {
            return;
        };
        if at > para.text.len() {
            return;
        }
        let inner = para.text[at..].to_string();
        para.text.truncate(at);
        if inner.trim().is_empty() {
            para.text.push_str(&inner);
        } else if let Some(url) = target {
            para.text.push_str(&format!("[{}]({})", inner.trim(), url));
        } else {
            para.text.push_str(&inner);
        }
    }

    fn close_para(&mut self) {
        let Some(para) = self.para.take() else {
            return;
        };
        let inline = para.text.trim().to_string();
        if let Some(cell) = self.cell.as_mut() {
            if !inline.is_empty() {
                if !cell.is_empty() {
                    cell.push(' ');
                }
                cell.push_str(&inline.replace('\n', " "));
            }
            return;
        }
        if inline.is_empty() {
            return;
        }
        if let Some((num, ilvl)) = para.num {
            let (marker, ordered) = self.list_marker(&num, ilvl);
            let indent = "  ".repeat(ilvl as usize);
            self.blocks.push(Block::Item {
                text: format!("{indent}{marker}{inline}"),
                ordered,
            });
        } else if let Some(level) = para.heading {
            self.blocks.push(Block::Para(format!(
                "{} {inline}",
                "#".repeat(level as usize)
            )));
        } else {
            self.blocks.push(Block::Para(inline));
        }
    }

    /// The rendered marker and ordered-ness for one list item, advancing per-(num, level) counters.
    fn list_marker(&mut self, num: &str, ilvl: u32) -> (String, bool) {
        let fmt = self
            .parts
            .num_to_abstract
            .get(num)
            .and_then(|abstract_id| self.parts.abstract_formats.get(abstract_id))
            .and_then(|levels| levels.get(&ilvl))
            .map(String::as_str)
            .unwrap_or("bullet");
        let n = {
            let counter = self.counters.entry((num.to_string(), ilvl)).or_insert(0);
            *counter += 1;
            *counter
        };
        let deeper: Vec<(String, u32)> = self
            .counters
            .keys()
            .filter(|(n2, l)| n2 == num && *l > ilvl)
            .cloned()
            .collect();
        for key in deeper {
            self.counters.insert(key, 0);
        }
        if fmt == "bullet" {
            ("- ".to_string(), false)
        } else {
            (format!("{n}. "), true)
        }
    }

    fn close_cell(&mut self) {
        let cell = self.cell.take().unwrap_or_default();
        if let Some(table) = self.tables.last_mut() {
            table.row.push(cell.trim().to_string());
        }
    }

    fn close_row(&mut self) {
        if let Some(table) = self.tables.last_mut() {
            let row = std::mem::take(&mut table.row);
            if !row.is_empty() {
                table.rows.push(row);
            }
        }
    }

    fn close_table(&mut self) {
        let Some(table) = self.tables.pop() else {
            return;
        };
        let rendered: Vec<String> = table
            .rows
            .iter()
            .map(|row| {
                row.iter()
                    .map(|cell| format!("| {} ", cell.replace('|', "\\|")))
                    .collect::<String>()
                    + "|"
            })
            .collect();
        if rendered.is_empty() {
            return;
        }
        let width = table.rows.first().map_or(1, Vec::len).max(1);
        let separator = format!("|{}", " --- |".repeat(width));
        let mut lines = Vec::with_capacity(rendered.len() + 1);
        lines.push(rendered[0].clone());
        lines.push(separator);
        lines.extend(rendered.iter().skip(1).cloned());
        let markdown = lines.join("\n");
        if let Some(cell) = self.cell.as_mut() {
            if !cell.is_empty() {
                cell.push(' ');
            }
            cell.push_str(&markdown.replace('\n', " "));
            return;
        }
        self.blocks.push(Block::Para(markdown));
    }
}

/// Blocks joined with blank lines; adjacent list items stay one list block.
fn render_blocks(blocks: Vec<Block>) -> String {
    let mut out: Vec<(Option<bool>, String)> = Vec::new();
    for block in blocks {
        match block {
            Block::Para(text) => out.push((None, text)),
            Block::Item { text, ordered } => {
                let merges = matches!(out.last(), Some((Some(kind), _)) if *kind == ordered);
                if merges && let Some((_, prev)) = out.last_mut() {
                    prev.push('\n');
                    prev.push_str(&text);
                } else {
                    out.push((Some(ordered), text));
                }
            }
        }
    }
    out.into_iter()
        .map(|(_, text)| text)
        .collect::<Vec<_>>()
        .join("\n\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture_bytes(name: &str) -> Vec<u8> {
        let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/convert")
            .join(name);
        std::fs::read(path).expect("fixture")
    }

    #[test]
    fn unsupported_suffix_reports_the_type() {
        assert_eq!(
            convert_document(".png", b"\x89PNG"),
            Err("unsupported binary type: .png".to_string())
        );
    }

    #[test]
    fn corrupt_docx_and_pdf_report_conversion_failures() {
        assert!(
            convert_document(".docx", b"not a zip at all")
                .unwrap_err()
                .starts_with("conversion failed:")
        );
        assert!(
            convert_document(".pdf", b"%PDF-1.4 garbage")
                .unwrap_err()
                .starts_with("conversion failed:")
        );
    }

    #[test]
    fn blank_pdf_extraction_is_a_skip_reason() {
        assert_eq!(
            convert_document(".pdf", &fixture_bytes("blank.pdf")),
            Err(EMPTY_EXTRACTION.to_string())
        );
    }

    #[test]
    fn text_paragraphs_collapse_blank_runs() {
        assert_eq!(
            text_paragraphs("\n\nDeploy the gateway\n\nRoute  traffic\n"),
            vec![
                "Deploy the gateway".to_string(),
                "Route traffic".to_string()
            ]
        );
    }

    #[test]
    fn heading_level_accepts_style_names() {
        assert_eq!(heading_level("heading 1"), Some(1));
        assert_eq!(heading_level("Heading 2"), Some(2));
        assert_eq!(heading_level("Title"), None);
        assert_eq!(heading_level("heading 9"), None);
    }
}
