"""Count the tokens in data/shop_handbook.md two ways and write output/handbook_estimate.md.

1) tiktoken, o200k_base encoding (local, no network).
2) The API's own count: two Responses API calls through Portkey, one with the
   handbook as the system message and one without; the difference in reported
   input_tokens is the handbook as the model sees it.

Key and model come from .env (PORTKEY_API_KEY, MODEL).
"""

from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import tiktoken
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
HANDBOOK = ROOT / "data" / "shop_handbook.md"
PRICES = ROOT / "data" / "prices.json"
OUT = ROOT / "output" / "handbook_estimate.md"
USER_MESSAGE = "Hello"

load_dotenv(ROOT / ".env")
MODEL = os.getenv("MODEL", "gpt-6-luna")
PORTKEY_BASE_URL = os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1").rstrip("/")


def tiktoken_count(text: str) -> int:
    return len(tiktoken.get_encoding("o200k_base").encode(text))


def api_input_tokens(client: OpenAI, system: str | None) -> int:
    messages = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": USER_MESSAGE})
    response = client.responses.create(model=MODEL, input=messages, max_output_tokens=16)
    return response.usage.input_tokens


def money(value: Decimal, places: int = 4) -> str:
    """Round half-up to `places` decimals and format as dollars."""
    return f"${value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)}"


def per_call(price_per_1m: float, tokens: int) -> Decimal:
    """price per 1M x tokens / 1,000,000, computed exactly."""
    return Decimal(str(price_per_1m)) * tokens / 1_000_000


def cost_lines(label: str, price_per_1m: float, tokens: int) -> list[str]:
    one = per_call(price_per_1m, tokens)
    return [
        f"| {label} | ${price_per_1m} x {tokens:,} / 1,000,000 = ${one:f} "
        f"| ${one:f} x 1,000 = ${(one * 1000).normalize():f} = {money(one * 1000)} |"
    ]


def realistic_row(prices: dict, tokens: int) -> tuple[str, Decimal, Decimal]:
    write = per_call(prices["cache_write_per_1m"], tokens)
    hit = per_call(prices["cached_input_per_1m"], tokens)
    total = write + 999 * hit
    uncached = per_call(prices["input_per_1m"], tokens) * 1000
    saved = (uncached - total) / uncached
    row = (
        f"| Realistic run: 1 cache write + 999 cached calls | n/a (a run, not one call) "
        f"| 1 x ${write:f} + 999 x ${hit:f} = ${write:f} + ${999 * hit:f} = ${total:f} "
        f"= {money(total)} (saves {saved:.1%} vs. {money(uncached)} uncached) |"
    )
    return row, total, saved


def main() -> None:
    text = HANDBOOK.read_text(encoding="utf-8")
    prices = json.loads(PRICES.read_text())[MODEL]

    tk = tiktoken_count(text)

    client = OpenAI(
        api_key=os.environ["PORTKEY_API_KEY"],
        base_url=PORTKEY_BASE_URL,
        default_headers={"x-portkey-provider": "openai"},
    )
    with_handbook = api_input_tokens(client, text)
    baseline = api_input_tokens(client, None)
    api = with_handbook - baseline
    print(f"tiktoken o200k_base: {tk}")
    print(f"API with handbook: {with_handbook}  baseline: {baseline}  difference: {api}")

    rows: list[str] = []
    rows += cost_lines("Not cached (input price)", prices["input_per_1m"], api)
    rows += cost_lines("Cached (cached input price)", prices["cached_input_per_1m"], api)
    rows += cost_lines("First call, writes the cache (cache write price)", prices["cache_write_per_1m"], api)
    realistic, _, _ = realistic_row(prices, api)

    md = f"""# Handbook token estimate

File: `data/shop_handbook.md` ({len(text):,} characters). Model: `{MODEL}`.

## The two counts

| Method | Tokens |
|---|---|
| 1. tiktoken, `o200k_base` | **{tk:,}** |
| 2. API difference (with handbook minus baseline) | **{api:,}** |

Difference between the two: {api - tk:+,} tokens ({(api - tk) / tk:+.2%} of the tiktoken count).

## How each was measured

1. **tiktoken.** `tiktoken.get_encoding("o200k_base").encode(text)` on the full text of the
   handbook, counting the returned token ids. Local, free, no network.
2. **API's own count.** Two requests to `{MODEL}` through Portkey (Responses API,
   `max_output_tokens=16`), both with the user message `"{USER_MESSAGE}"`:
   - with the handbook as the system message: **{with_handbook:,}** input tokens reported;
   - baseline, no system message: **{baseline:,}** input tokens reported.

   Handbook size as the model sees it = {with_handbook:,} - {baseline:,} = **{api:,}**.

## Why they differ a little

- tiktoken counts only the raw text. The API's number is a difference of two
  requests, so any fixed per-message framing cancels out except what the extra
  system message itself adds: its role marker and message delimiters (a few tokens).
- The model's own tokenizer and chat template are not guaranteed to be identical to
  the public `o200k_base` file, so counts on the same text can drift slightly.
- The API reports whole-request `input_tokens`, which is the number that is billed;
  tiktoken is an estimate of it.

## Cost of the handbook alone (using the API count, {api:,} tokens)

Prices for `{MODEL}` from `data/prices.json`: input ${prices['input_per_1m']}, cached input
${prices['cached_input_per_1m']}, cache write ${prices['cache_write_per_1m']} (all per 1M tokens).
Each calculation is price per 1M x tokens / 1,000,000.

| Case | 1 call | 1,000 calls |
|---|---|---|
{chr(10).join(rows)}
{realistic}

Notes:

- In the 1,000-call "cached" row all 1,000 calls are assumed to be cache hits. In
  practice the first call writes the cache, so the realistic row is the write cost
  once plus 999 cached calls.
- Dollar figures are computed exactly, then the 1,000-call totals are rounded half-up
  to 4 decimals.
- This is the handbook only. The real agent also sends my instructions and the vendor
  document on every call, and produces output tokens. Problem 4 will measure those.
"""
    OUT.write_text(md, encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
