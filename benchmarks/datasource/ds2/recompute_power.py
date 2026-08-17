#!/usr/bin/env python3
"""Recompute the DS-v2 power-analysis record from its own committed inputs.

Reads benchmarks/datasource/ds2/power-analysis.json (schema
cairn-ds2-power-analysis/1), re-derives every recorded figure from the
committed DS-v1 CI rows using stdlib arithmetic only (Sakai-style
topic-set-size design, two-sided paired test at alpha=0.05, detectable-effect
framing plus an 80%-power variant), asserts exact agreement with the recorded
outputs, and prints the recorded n_required values. No model inference, no
retrieval sweeps -- pure arithmetic over the record's inputs.

Run: uv run python benchmarks/datasource/ds2/recompute_power.py
Exits 0 on agreement, 1 with a diff on any mismatch.
"""

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOL_FLOAT = 1e-6  # recorded floats carry 6 decimals (rounding error <= 5e-7)
TOL_RAW = 1e-3    # recorded pre-ceiling raws carry 3 decimals

failures = []


def check(label, recomputed, recorded, tol=0.0):
    ok = abs(recomputed - recorded) <= tol if isinstance(recorded, float) else recomputed == recorded
    if not ok:
        failures.append(f"{label}: recorded {recorded} != recomputed {recomputed}")
    return ok


def main() -> int:
    rec = json.loads((HERE / "power-analysis.json").read_text())
    z = rec["method"]["constants"]["z_2sided_95pct"]
    z80 = rec["method"]["constants"]["z_80power"]
    n0 = rec["inputs"]["n_validate"]

    # Step 1: half-widths and per-query paired-difference SDs from each committed CI.
    sigma = {}
    for cand_in, cand_out in zip(rec["inputs"]["candidates"], rec["derived"]["candidates"]):
        assert cand_in["combo"] == cand_out["combo"]
        h = (cand_in["ci_high"] - cand_in["ci_low"]) / 2.0
        sd = h * math.sqrt(n0) / z
        sigma[cand_in["combo"]] = sd
        check(f"half_width[{cand_in['combo']}]", h, cand_out["half_width"], TOL_FLOAT)
        check(f"sigma_d[{cand_in['combo']}]", sd, cand_out["sigma_d"], TOL_FLOAT)

    s_match = sigma[rec["derived"]["sigma_matched"]["combo"]]
    check("sigma_matched", s_match, rec["derived"]["sigma_matched"]["value"], TOL_FLOAT)
    s_cons = max(sigma.values())
    check("sigma_conservative_max", s_cons, rec["derived"]["sigma_conservative_max"], TOL_FLOAT)

    def raw_detect(delta, s):
        return (z * s / delta) ** 2

    def raw_pow80(delta, s):
        return ((z + z80) * s / delta) ** 2

    # Step 2/3: n_required for (a) the observed effect and (b) the half-effect.
    out = rec["outputs"]
    for key, delta in (("a_observed_effect", out["a_observed_effect"]["delta"]),
                       ("b_half_effect", out["b_half_effect"]["delta"])):
        r = out[key]
        for framing, raw_fn, s, tag in (
            ("detectable", raw_detect, s_match, "matched"),
            ("detectable", raw_detect, s_cons, "conservative"),
            ("power80", raw_pow80, s_match, "matched"),
            ("power80", raw_pow80, s_cons, "conservative"),
        ):
            raw = raw_fn(delta, s)
            check(f"n_{framing}_{tag}_raw[{key}]", raw, r[f"n_{framing}_{tag}_raw"], TOL_RAW)
            check(f"n_{framing}_{tag}[{key}]", math.ceil(raw), r[f"n_{framing}_{tag}"])

    # Step 4: minimum certifiable effect at the target n=150.
    target_n = out["target_l1"]
    check("delta_min@150 matched", z * s_match / math.sqrt(target_n),
          out["min_detectable_effect_at_150"]["matched"], TOL_FLOAT)
    check("delta_min@150 conservative", z * s_cons / math.sqrt(target_n),
          out["min_detectable_effect_at_150"]["conservative"], TOL_FLOAT)

    # Step 5: the decision itself -- target = max(150 floor, operative n_required).
    check("target_l1", max(150, rec["decision"]["n_required_operative"]), rec["decision"]["target_l1_queries"])
    check("target_l5", 40, rec["decision"]["target_l5_queries"])

    # Cross-checks: the direct 1/sqrt(n) route and the research-note arithmetic.
    best = next(c for c in rec["inputs"]["candidates"] if c["combo"] == rec["derived"]["sigma_matched"]["combo"])
    h_best = (best["ci_high"] - best["ci_low"]) / 2.0
    check("sqrt_n direct route", math.ceil(n0 * (h_best / best["delta"]) ** 2),
          out["a_observed_effect"]["n_detectable_matched"])
    check("h(120) rq4 arithmetic", h_best * math.sqrt(n0 / 120), 0.067693, TOL_FLOAT)
    check("delta_min@29 == measured half-width", z * s_match / math.sqrt(n0), h_best, TOL_FLOAT)

    # Report.
    a, b = out["a_observed_effect"], out["b_half_effect"]
    print(f"schema            : {rec['schema']}")
    print(f"sigma_d           : matched {s_match:.6f} / conservative {s_cons:.6f} (n0={n0})")
    print(f"n_required (a) d=+{a['delta']}: detectable {a['n_detectable_matched']} (matched) / "
          f"{a['n_detectable_conservative']} (conservative); 80%-power {a['n_power80_matched']} / "
          f"{a['n_power80_conservative']}")
    print(f"n_required (b) d=+{b['delta']}: detectable {b['n_detectable_matched']} (matched) / "
          f"{b['n_detectable_conservative']} (conservative); 80%-power {b['n_power80_matched']} / "
          f"{b['n_power80_conservative']}")
    print(f"min certifiable effect at n={target_n}: +{out['min_detectable_effect_at_150']['matched']} "
          f"(matched) / +{out['min_detectable_effect_at_150']['conservative']} (conservative)")
    print(f"decision          : target L1 = {rec['decision']['target_l1_queries']} = max(150, "
          f"{rec['decision']['n_required_operative']}), L5 >= {rec['decision']['target_l5_queries']}")

    if failures:
        print(f"\nFAIL ({len(failures)} mismatches):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("OK: all recorded figures reproduced exactly from the committed inputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
