# Research: swe-bench-integration

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
<!-- External grounding for tech decisions: every claim below carries a source
     URL/DOI — no unsourced "it is known that". The tech agent consumes this
     file when choosing options in tech-spec.md. -->

## Questions

### Q1 — What is the canonical SWE-bench data source, schema, and split sizes, and how is it versioned?
- **source**: https://huggingface.co/datasets/princeton-nlp/SWE-bench/raw/main/README.md · **claim**: The full dataset card documents 12 string-typed fields — `instance_id`, `patch`, `repo`, `base_commit`, `hints_text`, `created_at`, `test_patch`, `problem_statement`, `version`, `environment_setup_commit`, `FAIL_TO_PASS`, `PASS_TO_PASS` — with `test` = 2,294 examples, `dev` = 225, `train` = 19,008, and no dataset-level version identifier anywhere in the card (the per-row `version` column is "Installation version to use for running evaluation", not a dataset revision). · **relevance**: Q1/FR-001/FR-002 — the loader's input contract and the pinning gap. · **confidence**: high
- **source**: https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite/raw/main/README.md · **claim**: SWE-bench Lite has `test` = 300 and `dev` = 23 examples drawn from 11 Python repositories. · **relevance**: Q1/Q6/FR-004 — candidate benchmark and smoke sizes. · **confidence**: high
- **source**: https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/raw/main/README.md · **claim**: SWE-bench Verified is "a subset of 500 samples from the SWE-bench test set, which have been human-validated for quality", with a single `test` split. · **relevance**: Q1/Q6 — the community-standard quality subset. · **confidence**: high
- **source**: https://arxiv.org/abs/2310.06770 · **claim**: The SWE-bench paper (ICLR 2024) defines the benchmark as "an evaluation framework consisting of 2,294 software engineering problems" across "12 popular Python repositories". · **relevance**: Q1 — authoritative definition of the benchmark. · **confidence**: high
- **source**: https://github.com/SWE-bench/SWE-bench · **claim**: The official harness loads tasks via the HF `datasets` library — `load_dataset('princeton-nlp/SWE-bench', split='test')` — so `princeton-nlp/SWE-bench` on HF is the canonical machine-readable source. · **relevance**: Q1/FR-002. · **confidence**: high

### Q2 — What licenses govern the dataset and the 12 source repositories, and what redistribution constraints apply?
- **source**: https://arxiv.org/html/2310.06770v3 · **claim**: Paper Table 12 lists per-repo licenses: astropy/django/flask/seaborn/scikit-learn BSD 3-Clause, pytest MIT, requests and xarray Apache-2.0, pylint GPL 2.0, and matplotlib/sphinx/sympy "Custom"; the ethics statement says the benchmark is collected entirely from public repositories with licenses that permit the authors' usage. · **relevance**: Q2/spec Assumptions — one copyleft (pylint) and three custom-licensed repos constrain vendoring. · **confidence**: high
- **source**: https://github.com/SWE-bench/SWE-bench · **claim**: The SWE-bench code/harness repo is MIT-licensed. · **relevance**: Q2/FR-003 — harness dependency licensing is unproblematic. · **confidence**: high
- **source**: https://huggingface.co/datasets/princeton-nlp/SWE-bench/raw/main/README.md · **claim**: Neither the full, Lite (https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite/raw/main/README.md), nor Verified (https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified/raw/main/README.md) dataset cards declares a license field — license obligations flow from the underlying repositories plus the MIT benchmark repo. · **relevance**: Q2/FR-003 — attribution must reference source-repo licenses, not a dataset-level license. · **confidence**: high

### Q3 — What is the established reproducible, offline-capable fetch pattern for HF-hosted task data?
- **source**: https://huggingface.co/docs/datasets/loading · **claim**: `load_dataset(..., revision=...)` accepts "tag name, or branch name, or commit hash" to pin a dataset version, split slices like `test[10:20]` and `ReadInstruction` select fixed deterministic subsets, and setting `HF_HUB_OFFLINE=1` forces full offline mode from the local cache. · **relevance**: Q3/FR-002 — pinned-revision load plus seeded/positional slice plus offline mode covers identical-output reruns. · **confidence**: high

### Q4 — What are the published anchor numbers, and what exactly does RepoGraph's +32.8% measure?
- **source**: https://arxiv.org/html/2410.14684v2 · **claim**: RepoGraph's +32.8% is an "average relative improvement" in success rate across four plugged-in systems (RAG+GPT-4, Agentless+GPT-4o, AutoCodeRover+GPT-4, SWE-agent+GPT-4o) on SWE-bench-Lite; absolute rates are 29.67% (Agentless+RepoGraph) and 20.33% (SWE-agent+RepoGraph); the paper reports no Verified numbers. · **relevance**: Q4/What — the spec's headline comparison is a resolve-rate metric on Lite, not an efficiency metric. · **confidence**: high
- **source**: https://github.com/ozyyshr/RepoGraph · **claim**: RepoGraph is Apache-2.0, builds a networkx graph with line-level info and def/ref (definition-reference) relations per repository, and is a plug-in retrieval layer for Agentless and SWE-agent — the closest published prior art to cairn's graph-assisted agent. · **relevance**: Q4 — method-level prior art for the README comparison. · **confidence**: high
- **source**: https://github.com/SWE-agent/mini-swe-agent · **claim**: mini-swe-agent (MIT) reports scoring ">74% on the SWE-bench verified benchmark", showing current-generation agent resolve rates well above the 2024 graph-agent figures. · **relevance**: Q4 — anchor for the deferred LLM arm's expected range. · **confidence**: med (README does not name the model behind the figure)
- **source**: no credible source found — decide from first principles · **claim**: No published deterministic (no-LLM) token/tool-call benchmark on SWE-bench exists to compare efficiency medians against; cairn's deterministic arm would be the reference for its own metric class. · **relevance**: Q4/FR-003 — published methodology must define the metric class explicitly. · **confidence**: med (absence claim, bounded search)

### Q5 — What does the task loader minimally need, and does the deterministic arm need the official harness?
- **source**: https://huggingface.co/datasets/princeton-nlp/SWE-bench/raw/main/README.md · **claim**: Field descriptions imply the deterministic efficiency arm needs only `instance_id`, `repo`, `base_commit` (+ `environment_setup_commit` for a faithful checkout) and `problem_statement`; `patch`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS` exist for gold-patch resolution testing, which only the deferred LLM arm consumes. · **relevance**: Q5/FR-001/FR-005 — minimal loader contract. · **confidence**: high
- **source**: https://github.com/SWE-bench/SWE-bench · **claim**: "SWE-bench uses Docker for reproducible evaluations" with guidance of "at least 120GB of free storage, 16GB of RAM, and 8 CPU cores" and experimental arm64 support — the official harness is required for resolve-rate scoring, not for reading task inputs. · **relevance**: Q5/FR-002/risk — the deterministic no-LLM arm legitimately skips the harness; pinning `swebench` as a dev-only dependency covers the future LLM arm. · **confidence**: high

### Q6 — What subset-selection patterns do established SWE-bench harnesses use (for fixed subsets and CI smoke)?
- **source**: https://www.mini-swe-agent.com/latest/usage/swebench/ · **claim**: mini-swe-agent's batch mode exposes `--subset` (full/verified/lite/multimodal/multilingual/smith/rebench or local path), `--split` (default `dev`), `--slice` ("Slice specification (e.g., '0:5' for first 5 instances)"), `--filter` (instance-id regex), and `--shuffle` — positional Python-style slicing over a pinned HF split is the established subsetting mechanism. · **relevance**: Q6/FR-002/FR-004 — prior art for a fixed-subset flag and smoke slicing. · **confidence**: high
- **source**: no credible source found — decide from first principles · **claim**: No standard "CI smoke subset" size is published for SWE-bench; sizing (e.g., Lite's 23-instance dev split vs a 10-20 instance slice of the reported split) is a first-principles decision balancing runtime against rot coverage. · **relevance**: Q6/FR-004. · **confidence**: med (absence claim, bounded search)

## Options summary

### Dataset split
- Lite (test 300, 11 repos) — the split graph-agent numbers incl. RepoGraph's +32.8% are measured on; smallest standard test set.
- Verified (test 500, human-validated) — community-quality standard; preferred by current agents, no graph-agent baseline need for the deterministic arm.
- Full test (2,294, 12 repos) — max coverage, heavyweight for medians and CI smoke.

### Fetch + pinning (FR-002)
- `load_dataset(repo, revision=<commit-sha>)` + fixed slice/seed — content-pinned with minimal code; needs one online fetch.
- Vendored/snapshot cache + `HF_HUB_OFFLINE=1` — fully offline reruns; duplicates ~1-4 MB (Lite) to 120 MB (full) of data in-repo/LFS.

### Subset pinning + redistribution
- Frozen instance-id manifest in-repo — pins the subset exactly without redistributing content (pylint is GPL-2.0; matplotlib/sphinx/sympy are "Custom").
- Vendoring full task data — maximally frozen but redistributes mixed-license content; riskiest license posture.

### Smoke subset shape (FR-004)
- Lite `dev` split (23 instances) — standard-selectable (`--split dev` idiom), tiny, but not the split the published arm reports on.
- Slice of the reported split (`0:10`) — same split as the headline medians, catching rot in exactly the reported path.

### Comparability scope (What/FR-003)
- Publish efficiency medians as their own metric class — honest for the deterministic arm, but not numerically comparable to RepoGraph's +32.8% (a resolve rate).
- Comparable resolve-rate number requires the deferred LLM arm (FR-005) — published anchors: RepoGraph 29.67% Lite, mini-swe-agent >74% Verified.
