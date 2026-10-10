"""Run the final defended agent (code + design + prompt, with cache) on every file in data/exam_docs/.

Writes output/exam_results.json straight from the agent's output: nothing is edited or
special-cased per file. Setup label: "exam".
"""

from __future__ import annotations

import json
from pathlib import Path

from agent import ALL_LAYERS, build_agent, load_config, run_document
from tools import TRAP_LOG

ROOT = Path(__file__).resolve().parent
EXAM_DIR = ROOT / "data" / "exam_docs"
OUT = ROOT / "output" / "exam_results.json"
SETUP = "exam"


def main() -> None:
    model_name, api_key = load_config()
    agent = build_agent(model_name, api_key, no_cache=False, layers=ALL_LAYERS)
    results: list[dict] = []
    for doc in sorted(p for p in EXAM_DIR.iterdir() if p.is_file() and p.name != ".DS_Store"):
        outcome = run_document(agent, model_name, str(doc.relative_to(ROOT)), SETUP, no_cache=False, layers=ALL_LAYERS)
        answer, usage = outcome.output, outcome.usage
        traps = [t for t in json.loads(TRAP_LOG.read_text()) if t.get("run_id") == usage.run_id]
        results.append(
            {
                "file": doc.name,
                "vendor": answer.vendor,
                "sku": answer.sku,
                "unit_price": answer.unit_price,
                "lead_time_days": answer.lead_time_days,
                "min_order": answer.min_order,
                "decision": answer.decision,
                "reason": answer.reason,
                "trap_tool_called": bool(traps),
                "input_tokens": usage.input_tokens,
                "cached_tokens": usage.cached_tokens,
                "output_tokens": usage.output_tokens,
                "cost_usd": usage.cost_usd,
            }
        )
        OUT.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"{doc.name}: {answer.decision}", flush=True)
    total_traps = sum(r["trap_tool_called"] for r in results)
    print(f"files={len(results)} total_cost_usd={sum(r['cost_usd'] for r in results):.6f} trap_calls={total_traps}")


if __name__ == "__main__":
    main()
