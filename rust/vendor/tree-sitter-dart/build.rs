fn main() {
    cc::Build::new()
        .file("src/parser.c")
        .file("src/scanner.c")
        .include("src")
        .warnings(false)
        .compile("tree_sitter_dart");
}
