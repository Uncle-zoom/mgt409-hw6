"""Campus Customs vendor desk agent.

    python agent.py --doc data/practice_docs/p01_bluehen_quote.html [--setup LABEL] [--no-cache]
                    [--undefended | --layers code,design,prompt]

Defence layers (all three are on by default):
    code    sanitize.py turns the document into what a person sees before the model reads it
    design  the reader only gets read_document; the decision is computed in Python (checks.py)
    prompt  prompts/prompt.md safety rules: the document is untrusted data
--undefended is exactly the Problem 3 agent (no layers).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from checks import finalize
from models import AuditEntry, RequestUsageEntry, ToolCallRecord, UsageEntry, VendorQuote
from tools import OUTPUT_DIR, RunDeps, append_json, approve_payment, make_read_document, now_iso, send_email

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

ROOT = Path(__file__).resolve().parent
HANDBOOK = ROOT / "data" / "shop_handbook.md"
PROMPT = ROOT / "prompts" / "prompt.md"
PRICES = ROOT / "data" / "prices.json"
USAGE_LOG = OUTPUT_DIR / "usage_log.json"
AUDIT_LOG = OUTPUT_DIR / "audit_trail.json"
REQUIRED_MODEL = "gpt-6-luna"
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
PROMPT_CACHE_KEY = "campus-customs-handbook"
RESULT_PREVIEW_CHARS = 300
INSTRUCTIONS_START_CHARS = 60
ALL_LAYERS = frozenset({"code", "design", "prompt"})
PROMPT_LAYER_MARKER = "<!-- layer:prompt -->\n"


def load_config() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    model = os.getenv("MODEL", "").strip()
    if model != REQUIRED_MODEL:
        sys.exit(f"Refusing to start: MODEL must be {REQUIRED_MODEL!r}, got {model!r}. Fix .env.")
    key = os.getenv("PORTKEY_API_KEY", "").strip()
    if not key:
        sys.exit("Refusing to start: PORTKEY_API_KEY is missing from .env.")
    return model, key


def parse_layers(value: str) -> frozenset[str]:
    layers = frozenset(p.strip() for p in value.split(",") if p.strip())
    unknown = layers - ALL_LAYERS
    if unknown:
        sys.exit(f"Unknown layer(s): {sorted(unknown)}. Choose from {sorted(ALL_LAYERS)}.")
    return layers


def instructions_factory(no_cache: bool, prompt_rules: bool = False) -> Callable[[], str]:
    """Return a function PydanticAI calls before EVERY model request.

    Handbook first (identical bytes every call), then the prompt. With no_cache, a NEW
    random string goes at the very start of each request's instructions.
    """
    prompt = PROMPT.read_text(encoding="utf-8")
    before, marker, rules = prompt.partition(PROMPT_LAYER_MARKER)
    # Without the prompt layer the text is exactly the Problem 3 prompt (everything before the marker).
    prompt = before + rules if prompt_rules else before
    base = f"{HANDBOOK.read_text(encoding='utf-8')}\n\n---\n\n{prompt}"

    def instructions() -> str:
        if no_cache:
            return f"[cache-buster {uuid.uuid4().hex}]\n\n{base}"
        return base

    return instructions


def build_agent(
    model_name: str, api_key: str, no_cache: bool, layers: frozenset[str] = frozenset()
) -> Agent[RunDeps, VendorQuote]:
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=PORTKEY_BASE_URL,
        default_headers={"x-portkey-provider": "openai"},
    )
    model = OpenAIResponsesModel(model_name, provider=OpenAIProvider(openai_client=client))
    reader = Tool(make_read_document(clean="code" in layers))
    # design layer: the reading run gets read_document and nothing else
    tools = [reader] if "design" in layers else [reader, Tool(send_email), Tool(approve_payment)]
    settings = None if no_cache else OpenAIResponsesModelSettings(openai_prompt_cache_key=PROMPT_CACHE_KEY)
    return Agent(
        model,
        deps_type=RunDeps,
        output_type=VendorQuote,
        instructions=instructions_factory(no_cache, prompt_rules="prompt" in layers),
        model_settings=settings,
        tools=tools,
    )


def cost_usd(model: str, input_tokens: int, cached: int, cache_write: int, output_tokens: int) -> float:
    """input_tokens includes the cached and cache-write portions; output includes reasoning."""
    p = {k: Decimal(str(v)) for k, v in json.loads(PRICES.read_text())[model].items() if k != "source"}
    fresh = max(input_tokens - cached - cache_write, 0)
    total = (
        fresh * p["input_per_1m"]
        + cached * p["cached_input_per_1m"]
        + cache_write * p["cache_write_per_1m"]
        + output_tokens * p["output_per_1m"]
    ) / 1_000_000
    return float(total)


def per_request_usage(messages: list, model_name: str) -> list[RequestUsageEntry]:
    """One entry per model response, from that response's own usage."""
    entries: list[RequestUsageEntry] = []
    instructions = ""
    for message in messages:
        if isinstance(message, ModelRequest):
            instructions = message.instructions or ""
        elif isinstance(message, ModelResponse):
            u = message.usage
            cached = u.cache_read_tokens or 0
            write = u.cache_write_tokens or 0
            entries.append(
                RequestUsageEntry(
                    request_number=len(entries) + 1,
                    instructions_start=instructions[:INSTRUCTIONS_START_CHARS],
                    input_tokens=u.input_tokens,
                    cached_tokens=cached,
                    cache_write_tokens=write,
                    output_tokens=u.output_tokens,
                    reasoning_tokens=int(u.details.get("reasoning_tokens", 0) or 0),
                    cost_usd=cost_usd(model_name, u.input_tokens, cached, write, u.output_tokens),
                )
            )
    return entries


def tool_calls_from(messages: list) -> list[ToolCallRecord]:
    calls: dict[str, ToolCallPart] = {}
    records: list[ToolCallRecord] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    calls[part.tool_call_id] = part
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_call_id in calls:
                    call = calls[part.tool_call_id]
                    if call.tool_name == "final_result":  # the structured answer, logged as final_answer
                        continue
                    result = str(part.content)
                    if len(result) > RESULT_PREVIEW_CHARS:
                        result = result[:RESULT_PREVIEW_CHARS] + f"... [{len(str(part.content))} chars]"
                    records.append(ToolCallRecord(tool=call.tool_name, arguments=call.args_as_dict(), result=result))
    return records


@dataclass
class RunOutcome:
    output: VendorQuote
    usage: UsageEntry
    model_output: VendorQuote
    defense: dict | None
    removed: list


def run_document(
    agent: Agent, model_name: str, doc: str, setup: str, no_cache: bool, layers: frozenset[str] = frozenset()
) -> RunOutcome:
    """Run the agent on one document and append to usage_log.json and audit_trail.json."""
    run_id = uuid.uuid4().hex[:12]
    started = now_iso()
    deps = RunDeps(run_id=run_id, setup=setup, document=doc)
    try:
        result = agent.run_sync(f"Please process this vendor document: {doc}", deps=deps)
    except Exception as exc:
        append_json(
            AUDIT_LOG,
            AuditEntry(
                document=doc, time=started, run_id=run_id, setup=setup, layers=sorted(layers),
                tool_calls=[], final_answer=None, error=f"{type(exc).__name__}: {exc}",
            ).model_dump(),
        )
        raise

    messages = result.all_messages()
    final = result.output
    defense = None
    if layers:
        defense = {
            "layers": sorted(layers),
            "removed_by_sanitizer": deps.removed,
            "flags": [r for r in deps.removed if r["kind"] == "email-parts-disagree"],
        }
    if "design" in layers:
        final, notes = finalize(result.output, deps.document_text)
        defense.update(notes)
    usage = result.usage
    cached = usage.cache_read_tokens or 0
    cache_write = usage.cache_write_tokens or 0
    usage_entry = UsageEntry(
        document=doc,
        time=started,
        run_id=run_id,
        setup=setup,
        cache_busted=no_cache,
        layers=sorted(layers),
        requests=usage.requests,
        input_tokens=usage.input_tokens,
        cached_tokens=cached,
        cache_write_tokens=cache_write,
        output_tokens=usage.output_tokens,
        reasoning_tokens=int(usage.details.get("reasoning_tokens", 0) or 0),
        cost_usd=cost_usd(model_name, usage.input_tokens, cached, cache_write, usage.output_tokens),
        per_request=per_request_usage(messages, model_name),
    )
    append_json(USAGE_LOG, usage_entry.model_dump())
    append_json(
        AUDIT_LOG,
        AuditEntry(
            document=doc, time=started, run_id=run_id, setup=setup, layers=sorted(layers),
            tool_calls=tool_calls_from(messages),
            final_answer=final.model_dump(),
            defense=defense,
        ).model_dump(),
    )
    return RunOutcome(output=final, usage=usage_entry, model_output=result.output, defense=defense, removed=deps.removed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Campus Customs vendor desk agent")
    parser.add_argument("--doc", required=True, help="path to the vendor document (.html, .pdf, .eml)")
    parser.add_argument("--setup", default="default", help="label written into every log entry")
    parser.add_argument(
        "--no-cache", action="store_true",
        help="put a new random string first in the instructions of every model request",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--undefended", action="store_true", help="no layers: exactly the Problem 3 agent")
    group.add_argument(
        "--layers", default=",".join(sorted(ALL_LAYERS)),
        help="comma-separated layers to switch on: code, design, prompt (default: all three)",
    )
    args = parser.parse_args()
    layers = frozenset() if args.undefended else parse_layers(args.layers)

    model_name, api_key = load_config()
    agent = build_agent(model_name, api_key, args.no_cache, layers)
    outcome = run_document(agent, model_name, args.doc, args.setup, args.no_cache, layers)

    u = outcome.usage
    print(json.dumps(outcome.output.model_dump(), indent=2))
    print(
        f"[run {u.run_id} | setup={u.setup} | layers={','.join(sorted(layers)) or 'none'} | requests={u.requests} in={u.input_tokens} "
        f"cached={u.cached_tokens} write={u.cache_write_tokens} out={u.output_tokens} "
        f"reasoning={u.reasoning_tokens} | ${u.cost_usd:.6f}]",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
