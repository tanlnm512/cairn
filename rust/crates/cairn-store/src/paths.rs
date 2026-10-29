//! Path helpers shared across crates.

/// Canonicalize-style paths with the Windows verbatim (`\\?\`) prefix removed,
/// matching plain `Path.resolve()`-style paths.
pub fn strip_verbatim(path: std::path::PathBuf) -> std::path::PathBuf {
    #[cfg(windows)]
    if let Some(s) = path.as_os_str().to_str() {
        if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
            return std::path::PathBuf::from(format!(r"\\{rest}"));
        }
        if let Some(rest) = s.strip_prefix(r"\\?\") {
            return std::path::PathBuf::from(rest);
        }
    }
    path
}
