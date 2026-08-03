import tempfile
from pathlib import Path

from codegraph.graph.scanner import detect_header_language, EXTENSION_MAP


def test_extension_map_includes_headers():
    assert ".h" in EXTENSION_MAP
    assert ".hpp" in EXTENSION_MAP
    assert ".cpp" in EXTENSION_MAP
    assert ".c" in EXTENSION_MAP


def test_detect_header_language():
    with tempfile.TemporaryDirectory() as tmpdir:
        objc_h = Path(tmpdir) / "User.h"
        objc_h.write_text("@interface User : NSObject\n@end\n", encoding="utf-8")
        assert detect_header_language(str(objc_h)) == "objc"

        cpp_h = Path(tmpdir) / "Entity.h"
        cpp_h.write_text("namespace core {\nclass Entity {};\n}\n", encoding="utf-8")
        assert detect_header_language(str(cpp_h)) == "cpp"

        c_h = Path(tmpdir) / "util.h"
        c_h.write_text("#ifndef UTIL_H\n#define UTIL_H\nvoid do_work(void);\n#endif\n", encoding="utf-8")
        assert detect_header_language(str(c_h)) == "c"
