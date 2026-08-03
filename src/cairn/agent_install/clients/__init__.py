"""Per-client installer modules.

Each module owns one client's config schema (generator) + install + uninstall
branch, so the three sites that must agree for a given client live together
in one place. No client module may import a sibling client.
"""
