"""Fixture: ten micro-flows covering the default source/sink categories.

Each of the five default source categories flows into a shell sink, and
the HTTP-parameter source flows into each of the five default sinks.
Source and sink matching rides the recorded call tails (``urlopen``,
``input``, ``getenv``, ``read_text``, ``json``, ``system``, ``execute``,
``write``, ``send``, ``eval``).
"""


def http_shell_entry(urlopen, sh):
    payload = urlopen("/matrix/http")
    return http_shell_sink(payload, sh)


def http_shell_sink(payload, sh):
    return sh.system(payload)


def cli_shell_entry(sh):
    payload = input()
    return cli_shell_sink(payload, sh)


def cli_shell_sink(payload, sh):
    return sh.system(payload)


def env_shell_entry(env, sh):
    payload = env.getenv("MATRIX_INPUT")
    return env_shell_sink(payload, sh)


def env_shell_sink(payload, sh):
    return sh.system(payload)


def file_read_shell_entry(handle, sh):
    payload = handle.read_text()
    return file_read_shell_sink(payload, sh)


def file_read_shell_sink(payload, sh):
    return sh.system(payload)


def api_shell_entry(response, sh):
    payload = response.json()
    return api_shell_sink(payload, sh)


def api_shell_sink(payload, sh):
    return sh.system(payload)


def http_sql_entry(urlopen, db):
    payload = urlopen("/matrix/http")
    return http_sql_sink(payload, db)


def http_sql_sink(payload, db):
    return db.execute(payload)


def http_shell_entry_b(urlopen, sh):
    payload = urlopen("/matrix/http")
    return http_shell_sink_b(payload, sh)


def http_shell_sink_b(payload, sh):
    return sh.system(payload)


def http_write_entry(urlopen, out):
    payload = urlopen("/matrix/http")
    return http_write_sink(payload, out)


def http_write_sink(payload, out):
    return out.write(payload)


def http_send_entry(urlopen, sock):
    payload = urlopen("/matrix/http")
    return http_send_sink(payload, sock)


def http_send_sink(payload, sock):
    return sock.send(payload)


def http_eval_entry(urlopen):
    payload = urlopen("/matrix/http")
    return http_eval_sink(payload)


def http_eval_sink(payload):
    return eval(payload)
