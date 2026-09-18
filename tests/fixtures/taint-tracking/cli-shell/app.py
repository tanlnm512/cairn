"""Fixture: CLI-argument flow into shell execution.

main reads a CLI argument via the ``input`` call and passes it to
run_command, which executes it in a shell via the ``sh.system`` call tail.
"""


def main(sh):
    command = input()
    return run_command(command, sh)


def run_command(command, sh):
    return sh.system(command)
