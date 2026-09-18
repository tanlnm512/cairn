"""Fixture: same shape as sql-flow with the terminal function format_report.

No function calls any sink name, so the parameter reaches no sink and no
path is reported, default or fuzzy.
"""


def handle_request(urlopen, title):
    raw = urlopen("/reports/current")
    return validate_input(raw, title)


def validate_input(value, title):
    return format_report(value, title)


def format_report(value, title):
    return title + ": " + value
