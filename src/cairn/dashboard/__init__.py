"""Read-only local web dashboard over the graph DB.

The server stack (starlette / uvicorn / jinja2 — transitive deps of mcp) is
imported lazily inside :func:`cairn.dashboard.app.create_app` /
:data.py consumers, so importing this package never loads it: core CLI
paths must not pay the import cost or break if a future mcp drop changes
its transitive footprint.
"""
