"""Signal tests for the Objective-C parser.

Covers the parser-side signals objc's grammar exposes (FR-006/FR-007):

- ``Import.local_alias`` — objc headers are includes, not imports (F2.1:
  ``preproc_include`` has no alias field), so every include form carries
  ``local_alias=None``.
- ``Edge.call_arity`` / ``Symbol.arity`` — argument counts on
  ``message_expression`` selector segments and plain-C ``call_expression``
  argument_lists; parameter counts from ``method_parameter`` children, with
  variadic ``...`` degrading to None (D-005).
- ``Edge.receiver_type`` — the ``receiver`` field of a message expression
  (F2.2), typed via the scope-ordered tracker (method parameters + typed
  locals) or the capitalized class-name heuristic. ``self`` resolves to the
  enclosing class; ``super`` abstains (its superclass lives in the header,
  cross-file); shadows and nested message receivers abstain (FR-009).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from cairn.parsers.objc import ObjCParser


def _parse(source: bytes):
    """Parse ``source`` with ObjCParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=".m", delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return ObjCParser().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _calls(pf):
    return [e for e in pf.edges if e.kind == "calls"]


def _edge(pf, target):
    matches = [e for e in _calls(pf) if e.target_name == target]
    assert len(matches) == 1, f"expected exactly one '{target}' calls edge, got {matches}"
    return matches[0]


def _symbol(pf, name):
    matches = [s for s in pf.symbols if s.name == name]
    assert len(matches) == 1, f"expected exactly one '{name}' symbol, got {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# Import signals — includes carry no local binding (F2.1).
# ---------------------------------------------------------------------------

class TestImportSignals:
    def test_framework_include_has_no_alias(self):
        pf = _parse(b'#import <Foundation/Foundation.h>\n')
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "Foundation/Foundation.h"
        assert imp.local_alias is None

    def test_quoted_include_has_no_alias(self):
        # Nonexistent target: path falls back to the literal text.
        pf = _parse(b'#import "Helper.h"\n')
        assert len(pf.imports) == 1
        assert pf.imports[0].imported_path == "Helper.h"
        assert pf.imports[0].local_alias is None


# ---------------------------------------------------------------------------
# Edge.call_arity — selector arguments + plain-C argument_list.
# ---------------------------------------------------------------------------

class TestCallArity:
    def test_bare_selector_has_zero_arguments(self):
        pf = _parse(b"void f() { [obj method]; }\n")
        assert _edge(pf, "method").call_arity == 0

    def test_keyword_selector_counts_each_argument(self):
        pf = _parse(b"void f() { [obj method:1 label:2]; }\n")
        assert _edge(pf, "method").call_arity == 2

    def test_nested_message_argument_counts_once(self):
        pf = _parse(b"void f() { [obj method:[other inner]]; }\n")
        assert _edge(pf, "method").call_arity == 1

    def test_c_call_expression_arity(self):
        pf = _parse(b'void f() { NSLog(@"hi %d", 1); }\n')
        assert _edge(pf, "NSLog").call_arity == 2


# ---------------------------------------------------------------------------
# Edge.receiver_type — the message_expression `receiver` field (F2.2).
# ---------------------------------------------------------------------------

class TestReceiverType:
    def test_receiver_from_typed_local(self):
        pf = _parse(
            b"void f() {\n"
            b"  NSString *s = @\"x\";\n"
            b"  [s lowercaseString];\n"
            b"}\n"
        )
        assert _edge(pf, "lowercaseString").receiver_type == "NSString"

    def test_receiver_from_typed_parameter(self):
        pf = _parse(
            b"@implementation User\n"
            b"- (void)m:(User *)u {\n"
            b"  [u helper];\n"
            b"}\n"
            b"@end\n"
        )
        assert _edge(pf, "helper").receiver_type == "User"

    def test_capitalized_receiver_is_class_name(self):
        pf = _parse(b"void f() { [NSString string]; }\n")
        assert _edge(pf, "string").receiver_type == "NSString"

    def test_self_receiver_resolves_to_enclosing_class(self):
        pf = _parse(
            b"@implementation User\n"
            b"- (void)m {\n"
            b"  [self helper];\n"
            b"}\n"
            b"@end\n"
        )
        assert _edge(pf, "helper").receiver_type == "User"

    def test_super_receiver_abstains(self):
        # The superclass is declared in the header, not the grammar-visible
        # receiver -- FR-009 abstention.
        pf = _parse(
            b"@implementation User\n"
            b"- (void)m {\n"
            b"  [super helper];\n"
            b"}\n"
            b"@end\n"
        )
        assert _edge(pf, "helper").receiver_type is None

    def test_untracked_lowercase_receiver_abstains(self):
        pf = _parse(b"void f() { [obj method]; }\n")
        assert _edge(pf, "method").receiver_type is None

    def test_nested_message_receiver_abstains(self):
        pf = _parse(b"void f() { [[User alloc] init]; }\n")
        assert _edge(pf, "init").receiver_type is None

    def test_shadow_of_different_type_poisons(self):
        # D-006: an inner redeclaration under a different type makes the
        # binding ambiguous until the scope pops.
        pf = _parse(
            b"void f() {\n"
            b"  NSString *a = @\"x\";\n"
            b"  {\n"
            b"    User *a;\n"
            b"    [a helper];\n"
            b"  }\n"
            b"}\n"
        )
        assert _edge(pf, "helper").receiver_type is None


# ---------------------------------------------------------------------------
# Symbol.arity — method_parameter count, variadic abstains (D-005).
# ---------------------------------------------------------------------------

class TestDefinitionArity:
    def test_method_arities(self):
        pf = _parse(
            b"@implementation User\n"
            b"- (void)zero {\n}\n"
            b"- (instancetype)initWithId:(NSString *)id name:(NSInteger)n {\n}\n"
            b"@end\n"
        )
        assert _symbol(pf, "zero").arity == 0
        assert _symbol(pf, "initWithId").arity == 2

    def test_variadic_method_abstains(self):
        pf = _parse(
            b"@implementation User\n"
            b"- (void)log:(NSString *)fmt, ... {\n}\n"
            b"@end\n"
        )
        assert _symbol(pf, "log").arity is None

    def test_interface_stub_arity(self):
        pf = _parse(
            b"@interface User\n"
            b"- (void)doIt:(NSString *)name withOpt:(NSInteger)n;\n"
            b"@end\n"
        )
        assert _symbol(pf, "doIt").arity == 2
