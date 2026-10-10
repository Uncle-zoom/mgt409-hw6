# Cache test: 12 practice documents, with and without cache

Generated 2026-10-10T15:26:45+00:00 by `cache_test.py`. Model `gpt-6-luna`, prices from `data/prices.json`
(input $0.1, cached input $0.01, cache write $0.125,
output $0.5 per 1M tokens). Every model request is in `output/cache_test.json`.

How the two setups differ:

- **with-cache**: handbook first and byte-identical on every request, `prompt_cache_key="campus-customs-handbook"`.
- **no-cache**: a new random string at the very start of the instructions on every model
  request (each document makes 2 requests). The sanity check passed: all 24 no-cache
  requests report 0 cached tokens.

Cost of a request = fresh input x $0.1 + cached x $0.01 + cache-write x $0.125 + output x $0.5, each per 1M.
Reasoning tokens are part of output tokens and are billed once, as output.

## Totals

| Setup | Requests | Input tokens | Cached | Cache write | Fresh (plain input) | Output | of which reasoning | Total cost | Cost per document |
|---|---|---|---|---|---|---|---|---|---|
| with-cache | 24 | 255,053 | 239,409 | 12,620 | 3,024 | 2,605 | 1,122 | $0.0056 | $0.000465 |
| no-cache | 24 | 255,624 | 0 | 252,600 | 3,024 | 2,686 | 1,222 | $0.0332 | $0.002768 |

**Saved by caching: $0.027644 of $0.033220 = 83.2%.**

### Where the money goes

| Setup | Fresh input | Cached input | Cache write | Output (share of bill) | of which reasoning (share) | Total |
|---|---|---|---|---|---|---|
| with-cache | $0.000302 | $0.002394 | $0.001578 | $0.001302 (23.4%) | $0.000561 (10.1%) | $0.005576 |
| no-cache | $0.000302 | $0.000000 | $0.031575 | $0.001343 (4.0%) | $0.000611 (1.8%) | $0.033220 |

### Cost per document

| Document | with-cache | no-cache |
|---|---|---|
| p01_bluehen_quote.html | $0.001522 | $0.002785 |
| p02_elmcity_quote.html | $0.000353 | $0.002771 |
| p03_ivyprint_catalog.html | $0.000492 | $0.002869 |
| p04_harborstitch_quote.html | $0.000347 | $0.002725 |
| p05_maplemills_quote.pdf | $0.000338 | $0.002733 |
| p06_qtextile_quote.pdf | $0.000322 | $0.002726 |
| p07_longwharf_quote.pdf | $0.000391 | $0.002759 |
| p08_chapelst_email.eml | $0.000333 | $0.002752 |
| p09_westville_email.eml | $0.000432 | $0.002866 |
| p10_eastrock_email.eml | $0.000358 | $0.002747 |
| p11_yorkst_quote.html | $0.000331 | $0.002729 |
| p12_bluehen_quote.pdf | $0.000356 | $0.002758 |

First with-cache request (p01_bluehen_quote.html, request 1): input 10,544,
cached 0, cache write 10,418, output 27,
cost $0.001328.

## Comparison with the Problem 2 estimate

Problem 2 priced the handbook alone at 10,062 tokens. In this test the real requests averaged
10,627 input tokens (with-cache) and 10,651 (no-cache), because each request also carries
the instructions, tool definitions, the document and, on request 2, the tool result.

| Case (24 requests each) | Problem 2 handbook-only estimate | Real input-side cost (everything sent) |
|---|---|---|
| with-cache: 1 write + 23 cached | $0.003572 | $0.004274 |
| no-cache, if billed at plain input price | $0.024149 | $0.031877 |
| no-cache, if every request billed as a cache write | $0.030186 | $0.031877 |

Output tokens are not in the Problem 2 estimate at all; they add $0.001302 (with-cache) and
$0.001343 (no-cache).

## Projection: 1,000 documents a day for 30 days (30,000 documents)

Using the measured mean cost per document in this test.

| Setup | Per document | Per day | 30 days |
|---|---|---|---|
| with-cache (mean of all 12, includes the cold start) | $0.000465 | $0.46 | $13.94 |
| with-cache (documents 2 to 12 only, warm cache) | $0.000369 | $0.37 | $11.06 |
| no-cache | $0.002768 | $2.77 | $83.05 |

The 30-day saving is $69.11 (83.2%) on the all-12 mean.


## What surprised me, and why

**1. No-cache requests are billed as cache writes, at $0.125, not $0.10.** Yes. In the
no-cache setup 252,600 of 255,624 input tokens (98.8%) were reported as cache-write tokens
and 0 as cached. The random string stops any *read*, but the API still stores each new
prefix, and prices.json charges $0.125 per 1M for that. Had the same 255,624 tokens been
billed at the plain $0.10, the input side would have been $0.0256 instead of $0.0319: the
"no cache" baseline is **$0.0063 (25%) more expensive than plain input**. So turning
caching "off" this way is worse than a model with no caching at all, and the 83.2% saving
above is measured against that inflated baseline. Against a plain-input baseline of
$0.0269 total ($0.0256 input + $0.0013 output), the saving would be about 79%.

**2. The first with-cache request costs the same as a no-cache request.** Request 1 of
p01 had 0 cached tokens (the cache from my earlier runs had expired) and wrote 10,418
tokens: $0.001328, versus $0.001331 for the same request in no-cache. The whole with-cache
setup paid a write once; document 1 cost $0.001522 and documents 2 to 12 cost between
$0.000322 and $0.000492. This matches Problem 2's "first call writes the cache" row
($0.00125775 for the handbook alone).

**3. With cache, writes are still 28% of the bill.** 12,620 tokens were written even in
the with-cache setup ($0.001578 of $0.005576). Only the first request writes the handbook;
the other writes appear to be the tail of every request, the document text and the tool
result, which is new each time, is written to the cache, and is never reused. Caching saves on
the handbook but charges a 25% premium on everything that is not reused.

**4. Output is small in tokens but large in dollars once the input is cached.** Output
was 2,605 tokens (1.0% of tokens) and **23.4% of the with-cache bill** ($0.001302),
because output is priced at $0.50 per 1M, 5x the plain input price and 50x the cached
price. Reasoning tokens were 1,122 of those 2,605 output tokens (43% of output) and
**10.1% of the with-cache bill** ($0.000561), billed as output. In the no-cache setup the
same output is only 4.0% of the bill because the input side is so much bigger. Request 1
of each run has reasoning 0 (it only calls the tool); request 2, which writes the answer
and reason, carries all the reasoning: from 0 to 355 tokens, highest on p03 (355, a removed
vendor) and p09 (173).

**5. Real cost is above the Problem 2 estimate, but the saving held up.** The handbook
alone was estimated at $0.003572 for 24 requests with cache (1 write + 23 cached) and
$0.030186 without (all writes). Real input-side cost was $0.004274 (+20%) and $0.031877
(+6%): each request also sends about 565 more tokens than the handbook (instructions,
tool definitions, the document, the tool result), and those are mostly not cacheable.
Handbook-only, Problem 2 predicted an 88% saving; the real total saving is 83.2% because
output ($0.0013 in both setups) and the uncached tail dilute it.

**6. The second request of a run used to hit the cache.** In the earlier Problem 3 runs
request 2 read request 1's prefix from cache. That is why this test needed a new random
string on every request, not one per run: with it, all 24 no-cache requests show 0 cached
tokens (24 different instructions starts are recorded in `cache_test.json`).

## Caveats

- 12 documents and 24 requests per setup is a small sample; per-document figures are
  fractions of a cent and move with how long the reasoning runs.
- The projection assumes 1,000 documents a day keep the cache warm. If traffic stops for
  long enough that the cache expires, the next request pays the write again, as p01 did.
- "No-cache" here means a unique prefix, not the cache being disabled; the API still
  writes entries and bills them, as shown in point 1.
