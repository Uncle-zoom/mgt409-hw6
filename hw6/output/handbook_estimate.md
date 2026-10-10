# Handbook token estimate

File: `data/shop_handbook.md` (28,903 characters). Model: `gpt-6-luna`.

## The two counts

| Method | Tokens |
|---|---|
| 1. tiktoken, `o200k_base` | **10,058** |
| 2. API difference (with handbook minus baseline) | **10,062** |

Difference between the two: +4 tokens (+0.04% of the tiktoken count).

## How each was measured

1. **tiktoken.** `tiktoken.get_encoding("o200k_base").encode(text)` on the full text of the
   handbook, counting the returned token ids. Local, free, no network.
2. **API's own count.** Two requests to `gpt-6-luna` through Portkey (Responses API,
   `max_output_tokens=16`), both with the user message `"Hello"`:
   - with the handbook as the system message: **10,069** input tokens reported;
   - baseline, no system message: **7** input tokens reported.

   Handbook size as the model sees it = 10,069 - 7 = **10,062**.

## Why they differ a little

- tiktoken counts only the raw text. The API's number is a difference of two
  requests, so any fixed per-message framing cancels out except what the extra
  system message itself adds: its role marker and message delimiters (a few tokens).
- The model's own tokenizer and chat template are not guaranteed to be identical to
  the public `o200k_base` file, so counts on the same text can drift slightly.
- The API reports whole-request `input_tokens`, which is the number that is billed;
  tiktoken is an estimate of it.

## Cost of the handbook alone (using the API count, 10,062 tokens)

Prices for `gpt-6-luna` from `data/prices.json`: input $0.1, cached input
$0.01, cache write $0.125 (all per 1M tokens).
Each calculation is price per 1M x tokens / 1,000,000.

| Case | 1 call | 1,000 calls |
|---|---|---|
| Not cached (input price) | $0.1 x 10,062 / 1,000,000 = $0.0010062 | $0.0010062 x 1,000 = $1.0062 = $1.0062 |
| Cached (cached input price) | $0.01 x 10,062 / 1,000,000 = $0.00010062 | $0.00010062 x 1,000 = $0.10062 = $0.1006 |
| First call, writes the cache (cache write price) | $0.125 x 10,062 / 1,000,000 = $0.00125775 | $0.00125775 x 1,000 = $1.25775 = $1.2578 |
| Realistic run: 1 cache write + 999 cached calls | n/a (a run, not one call) | 1 x $0.00125775 + 999 x $0.00010062 = $0.00125775 + $0.10051938 = $0.10177713 = $0.1018 (saves 89.9% vs. $1.0062 uncached) |

Notes:

- In the 1,000-call "cached" row all 1,000 calls are assumed to be cache hits. In
  practice the first call writes the cache, so the realistic row is the write cost
  once plus 999 cached calls.
- Dollar figures are computed exactly, then the 1,000-call totals are rounded half-up
  to 4 decimals.
- This is the handbook only. The real agent also sends my instructions and the vendor
  document on every call, and produces output tokens. Problem 4 will measure those.
