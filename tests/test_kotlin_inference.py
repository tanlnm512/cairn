from cairn.parsers.base import ParsedFile, Symbol, Edge
from cairn.parsers.inference.kotlin import kotlin_receiver_types


def test_kotlin_inference_pass():
    pf = ParsedFile(
        path="User.kt",
        language="kotlin",
        hash="h",
        line_count=10,
        symbols=[
            Symbol(name="User", kind="class", line_start=1, line_end=10),
            Symbol(name="apiFactory", kind="property", line_start=2, line_end=2),
        ],
        edges=[
            Edge(source_name="User", kind="calls", target_name="apiFactory.create", line=5),
            Edge(source_name="User", kind="calls", target_name="this.process", line=6),
        ],
    )

    processed = kotlin_receiver_types(pf)
    assert processed.edges[1].receiver_type == "User"
