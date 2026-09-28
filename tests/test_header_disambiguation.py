import tempfile
from pathlib import Path

from cairn.graph.scanner import (
    iter_files_and_skips,
    resolve_file_language,
)


def test_resolve_file_language_routes_header():
    """`.h` must resolve to a real parser language, never the 'header' sentinel."""
    with tempfile.TemporaryDirectory() as tmpdir:
        objc_h = Path(tmpdir) / "User.h"
        objc_h.write_text("@interface User : NSObject\n@end\n", encoding="utf-8")
        assert resolve_file_language(".h", str(objc_h)) == "objc"

        cpp_h = Path(tmpdir) / "Entity.h"
        cpp_h.write_text("namespace core {\nclass Entity {};\n}\n", encoding="utf-8")
        assert resolve_file_language(".h", str(cpp_h)) == "cpp"

        c_h = Path(tmpdir) / "util.h"
        c_h.write_text("#ifndef UTIL_H\n#define UTIL_H\nvoid do_work(void);\n#endif\n", encoding="utf-8")
        assert resolve_file_language(".h", str(c_h)) == "c"


def test_resolve_file_language_passes_through_non_header():
    """Non-header extensions are returned unchanged from EXTENSION_MAP."""
    with tempfile.TemporaryDirectory() as tmpdir:
        swift_f = Path(tmpdir) / "App.swift"
        swift_f.write_text("struct App {}", encoding="utf-8")
        assert resolve_file_language(".swift", str(swift_f)) == "swift"

        m_f = Path(tmpdir) / "App.m"
        m_f.write_text("@implementation App\n@end", encoding="utf-8")
        assert resolve_file_language(".m", str(m_f)) == "objc"


def test_scan_assigns_resolved_header_language():
    """End-to-end: a scanned .h file carries the sniffed language, not 'header'.

    Regression for the bug where FileInfo.language was built straight from
    EXTENSION_MAP, leaving .h files tagged 'header' with no parser.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir) / "demo"
        repo.mkdir()
        (repo / ".git").mkdir()

        (repo / "Widget.h").write_text(
            "@interface Widget : NSObject\n@property NSString *title;\n@end\n",
            encoding="utf-8",
        )

        files, skips = iter_files_and_skips(repo)
        assert len(files) == 1
        assert not skips
        assert files[0].rel_path == "Widget.h"
        assert files[0].language == "objc"

