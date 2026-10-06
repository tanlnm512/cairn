# Test Cases: grade-a-ratchet

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Fixture and invocation contract (every command is self-contained, restores
whatever it temporarily moves or stages, and finishes well under the 120 s
audit cap when run from the repo root):
- Cases that must mutate state do so against the real audit documents or the
  real source tree, keep a backup, and restore it in a finally block — a
  failed or killed run never leaves the fixture behind.
- The comment-style checker is invoked through the entry point its pre-commit
  hook declares, discovered from the hook config at run time; the audit-status
  JSON mode is invoked the way the make target itself runs it, discovered
  from a dry run. No case hardcodes a script path or flag spelling beyond
  what the spec names.
- Probe files are staged intent-to-add so both filesystem-walking and
  git-listing checkers see them, then unstaged and deleted.

## TC-001 — Root artifacts land in their documented homes
- **Story**: US3 · **Traces to**: FR-001 (AC1)
- **Given** the repository still tracks the diagram page and the audit report at the root
- **When** the relocation change is applied
- **Then** both artifacts are tracked at their new locations with their content intact, and nothing remains at the root
- **Pass condition**: `git ls-files --error-unmatch docs/diagrams/architecture.html docs/audits/2026-10-02.md && test -s docs/diagrams/architecture.html && test -s docs/audits/2026-10-02.md && test ! -e architecture.html && test ! -e audit-findings-2026-10-02.md`

## TC-002 — Nothing keeps linking at the old root locations
- **Story**: US3 · **Traces to**: FR-001 (AC1)
- **Given** the artifacts have moved under the docs tree
- **When** every live reference in the repository is searched for the old root locations
- **Then** no live link points at the old locations — only historical changelog entries and the spec's own working notes may mention the old names
- **Pass condition**: `git grep -nE "(^|[^-/[:alnum:]])architecture\.html|audit-findings-2026-10-02" -- ":!CHANGELOG.md" ":!specs" ":!docs/diagrams" ":!docs/audits" > /tmp/qa-old-links.out; test ! -s /tmp/qa-old-links.out`

## TC-003 — Standing guard: the repo root stays artifact-free
- **Story**: US3 · **Traces to**: FR-001
- **Given** the relocation has landed and the root holds only standard project entries
- **When** any later change tracks a diagram or audit artifact at the repo root again
- **Then** the guard fails, so root clutter cannot creep back unnoticed
- **Pass condition**: `git ls-files | grep -v / > /tmp/qa-root.out; grep -Ei "\.html$|audit-findings" /tmp/qa-root.out > /tmp/qa-root-bad.out; test ! -s /tmp/qa-root-bad.out`

## TC-004 — Audit debt prints as remaining-vs-total for every priority
- **Story**: US1 · **Traces to**: FR-002 (AC1)
- **Given** the relocated audit report and its status sidecar are in place
- **When** the maintainer runs the audit-status command
- **Then** it exits 0 and prints one remaining-vs-total line per priority P0 through P3, each total matching the counts declared by the audit report (P0 2, P1 51, P2 88, P3 3) and no remaining count exceeding its total
- **Pass condition**: `python3 -c 'exec("""import re, subprocess, sys\np = subprocess.run(["make", "audit-status"], capture_output=True, text=True)\nout = p.stdout + p.stderr\nok = p.returncode == 0\nfor k, v in {"P0": 2, "P1": 51, "P2": 88, "P3": 3}.items():\n    ints = []\n    for line in out.splitlines():\n        if k in line:\n            ints += [int(x) for x in re.findall("[0-9]+", line.replace(k, ""))]\n    ok = ok and v in ints and (len(ints) < 2 or ints[0] <= ints[-1])\nsys.exit(0 if ok else 1)""")'`

## TC-005 — Marking a finding fixed burns down the remaining count
- **Story**: US1 · **Traces to**: FR-002 (AC1)
- **Given** the audit report and sidecar are in place and the current per-priority remaining-vs-total counts are recorded
- **When** one finding is marked fixed in the status sidecar using its documented machine-readable format, changing nothing else
- **Then** re-running audit-status keeps every total unchanged and lowers the remaining count of that finding's priority by exactly one
- **Pass condition**: `Human observation: edit one finding's fixed status in the sidecar with the format the shipped sidecar already uses, re-run the audit-status command, and compare the two outputs — totals identical, one priority's remaining count down by exactly one, exit 0 both times; restore the sidecar afterwards.`

## TC-006 — Audit debt is consumable as JSON
- **Story**: US1 · **Traces to**: FR-002, FR-004, NFR-005 (AC1)
- **Given** the audit report and sidecar are in place
- **When** the audit-status tool is invoked in its JSON mode
- **Then** it exits 0, the output parses as JSON, and the payload carries all four priorities with their remaining and total counts
- **Pass condition**: `python3 -c 'exec("""import json, subprocess, sys\ndry = subprocess.run(["make", "-n", "audit-status"], capture_output=True, text=True)\nrecipe = [l.strip().lstrip("@-").strip() for l in dry.stdout.splitlines()]\nrecipe = [c for c in recipe if c and not c.startswith("make")]\ntries = [c + " --json" for c in recipe] + ["make audit-status ARGS=--json", "make audit-status JSON=1"]\nok = False\nfor t in tries:\n    r = subprocess.run(t, shell=True, capture_output=True, text=True)\n    try:\n        d = json.loads(r.stdout)\n    except Exception:\n        continue\n    if r.returncode == 0 and all(p in json.dumps(d) for p in ("P0", "P1", "P2", "P3")):\n        ok = True\n        break\nsys.exit(0 if ok else 1)""")'`

## TC-007 — A missing audit document or sidecar fails cleanly, naming the file
- **Story**: US1 · **Traces to**: FR-002 (AC2)
- **Given** the audit report and sidecar are in place
- **When** first the audit document, then the status sidecar, is temporarily moved away and the audit-status command is run each time
- **Then** each run fails with a non-zero exit and an error that names the missing file; both files are restored afterwards
- **Pass condition**: `python3 -c 'exec("""import shutil, subprocess, sys\nfrom pathlib import Path\nbase = Path("docs/audits")\nmds = sorted(base.glob("*.md"))\nsds = sorted(base.glob("*.status.json"))\nif not mds or not sds:\n    sys.exit(1)\ntmp = Path("/tmp/qa-audit-missing")\nshutil.rmtree(tmp, ignore_errors=True)\ntmp.mkdir(parents=True)\nok = True\ntry:\n    for f in (mds[0], sds[0]):\n        bak = tmp / f.name\n        shutil.move(str(f), str(bak))\n        r = subprocess.run(["make", "audit-status"], capture_output=True, text=True)\n        ok = ok and r.returncode != 0 and f.name in (r.stdout + r.stderr)\n        if not f.exists():\n            shutil.move(str(bak), str(f))\nfinally:\n    for f in (mds[0], sds[0]):\n        bak = tmp / f.name\n        if not f.exists() and bak.exists():\n            shutil.move(str(bak), str(f))\nsys.exit(0 if ok else 1)""")'`

## TC-008 — An unparseable sidecar fails cleanly, naming the file
- **Story**: US1 · **Traces to**: FR-002 (AC2)
- **Given** the status sidecar is in place
- **When** its content is temporarily replaced with bytes that are not valid machine-readable data and the audit-status command is run
- **Then** the run fails with a non-zero exit and an error naming the corrupted sidecar; the original content is restored afterwards
- **Pass condition**: `python3 -c 'exec("""import subprocess, sys\nfrom pathlib import Path\nsds = sorted(Path("docs/audits").glob("*.status.json"))\nif not sds:\n    sys.exit(1)\nf = sds[0]\ndata = f.read_bytes()\ntry:\n    f.write_bytes(b"not-json{")\n    r = subprocess.run(["make", "audit-status"], capture_output=True, text=True)\n    ok = r.returncode != 0 and f.name in (r.stdout + r.stderr)\nfinally:\n    f.write_bytes(data)\nsys.exit(0 if ok else 1)""")'`

## TC-009 — The style gate is wired into local and CI review
- **Story**: US2 · **Traces to**: FR-003
- **Given** the checker exists as a script
- **When** the repository's hook configuration and continuous-integration configuration are inspected for a comment-style gate
- **Then** both surfaces reference the gate, so a contributor cannot bypass it locally or in CI
- **Pass condition**: `grep -E "^[[:space:]]*-[[:space:]]*id:.*(comment|docstring|style|ratchet)" .pre-commit-config.yaml && grep -qiE "comment[-_ ]?style|comment[-_ ]?length|docstring|ratchet" .github/workflows/ci.yml`

## TC-010 — An unchanged tree passes and shows its remaining violation count
- **Story**: US2 · **Traces to**: FR-003 (AC1)
- **Given** the committed baseline allowlist matches the current tree
- **When** the checker runs over the repository
- **Then** it exits 0 and prints the number of grandfathered violations still remaining
- **Pass condition**: `python3 -c 'exec("""import os, re, subprocess, sys\nfrom pathlib import Path\ncfg = Path(".pre-commit-config.yaml").read_text()\nblocks = [b for b in re.split("(?m)^[ \\t]*-[ ]+id:[ ]*", cfg)[1:] if re.search("comment|docstring|ratchet|style", b, re.I)]\nentries = [m.group(1).strip().strip(chr(34)).strip(chr(39)) for m in (re.search("(?m)^[ \\t]*entry:[ \\t]*(.+?)[ \\t]*$", b) for b in blocks) if m]\ncmd = (entries or [""])[0]\nif not cmd:\n    sys.exit(1)\nenv = dict(os.environ, PATH=str(Path(".venv/bin").resolve()) + os.pathsep + os.environ["PATH"])\nrun = lambda c: subprocess.run(c, shell=True, capture_output=True, text=True, env=env)\n\nr = run(cmd)\nout = r.stdout + r.stderr\nsys.exit(0 if r.returncode == 0 and re.search("[0-9]", out) else 1)""")'`

## TC-011 — The length limit is exactly three lines
- **Story**: US2 · **Traces to**: FR-003 (AC2)
- **Given** the committed baseline is unchanged
- **When** a probe file holding a three-line comment block and a one-line docstring is added, then replaced by a probe holding a four-line comment block and a four-line docstring
- **Then** the three-line probe passes, while the four-line probe fails with the offending file and block named; both probes are removed afterwards
- **Pass condition**: `python3 -c 'exec("""import os, re, subprocess, sys\nfrom pathlib import Path\ncfg = Path(".pre-commit-config.yaml").read_text()\nblocks = [b for b in re.split("(?m)^[ \\t]*-[ ]+id:[ ]*", cfg)[1:] if re.search("comment|docstring|ratchet|style", b, re.I)]\nentries = [m.group(1).strip().strip(chr(34)).strip(chr(39)) for m in (re.search("(?m)^[ \\t]*entry:[ \\t]*(.+?)[ \\t]*$", b) for b in blocks) if m]\ncmd = (entries or [""])[0]\nif not cmd:\n    sys.exit(1)\nenv = dict(os.environ, PATH=str(Path(".venv/bin").resolve()) + os.pathsep + os.environ["PATH"])\nrun = lambda c: subprocess.run(c, shell=True, capture_output=True, text=True, env=env)\n\nokf = Path("src/cairn/_qa_style_ok.py")\nbadf = Path("src/cairn/_qa_style_bad.py")\nQ = chr(34) * 3\noksrc = chr(10).join(["# one", "# two", "# three", "def ok_probe():", chr(9) + Q + "One line." + Q, chr(9) + "return 1"])\nbadsrc = chr(10).join(["# one", "# two", "# three", "# four", "def bad_probe():", Q, "one", "two", "three", "four", Q, "    return 1"])\ndef stage(p, text):\n    p.write_text(text)\n    subprocess.run(["git", "add", "-N", str(p)], check=True, capture_output=True)\ndef unstage(p):\n    subprocess.run(["git", "reset", "-q", "--", str(p)], capture_output=True)\n    p.unlink(missing_ok=True)\ntry:\n    stage(okf, oksrc)\n    r1 = run(cmd)\n    unstage(okf)\n    stage(badf, badsrc)\n    r2 = run(cmd)\nfinally:\n    unstage(okf)\n    unstage(badf)\nsys.exit(0 if r1.returncode == 0 and r2.returncode != 0 and "_qa_style_bad" in (r2.stdout + r2.stderr) else 1)""")'`

## TC-012 — The gate scopes to the source tree only
- **Story**: US2 · **Traces to**: FR-003
- **Given** the committed baseline is unchanged
- **When** a probe file holding a four-line comment block is added outside the source tree, where this spec's scope explicitly ends
- **Then** the checker still passes, proving the gate does not silently widen to files it promised not to judge; the probe is removed afterwards
- **Pass condition**: `python3 -c 'exec("""import os, re, subprocess, sys\nfrom pathlib import Path\ncfg = Path(".pre-commit-config.yaml").read_text()\nblocks = [b for b in re.split("(?m)^[ \\t]*-[ ]+id:[ ]*", cfg)[1:] if re.search("comment|docstring|ratchet|style", b, re.I)]\nentries = [m.group(1).strip().strip(chr(34)).strip(chr(39)) for m in (re.search("(?m)^[ \\t]*entry:[ \\t]*(.+?)[ \\t]*$", b) for b in blocks) if m]\ncmd = (entries or [""])[0]\nif not cmd:\n    sys.exit(1)\nenv = dict(os.environ, PATH=str(Path(".venv/bin").resolve()) + os.pathsep + os.environ["PATH"])\nrun = lambda c: subprocess.run(c, shell=True, capture_output=True, text=True, env=env)\n\np = Path("tests/_qa_style_outside.py")\nsrc = chr(10).join(["# one", "# two", "# three", "# four", "outside = 1"])\ntry:\n    p.write_text(src)\n    subprocess.run(["git", "add", "-N", str(p)], check=True, capture_output=True)\n    r = run(cmd)\nfinally:\n    subprocess.run(["git", "reset", "-q", "--", str(p)], capture_output=True)\n    p.unlink(missing_ok=True)\nsys.exit(0 if r.returncode == 0 else 1)""")'`

## TC-013 — A stale allowlist entry blocks the run until removed
- **Story**: US2 · **Traces to**: FR-003 (AC3)
- **Given** a source file carrying several grandfathered long blocks
- **When** its content is temporarily replaced by a stub, so its baseline entries no longer correspond to any real violation, and the checker runs
- **Then** the run fails reporting the stale entries against that file, so the allowlist can only ever shrink honestly; the file's original content is restored afterwards
- **Pass condition**: `python3 -c 'exec("""import os, re, subprocess, sys\nfrom pathlib import Path\ncfg = Path(".pre-commit-config.yaml").read_text()\nblocks = [b for b in re.split("(?m)^[ \\t]*-[ ]+id:[ ]*", cfg)[1:] if re.search("comment|docstring|ratchet|style", b, re.I)]\nentries = [m.group(1).strip().strip(chr(34)).strip(chr(39)) for m in (re.search("(?m)^[ \\t]*entry:[ \\t]*(.+?)[ \\t]*$", b) for b in blocks) if m]\ncmd = (entries or [""])[0]\nif not cmd:\n    sys.exit(1)\nenv = dict(os.environ, PATH=str(Path(".venv/bin").resolve()) + os.pathsep + os.environ["PATH"])\nrun = lambda c: subprocess.run(c, shell=True, capture_output=True, text=True, env=env)\n\ntarget = Path("src/cairn/graph/incremental.py")\ndata = target.read_bytes()\ntry:\n    target.write_bytes(b"stub = 1\\n")\n    r = run(cmd)\nfinally:\n    target.write_bytes(data)\nsys.exit(0 if r.returncode != 0 and "incremental.py" in (r.stdout + r.stderr) else 1)""")'`

## TC-014 — The style gate's count is consumable as JSON
- **Story**: US2 · **Traces to**: FR-003, FR-004, NFR-005
- **Given** the committed baseline matches the current tree
- **When** the checker is invoked in its JSON mode
- **Then** it exits 0, the output parses as JSON, and the payload carries the remaining violation count as a number
- **Pass condition**: `python3 -c 'exec("""import os, re, subprocess, sys\nfrom pathlib import Path\ncfg = Path(".pre-commit-config.yaml").read_text()\nblocks = [b for b in re.split("(?m)^[ \\t]*-[ ]+id:[ ]*", cfg)[1:] if re.search("comment|docstring|ratchet|style", b, re.I)]\nentries = [m.group(1).strip().strip(chr(34)).strip(chr(39)) for m in (re.search("(?m)^[ \\t]*entry:[ \\t]*(.+?)[ \\t]*$", b) for b in blocks) if m]\ncmd = (entries or [""])[0]\nif not cmd:\n    sys.exit(1)\nenv = dict(os.environ, PATH=str(Path(".venv/bin").resolve()) + os.pathsep + os.environ["PATH"])\nrun = lambda c: subprocess.run(c, shell=True, capture_output=True, text=True, env=env)\n\nimport json\nr = run(cmd + " --json")\nok = False\ntry:\n    d = json.loads(r.stdout)\n    ok = r.returncode == 0 and re.search("[0-9]", json.dumps(d))\nexcept Exception:\n    ok = False\nsys.exit(0 if ok else 1)""")'`

## TC-015 — The system report carries both gates' numbers
- **Story**: US1 · **Traces to**: FR-004
- **Given** both gates are green on the current tree and their current numbers are known
- **When** the repository's system report is generated in its JSON mode
- **Then** the report payload contains the audit priorities with their counts and the style gate's remaining violation count, so an agent can read both ratchets without running them
- **Pass condition**: `python3 -c 'exec("""import os, re, subprocess, sys\nfrom pathlib import Path\ncfg = Path(".pre-commit-config.yaml").read_text()\nblocks = [b for b in re.split("(?m)^[ \\t]*-[ ]+id:[ ]*", cfg)[1:] if re.search("comment|docstring|ratchet|style", b, re.I)]\nentries = [m.group(1).strip().strip(chr(34)).strip(chr(39)) for m in (re.search("(?m)^[ \\t]*entry:[ \\t]*(.+?)[ \\t]*$", b) for b in blocks) if m]\ncmd = (entries or [""])[0]\nif not cmd:\n    sys.exit(1)\nenv = dict(os.environ, PATH=str(Path(".venv/bin").resolve()) + os.pathsep + os.environ["PATH"])\nrun = lambda c: subprocess.run(c, shell=True, capture_output=True, text=True, env=env)\n\nimport json\nr = run(cmd)\nrep = subprocess.run([".venv/bin/cairn", "report", "--json"], capture_output=True, text=True)\nints = [int(x) for x in re.findall("[0-9]+", r.stdout + r.stderr)]\nbig = [i for i in ints if i >= 10] or ints\ntry:\n    s = json.dumps(json.loads(rep.stdout))\nexcept Exception:\n    sys.exit(1)\nok = r.returncode == 0 and rep.returncode == 0 and all(p in s for p in ("P0", "P1", "P2", "P3")) and any(str(i) in s for i in big)\nsys.exit(0 if ok else 1)""")'`

## TC-016 — Both gates answer in under five seconds
- **Story**: US1 · **Traces to**: NFR-003 (FR-002, FR-003)
- **Given** the repository at its current size
- **When** each gate is timed end to end
- **Then** audit-status and the comment-style checker each finish in under five seconds and exit 0
- **Pass condition**: `/usr/bin/time -p make audit-status > /tmp/qa-perf-audit.out 2>&1 && awk '/^real/ { if ($2 + 0 >= 5) exit 1 }' /tmp/qa-perf-audit.out && python3 -c 'exec("""import os, re, subprocess, sys\nfrom pathlib import Path\ncfg = Path(".pre-commit-config.yaml").read_text()\nblocks = [b for b in re.split("(?m)^[ \\t]*-[ ]+id:[ ]*", cfg)[1:] if re.search("comment|docstring|ratchet|style", b, re.I)]\nentries = [m.group(1).strip().strip(chr(34)).strip(chr(39)) for m in (re.search("(?m)^[ \\t]*entry:[ \\t]*(.+?)[ \\t]*$", b) for b in blocks) if m]\ncmd = (entries or [""])[0]\nif not cmd:\n    sys.exit(1)\nenv = dict(os.environ, PATH=str(Path(".venv/bin").resolve()) + os.pathsep + os.environ["PATH"])\nrun = lambda c: subprocess.run(c, shell=True, capture_output=True, text=True, env=env)\n\nimport time\nt = time.monotonic()\nr = run(cmd)\ndt = time.monotonic() - t\nsys.exit(0 if r.returncode == 0 and dt < 5 else 1)""")'`

## TC-017 — The style gate is deterministic
- **Story**: US1 · **Traces to**: NFR-004 (FR-003)
- **Given** an unchanged tree and an unchanged baseline
- **When** the comment-style checker runs twice in a row
- **Then** both runs exit 0 with byte-identical output
- **Pass condition**: `python3 -c 'exec("""import os, re, subprocess, sys\nfrom pathlib import Path\ncfg = Path(".pre-commit-config.yaml").read_text()\nblocks = [b for b in re.split("(?m)^[ \\t]*-[ ]+id:[ ]*", cfg)[1:] if re.search("comment|docstring|ratchet|style", b, re.I)]\nentries = [m.group(1).strip().strip(chr(34)).strip(chr(39)) for m in (re.search("(?m)^[ \\t]*entry:[ \\t]*(.+?)[ \\t]*$", b) for b in blocks) if m]\ncmd = (entries or [""])[0]\nif not cmd:\n    sys.exit(1)\nenv = dict(os.environ, PATH=str(Path(".venv/bin").resolve()) + os.pathsep + os.environ["PATH"])\nrun = lambda c: subprocess.run(c, shell=True, capture_output=True, text=True, env=env)\n\na = run(cmd)\nb = run(cmd)\nsame = (a.returncode, a.stdout, a.stderr) == (b.returncode, b.stdout, b.stderr)\nsys.exit(0 if same and a.returncode == 0 else 1)""")'`

## TC-018 — Baseline regeneration is idempotent
- **Story**: US1 · **Traces to**: NFR-004 (FR-003)
- **Given** a committed baseline generated from the current tree
- **When** the baseline is regenerated twice without touching any source file
- **Then** the allowlist content is identical after each regeneration and the checker still exits 0 — regenerating never invents or loses entries on a repeat
- **Pass condition**: `Human observation: regenerate the machine-generated allowlist twice using the documented regeneration entry point, compare the two results byte for byte (they must be identical and match the committed baseline), then run the checker and confirm it still exits 0; leave the tree unchanged.`

## TC-019 — Standing guard: the gate wiring adds no credentials
- **Story**: US1 · **Traces to**: NFR-001
- **Given** the audit-status tooling, style gate, make targets, and review wiring have landed
- **When** the surfaces this change owns are scanned for hardcoded credential-shaped assignments
- **Then** nothing is found, so the no-secrets scope of this change cannot silently widen
- **Pass condition**: `grep -rnEi "(api[_-]?key|password|secret|token)[[:space:]]*=" scripts Makefile > /tmp/qa-secrets.out; test ! -s /tmp/qa-secrets.out`

## TC-020 — Standing guard: the gates make no network calls
- **Story**: US1 · **Traces to**: NFR-002
- **Given** the audit-status tooling and style gate have landed
- **When** the tooling surfaces are scanned for network-client imports
- **Then** none are present, so no repository data can leave the machine through either gate
- **Pass condition**: `grep -rnE "^[[:space:]]*(import|from) (requests|httpx|urllib|socket|aiohttp|ftplib|smtplib)" scripts Makefile > /tmp/qa-egress.out; test ! -s /tmp/qa-egress.out`

## TC-021 — Standing guard: the gates stay command-line tools
- **Story**: US1 · **Traces to**: NFR-006
- **Given** the audit-status tooling and style gate have landed
- **When** the tooling surfaces are scanned for user-interface markup
- **Then** none are present, so the change cannot introduce an accessibility surface it never promised to own
- **Pass condition**: `grep -rnE "<html|<div|<button|<canvas" scripts Makefile > /tmp/qa-ui.out; test ! -s /tmp/qa-ui.out`

## Coverage matrix
<!-- Every FR and applicable NFR appears; `check.py` fails a requirement with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003 | auto |
| FR-002      | TC-004, TC-005, TC-006, TC-007, TC-008 | auto (TC-005 manual) |
| FR-003      | TC-009, TC-010, TC-011, TC-012, TC-013, TC-014 | auto |
| FR-004      | TC-015, TC-006, TC-014 | auto |
| NFR-001     | TC-019 | auto |
| NFR-002     | TC-020 | auto |
| NFR-003     | TC-016 | auto |
| NFR-004     | TC-017, TC-018 | auto (TC-018 manual) |
| NFR-005     | TC-006, TC-014 | auto |
| NFR-006     | TC-021 | auto |
