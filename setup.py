"""Build the vendored fwcd Kotlin grammar (vendor/tree-sitter-kotlin) as an
in-tree cp310-abi3 extension inside the cairn package."""

from setuptools import Extension, setup

setup(
    ext_modules=[
        Extension(
            "cairn._tree_sitter_kotlin",
            sources=[
                "vendor/tree-sitter-kotlin/src/parser.c",
                "vendor/tree-sitter-kotlin/src/scanner.c",
                "src/cairn/_tree_sitter_kotlin_binding.c",
            ],
            include_dirs=["vendor/tree-sitter-kotlin/src"],
            define_macros=[
                ("Py_LIMITED_API", "0x030A0000"),
                ("PY_SSIZE_T_CLEAN", None),
            ],
            py_limited_api=True,
        ),
    ],
    options={"bdist_wheel": {"py_limited_api": "cp310"}},
)
