//! Language-name dispatch to the cairn-parse family parsers.

use std::path::Path;

use cairn_parse::model::ParsedFile;
use cairn_parse::parser_apple::{ObjCParser, SwiftParser};
use cairn_parse::parser_jvm::{JavaParser, KotlinParser};
use cairn_parse::parser_script::{DartParser, PhpParser, PythonParser, RubyParser};
use cairn_parse::parser_sys::{CFamilyParser, CSharpParser, GoParser};
use cairn_parse::parser_web::WebParser;
use cairn_parse::registry;

/// Whether the routed grammars cover `language`.
pub fn is_language_available(language: &str) -> bool {
    registry::is_language_available(language)
}

/// Parse one file with the parser for `language`; errors mirror the Python
/// per-file contract (a failed parse is a parse_errors row, never a build abort).
pub fn parse_language_file(language: &str, path: &Path) -> Result<ParsedFile, String> {
    let result =
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| parse_inner(language, path)));
    match result {
        Ok(inner) => inner.map_err(|e| e.to_string()),
        Err(payload) => {
            let detail = payload
                .downcast_ref::<&str>()
                .map(|s| s.to_string())
                .or_else(|| payload.downcast_ref::<String>().cloned())
                .unwrap_or_else(|| "parser panicked".to_string());
            Err(format!("parser panic: {detail}"))
        }
    }
}

fn parse_inner(language: &str, path: &Path) -> Result<ParsedFile, std::io::Error> {
    match language {
        "python" => PythonParser::new().parse(path),
        "php" => PhpParser::new().parse(path),
        "ruby" => RubyParser::new().parse(path),
        "dart" => DartParser::new().parse(path),
        "kotlin" => KotlinParser::new().parse(path),
        "java" => JavaParser::new().parse(path),
        "go" => GoParser::parse_file(path),
        "rust" => cairn_parse::parser_sys::parse_rust_file(path),
        "c" => CFamilyParser::c().parse_file(path),
        "cpp" => CFamilyParser::cpp().parse_file(path),
        "csharp" => CSharpParser::parse_file(path),
        "swift" => SwiftParser.parse_file(path),
        "objc" => ObjCParser.parse_file(path),
        "typescript" => WebParser::typescript().parse_file(path),
        "javascript" => WebParser::javascript().parse_file(path),
        other => Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            format!("No parser for {other}"),
        )),
    }
}

/// The Rust generic-tier marker languages (resolver stays name-based for these).
pub fn is_generic_tier_language(language: &str) -> bool {
    language == "rust"
}
