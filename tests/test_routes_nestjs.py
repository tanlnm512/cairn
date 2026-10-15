"""NestJS route detection from controller decorators (URL -> code)."""
from __future__ import annotations

from cairn.parsers.base import ParsedFile, Symbol
from cairn.parsers.routes import detect_routes


def _controller_pf(method_modifiers) -> ParsedFile:
    """A parsed @Controller class owning one method with the given decorators."""
    return ParsedFile(
        path="src/users/users.controller.ts",
        language="typescript",
        hash="0" * 64,
        line_count=10,
        symbols=[
            Symbol(
                name="UsersController",
                kind="class",
                line_start=2,
                line_end=10,
                qualified_name="users.controller.UsersController",
                modifiers=["@Controller('users')"],
            ),
            Symbol(
                name="handler",
                kind="method",
                line_start=5,
                line_end=7,
                qualified_name="users.controller.UsersController.handler",
                modifiers=list(method_modifiers),
            ),
        ],
    )


def test_stacked_http_method_decorators_emit_one_route_each():
    """Every stacked @Get/@Post decorator on a handler yields its own route;
    only the first must not win."""
    extraction = detect_routes(
        _controller_pf(["@Get(':id')", "@Post()"]), "typescript"
    )

    assert extraction is not None
    routes = {(r.metadata["http_method"], r.metadata["path"]) for r in extraction.routes}
    assert routes == {("GET", "/users/:id"), ("POST", "/users")}
    assert [e.target_name for e in extraction.references] == ["handler", "handler"]


def test_single_decorator_emits_one_route():
    extraction = detect_routes(_controller_pf(["@Get('list')"]), "typescript")

    assert extraction is not None
    assert len(extraction.routes) == 1
    route = extraction.routes[0]
    assert route.metadata["http_method"] == "GET"
    assert route.metadata["path"] == "/users/list"
    assert route.metadata["provenance"] == "exact"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
