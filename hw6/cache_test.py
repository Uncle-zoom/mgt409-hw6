"""Run the agent on all 12 practice documents with and without cache and compare cost.

Setup "with-cache": handbook first and byte-identical on every request, fixed prompt_cache_key.
Setup "no-cache":   a NEW random string at the start of every model request's instructions.

Writes output/cache_test.json (every model request) and the data sections of
output/cache_test.md. Stops immediately if a no-cache request reports cached tokens > 0.
"""

from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path

from agent import build_agent, load_config, run_document
from tools import now_iso

ROOT = Path(__file__).resolve().parent
DOCS = sorted((ROOT / "data" / "practice_docs").iterdir())
PRICES = json.loads((ROOT / "data" / "prices.json").read_text())
JSON_OUT = ROOT / "output" / "cache_test.json"
MD_OUT = ROOT / "output" / "cache_test.md"
ESTIMATE_MD = ROOT / "output" / "handbook_estimate.md"
DOCS_PER_DAY, DAYS = 1000, 30
SETUPS = [("with-cache", False), ("no-cache", True)]


def dec(x) -> Decimal:
    return Decimal(str(x))


def run_all() -> tuple[list[dict], str | None]:
    """Returns (request rows, problem message or None)."""
    model_name, api_key = load_config()
    rows: list[dict] = []
    for setup, no_cache in SETUPS:
        agent = build_agent(model_name, api_key, no_cache)
        for doc in DOCS:
            rel = str(doc.relative_to(ROOT))
            outcome = run_document(agent, model_name, rel, setup, no_cache, frozenset())
            for r in outcome.usage.per_request:
                rows.append(
                    {
                        "setup": setup,
                        "document": rel,
                        "run_id": outcome.usage.run_id,
                        "request_number": r.request_number,
                        "instructions_start": r.instructions_start,
                        "input_tokens": r.input_tokens,
                        "cached_tokens": r.cached_tokens,
                        "cache_write_tokens": r.cache_write_tokens,
                        "output_tokens": r.output_tokens,
                        "reasoning_tokens": r.reasoning_tokens,
                        "cost_usd": r.cost_usd,
                    }
                )
                print(
                    f"{setup:10} {doc.name:28} req {r.request_number}: in={r.input_tokens} "
                    f"cached={r.cached_tokens} write={r.cache_write_tokens} out={r.output_tokens} "
                    f"reason={r.reasoning_tokens} ${r.cost_usd:.6f}",
                    flush=True,
                )
                if no_cache and r.cached_tokens > 0:
                    return rows, (
                        f"STOP: no-cache request {r.request_number} on {rel} reported "
                        f"{r.cached_tokens} cached tokens."
                    )
    return rows, None


def summarize(rows: list[dict], setup: str) -> dict:
    p = PRICES["gpt-6-luna"]
    mine = [r for r in rows if r["setup"] == setup]
    s = {k: sum(r[k] for r in mine) for k in ("input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens")}
    s["fresh_tokens"] = s["input_tokens"] - s["cached_tokens"] - s["cache_write_tokens"]
    s["requests"] = len(mine)
    s["docs"] = len({r["document"] for r in mine})
    s["usd_fresh"] = dec(s["fresh_tokens"]) * dec(p["input_per_1m"]) / 1_000_000
    s["usd_cached"] = dec(s["cached_tokens"]) * dec(p["cached_input_per_1m"]) / 1_000_000
    s["usd_write"] = dec(s["cache_write_tokens"]) * dec(p["cache_write_per_1m"]) / 1_000_000
    s["usd_output"] = dec(s["output_tokens"]) * dec(p["output_per_1m"]) / 1_000_000
    s["usd_reasoning"] = dec(s["reasoning_tokens"]) * dec(p["output_per_1m"]) / 1_000_000
    s["usd_input_side"] = s["usd_fresh"] + s["usd_cached"] + s["usd_write"]
    s["usd_total"] = s["usd_input_side"] + s["usd_output"]
    per_doc: dict[str, Decimal] = {}
    for r in mine:
        per_doc[r["document"]] = per_doc.get(r["document"], Decimal(0)) + dec(r["cost_usd"])
    s["per_doc"] = per_doc
    s["first_request"] = mine[0]
    return s


def usd(x: Decimal, places: int = 6) -> str:
    return f"${x:.{places}f}"


def handbook_tokens() -> int:
    match = re.search(r"API difference[^|]*\|\s*\*\*([\d,]+)\*\*", ESTIMATE_MD.read_text())
    return int(match.group(1).replace(",", "")) if match else 10_062


def write_report(rows: list[dict]) -> None:
    p = PRICES["gpt-6-luna"]
    w, n = summarize(rows, "with-cache"), summarize(rows, "no-cache")
    H = handbook_tokens()
    saved = (n["usd_total"] - w["usd_total"]) / n["usd_total"]
    w_steady = [v for d, v in w["per_doc"].items() if d != rows[0]["document"]]
    w_steady_mean = sum(w_steady) / len(w_steady)

    def setup_row(label: str, s: dict) -> str:
        return (
            f"| {label} | {s['requests']} | {s['input_tokens']:,} | {s['cached_tokens']:,} | "
            f"{s['cache_write_tokens']:,} | {s['fresh_tokens']:,} | {s['output_tokens']:,} | "
            f"{s['reasoning_tokens']:,} | {usd(s['usd_total'], 4)} | {usd(s['usd_total'] / s['docs'], 6)} |"
        )

    def comp_row(label: str, s: dict) -> str:
        t = s["usd_total"]
        return (
            f"| {label} | {usd(s['usd_fresh'])} | {usd(s['usd_cached'])} | {usd(s['usd_write'])} | "
            f"{usd(s['usd_output'])} ({s['usd_output'] / t:.1%}) | "
            f"{usd(s['usd_reasoning'])} ({s['usd_reasoning'] / t:.1%}) | {usd(t)} |"
        )

    N_w, N_n = w["requests"], n["requests"]
    est = {
        "w_cached_mix": (dec(p["cache_write_per_1m"]) * H + dec(p["cached_input_per_1m"]) * H * (N_w - 1)) / 1_000_000,
        "n_plain": dec(p["input_per_1m"]) * H * N_n / 1_000_000,
        "n_write": dec(p["cache_write_per_1m"]) * H * N_n / 1_000_000,
    }
    per_doc_rows = "\n".join(
        f"| {d.split('/')[-1]} | {usd(w['per_doc'][d])} | {usd(n['per_doc'][d])} |" for d in sorted(w["per_doc"])
    )
    docs_total = DOCS_PER_DAY * DAYS
    proj_w, proj_n = w["usd_total"] / w["docs"] * docs_total, n["usd_total"] / n["docs"] * docs_total
    proj_w_steady = w_steady_mean * docs_total
    fr = w["first_request"]
    avg_in_w, avg_in_n = w["input_tokens"] / N_w, n["input_tokens"] / N_n

    md = f"""# Cache test: 12 practice documents, with and without cache

Generated {now_iso()} by `cache_test.py`. Model `gpt-6-luna`, prices from `data/prices.json`
(input ${p['input_per_1m']}, cached input ${p['cached_input_per_1m']}, cache write ${p['cache_write_per_1m']},
output ${p['output_per_1m']} per 1M tokens). Every model request is in `output/cache_test.json`.

How the two setups differ:

- **with-cache**: handbook first and byte-identical on every request, `prompt_cache_key="campus-customs-handbook"`.
- **no-cache**: a new random string at the very start of the instructions on every model
  request (each document makes 2 requests). The sanity check passed: all {N_n} no-cache
  requests report 0 cached tokens.

Cost of a request = fresh input x ${p['input_per_1m']} + cached x ${p['cached_input_per_1m']} + cache-write x ${p['cache_write_per_1m']} + output x ${p['output_per_1m']}, each per 1M.
Reasoning tokens are part of output tokens and are billed once, as output.

## Totals

| Setup | Requests | Input tokens | Cached | Cache write | Fresh (plain input) | Output | of which reasoning | Total cost | Cost per document |
|---|---|---|---|---|---|---|---|---|---|
{setup_row('with-cache', w)}
{setup_row('no-cache', n)}

**Saved by caching: {usd(n['usd_total'] - w['usd_total'], 6)} of {usd(n['usd_total'], 6)} = {saved:.1%}.**

### Where the money goes

| Setup | Fresh input | Cached input | Cache write | Output (share of bill) | of which reasoning (share) | Total |
|---|---|---|---|---|---|---|
{comp_row('with-cache', w)}
{comp_row('no-cache', n)}

### Cost per document

| Document | with-cache | no-cache |
|---|---|---|
{per_doc_rows}

First with-cache request ({fr['document'].split('/')[-1]}, request 1): input {fr['input_tokens']:,},
cached {fr['cached_tokens']:,}, cache write {fr['cache_write_tokens']:,}, output {fr['output_tokens']:,},
cost {usd(dec(fr['cost_usd']))}.

## Comparison with the Problem 2 estimate

Problem 2 priced the handbook alone at {H:,} tokens. In this test the real requests averaged
{avg_in_w:,.0f} input tokens (with-cache) and {avg_in_n:,.0f} (no-cache), because each request also carries
the instructions, tool definitions, the document and, on request 2, the tool result.

| Case ({N_w} requests each) | Problem 2 handbook-only estimate | Real input-side cost (everything sent) |
|---|---|---|
| with-cache: 1 write + {N_w - 1} cached | {usd(est['w_cached_mix'])} | {usd(w['usd_input_side'])} |
| no-cache, if billed at plain input price | {usd(est['n_plain'])} | {usd(n['usd_input_side'])} |
| no-cache, if every request billed as a cache write | {usd(est['n_write'])} | {usd(n['usd_input_side'])} |

Output tokens are not in the Problem 2 estimate at all; they add {usd(w['usd_output'])} (with-cache) and
{usd(n['usd_output'])} (no-cache).

## Projection: {DOCS_PER_DAY:,} documents a day for {DAYS} days ({docs_total:,} documents)

Using the measured mean cost per document in this test.

| Setup | Per document | Per day | 30 days |
|---|---|---|---|
| with-cache (mean of all 12, includes the cold start) | {usd(w['usd_total'] / w['docs'], 6)} | {usd(w['usd_total'] / w['docs'] * DOCS_PER_DAY, 2)} | {usd(proj_w, 2)} |
| with-cache (documents 2 to 12 only, warm cache) | {usd(w_steady_mean, 6)} | {usd(w_steady_mean * DOCS_PER_DAY, 2)} | {usd(proj_w_steady, 2)} |
| no-cache | {usd(n['usd_total'] / n['docs'], 6)} | {usd(n['usd_total'] / n['docs'] * DOCS_PER_DAY, 2)} | {usd(proj_n, 2)} |

The 30-day saving is {usd(proj_n - proj_w, 2)} ({(proj_n - proj_w) / proj_n:.1%}) on the all-12 mean.

<!-- analysis below is written by hand from the numbers above -->
"""
    MD_OUT.write_text(md, encoding="utf-8")


def main() -> None:
    rows, problem = run_all()
    JSON_OUT.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} requests to {JSON_OUT}")
    if problem:
        print(problem)
        sys.exit(2)
    write_report(rows)
    print(f"wrote {MD_OUT}")


if __name__ == "__main__":
    main()
