//! Parsed-file data model mirroring the Python parser contract.

use sha2::{Digest, Sha256};

/// JSON payload for symbol-kind-specific metadata.
pub type Metadata = serde_json::Map<String, serde_json::Value>;

/// Declared symbol extracted from a source file.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Symbol {
    pub name: String,
    /// class|function|method|property|variable|interface|enum|route|...
    pub kind: String,
    pub line_start: usize,
    pub line_end: usize,
    pub column_start: usize,
    pub column_end: usize,
    pub qualified_name: Option<String>,
    pub docstring: Option<String>,
    pub modifiers: Vec<String>,
    pub metadata: Option<Metadata>,
    /// Embedding-context columns; builders derive parent_scope/imports_summary when None.
    pub parameters: Option<String>,
    pub return_type: Option<String>,
    pub parent_scope: Option<String>,
    pub imports_summary: Option<String>,
    pub body: Option<String>,
    /// Parameter count when counted unambiguously; None = unknown.
    pub arity: Option<usize>,
}

/// Reference edge between symbols; targets stay name-based until resolution.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Edge {
    /// Name of the enclosing symbol that owns this edge.
    pub source_name: String,
    pub kind: String,
    /// Unresolved target name, resolved to a symbol_id by the builder.
    pub target_name: String,
    pub line: usize,
    pub column: usize,
    /// Bare receiver type; None makes the type-aware resolver abstain.
    pub receiver_type: Option<String>,
    /// Call-site argument count when counted; None = unknown.
    pub call_arity: Option<usize>,
    /// True for generic-tier edges whose target must stay name-based.
    pub generic_tier: bool,
}

/// Import statement binding.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Import {
    pub imported_path: String,
    pub line: usize,
    /// Local binding name when it differs from the imported name.
    pub local_alias: Option<String>,
}

/// NOTE/WHY/HACK marker comment.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct RationaleRecord {
    pub line: usize,
    /// note|why|hack
    pub kind: String,
    pub text: String,
}

/// Parser output for one source file.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ParsedFile {
    pub path: String,
    pub language: String,
    pub hash: String,
    pub line_count: usize,
    pub symbols: Vec<Symbol>,
    pub edges: Vec<Edge>,
    pub imports: Vec<Import>,
    pub rationale: Vec<RationaleRecord>,
}

/// Lowercase hex SHA-256 of `data` (the ParsedFile.hash contract).
pub fn sha256_hex(data: &[u8]) -> String {
    let digest = Sha256::digest(data);
    let mut hex = String::with_capacity(digest.len() * 2);
    for byte in digest {
        hex.push_str(&format!("{byte:02x}"));
    }
    hex
}
