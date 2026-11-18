"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    rows, base_rows = pr.get("rows") or [], base.get("rows") or []
    man, base_man = pr.get("manifest") or {}, base.get("manifest") or {}
    spec_ids = [m["id"] for m in spec.get("metrics") or []]

    # 1. Metric values must be finite numbers (NaN compares False with every threshold).
    for k, v in (pr.get("metrics") or {}).items():
        if isinstance(v, float) and not math.isfinite(v):
            problems.append(f"metric {k} is {v} (non-finite)")
    for k in spec_ids:
        if k in (pr.get("metrics") or {}) and pr["metrics"][k] is None and k in (rows[0] if rows else {}):
            problems.append(f"metric {k} is None although rows carry it")

    # 2. Grader failures: a crashed grader must never turn into a missing (= ignored) value.
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s) in the PR report")
    graded = [k for k in spec_ids if rows and any(k in r for r in rows)]
    for k in graded:
        n_none = sum(1 for r in rows if r.get(k) is None)
        if n_none:
            problems.append(f"{n_none} row(s) with {k}=None (crashed or blanked grader)")

    # 3. Row count and (item, seed) coverage: no dropped, duplicated or cherry-picked rows.
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    if len(keys) != len(set(keys)):
        problems.append(f"{len(keys) - len(set(keys))} duplicated (item_id, seed) row(s)")
    seeds = man.get("seeds") or []
    if len(rows) != (man.get("items") or 0) * len(seeds):
        problems.append(f"rows={len(rows)} != manifest items {man.get('items')} x seeds {len(seeds)}")
    if spec.get("seeds") and list(seeds) != list(spec["seeds"]):
        problems.append(f"manifest seeds {seeds} != spec seeds {spec['seeds']}")
    if (man.get("items"), list(seeds)) != (base_man.get("items"), list(base_man.get("seeds") or [])):
        problems.append("manifest items/seeds differ from the baseline's")
    base_keys = {(r.get("item_id"), r.get("seed")) for r in base_rows}
    if set(keys) != base_keys:
        miss, extra = len(base_keys - set(keys)), len(set(keys) - base_keys)
        problems.append(f"(item_id, seed) set differs from baseline: {miss} missing, {extra} extra")

    # 4. Slice tags are derived from labels; relabelling an item hides its regression from the slice gate.
    base_tags = {r.get("item_id"): sorted(r.get("slices") or []) for r in base_rows}
    retagged = sorted({r.get("item_id") for r in rows
                       if r.get("item_id") in base_tags and sorted(r.get("slices") or []) != base_tags[r.get("item_id")]})
    if retagged:
        problems.append(f"slice tags differ from baseline for {retagged[:5]}{'...' if len(retagged) > 5 else ''}")

    # 5. Headline numbers must follow from the rows (no hand-edited metrics).
    for k in graded:
        v = (pr.get("metrics") or {}).get(k)
        vals = [r[k] for r in rows if r.get(k) is not None]
        if v is None or not vals or (isinstance(v, float) and not math.isfinite(v)):
            continue
        want = sum(vals) if k in ("secret_leak", "approval_bypass") else sum(vals) / len(vals)
        if abs(float(v) - float(want)) > 1e-3:
            problems.append(f"metric {k}={v} does not match its rows ({want:.4f})")

    # 6. Ratchet: on the same eval-set version thresholds may only get stricter.
    if base_spec is not None:
        problems += _ratchet(spec, base_spec)
    return problems


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        return []                       # a new eval-set version starts a new baseline and new floors
    out: list[str] = []

    def looser(where: str, old: dict, new: dict | None) -> None:
        if new is None:
            out.append(f"ratchet: {where} was removed")
            return
        o, n = old.get("threshold", old), new.get("threshold", new)
        if "max" in o and ("max" not in n or n["max"] > o["max"]):
            out.append(f"ratchet: {where} max {o['max']} -> {n.get('max')}")
        if "min" in o and ("min" not in n or n["min"] < o["min"]):
            out.append(f"ratchet: {where} min {o['min']} -> {n.get('min')}")
        if "max_increase" in o and ("max_increase" not in n or n["max_increase"] > o["max_increase"]):
            out.append(f"ratchet: {where} max_increase {o['max_increase']} -> {n.get('max_increase')}")
        if o.get("no_significant_drop") and not n.get("no_significant_drop"):
            out.append(f"ratchet: {where} dropped no_significant_drop")
        if old.get("blocking") and not new.get("blocking"):
            out.append(f"ratchet: {where} is no longer blocking")

    new_metrics = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        looser(f"metric {m['id']}", m, new_metrics.get(m["id"]))
    new_slices = {(s["name"], s["metric"]): s for s in spec.get("slices") or []}
    for s in base_spec.get("slices") or []:
        looser(f"slice {s['name']}/{s['metric']}", s, new_slices.get((s["name"], s["metric"])))
    if list(spec.get("seeds") or []) != list(base_spec.get("seeds") or []):
        out.append(f"ratchet: seeds {base_spec.get('seeds')} -> {spec.get('seeds')}")
    return out


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for s in spec.get("slices") or []:
        name, metric = s["name"], s["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", "", None
        if not a:
            # A slice that vanished from the PR report cannot be judged: never pass it silently.
            verdict, note = "missing", "no PR rows in slice"
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in s and val < s["min"]:
                verdict = "fail"
            if s.get("no_significant_drop"):
                keys = sorted(set(a) & set(b))
                if keys:
                    d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                       n_resamples=bs.get("n_resamples", 1000),
                                                       rng=bs.get("rng", 2027),
                                                       confidence=bs.get("confidence", 0.95))
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
                else:
                    verdict, note = "missing", "slice items differ from baseline"
        if verdict != "ok" and s.get("blocking"):
            blocked.append(sid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if s.get("blocking") else "warn")
        th = {k: s[k] for k in ("min", "no_significant_drop") if k in s}
        lines.append(f"  {sid:<38} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
