"""Layer 2 (design), the Python half: the decision is computed here, not by the model.

- Parses the handbook tables: approved vendors (section 5), removed and pending vendors
  (section 6), catalogue max unit cost per SKU (section 9), and the lead time and minimum
  order limits (section 4).
- Checks that every fact the model extracted appears in the text the model was given.
- Applies the four purchasing checks in order and writes the reason.
- Strips links and email addresses from any reason that is kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models import VendorQuote

HANDBOOK = Path(__file__).resolve().parent / "data" / "shop_handbook.md"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


@dataclass
class Handbook:
    approved: dict[str, str]  # normalised name -> name
    removed: dict[str, str]  # normalised name -> status text
    max_cost: dict[str, float]  # SKU -> max unit cost
    max_lead_days: int
    max_min_order: int


def _section(text: str, number: int) -> str:
    match = re.search(rf"^## {number}\..*?(?=^## \d+\.|\Z)", text, flags=re.S | re.M)
    if not match:
        raise ValueError(f"handbook section {number} not found")
    return match.group(0)


def _rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        if line.startswith("|") and not re.match(r"^\|[\s:|-]+\|?$", line):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows[1:]  # drop the header row


def load_handbook(path: Path = HANDBOOK) -> Handbook:
    text = path.read_text(encoding="utf-8")
    rules = _section(text, 4)
    lead = re.search(r"lead time is (\d+) days or less", rules)
    minimum = re.search(r"minimum order is (\d+) units or less", rules)
    if not (lead and minimum):
        raise ValueError("could not read the lead time and minimum order limits from section 4")
    approved = {_norm(r[1]): r[1] for r in _rows(_section(text, 5)) if len(r) >= 2}
    removed = {_norm(r[0]): r[1] for r in _rows(_section(text, 6)) if len(r) >= 2}
    max_cost = {
        r[0]: float(r[4].replace("$", "").replace(",", ""))
        for r in _rows(_section(text, 9))
        if len(r) >= 5 and re.fullmatch(r"CC-[A-Z]{2}-\d{3}", r[0])
    }
    return Handbook(approved, removed, max_cost, int(lead.group(1)), int(minimum.group(1)))


# ---------------------------------------------------------------- verification

_NUMBER = re.compile(r"(?<![\w-])\$?(\d[\d,]*(?:\.\d+)?)")


def numbers_in(text: str) -> list[float]:
    out = []
    for token in _NUMBER.findall(text):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:
            pass
    return out


def verify_facts(quote: VendorQuote, text: str) -> tuple[VendorQuote, list[str]]:
    """Null out any extracted fact that does not appear in the text. Returns (quote, problems)."""
    problems: list[str] = []
    data = quote.model_dump()
    squashed = _norm(text)
    if data["vendor"] is not None and _norm(data["vendor"]) not in squashed:
        problems.append(f"vendor {data['vendor']!r} not in the text")
        data["vendor"] = None
    if data["sku"] is not None and data["sku"].lower() not in text.lower():
        problems.append(f"sku {data['sku']!r} not in the text")
        data["sku"] = None
    nums = numbers_in(text)
    for field in ("unit_price", "lead_time_days", "min_order"):
        value = data[field]
        if value is not None and not any(abs(n - float(value)) < 0.005 for n in nums):
            problems.append(f"{field} {value!r} not in the text")
            data[field] = None
    return VendorQuote(**data), problems


# ---------------------------------------------------------------- the decision


def decide(quote: VendorQuote, hb: Handbook) -> tuple[str, str, str]:
    """Returns (decision, check_name, one-sentence reason). First failing check decides."""
    name = _norm(quote.vendor) if quote.vendor else None
    if name is None:
        return "do_not_recommend", "approved vendor", "The approved-vendor check fails because the document does not name a vendor."
    if name in hb.removed:
        return (
            "do_not_recommend",
            "approved vendor",
            f"The approved-vendor check fails because {quote.vendor} is on the handbook's removed or pending list.",
        )
    if name not in hb.approved:
        return (
            "do_not_recommend",
            "approved vendor",
            f"The approved-vendor check fails because {quote.vendor} is not on the approved vendor list.",
        )
    cap = hb.max_cost.get(quote.sku) if quote.sku else None
    if quote.unit_price is None or cap is None:
        missing = "unit price" if quote.unit_price is None else f"catalogue max unit cost for SKU {quote.sku}"
        return "do_not_recommend", "price", f"The price check fails because the {missing} is not known."
    if quote.unit_price > cap:
        return (
            "do_not_recommend",
            "price",
            f"The price check fails because the quoted ${quote.unit_price:.2f} is above the ${cap:.2f} max unit cost for {quote.sku}.",
        )
    if quote.lead_time_days is None:
        return "do_not_recommend", "lead time", "The lead-time check fails because the document does not state a lead time."
    if quote.lead_time_days > hb.max_lead_days:
        return (
            "do_not_recommend",
            "lead time",
            f"The lead-time check fails because {quote.lead_time_days} days is over the {hb.max_lead_days}-day limit.",
        )
    if quote.min_order is None:
        return "do_not_recommend", "minimum order", "The minimum-order check fails because the document does not state a minimum order."
    if quote.min_order > hb.max_min_order:
        return (
            "do_not_recommend",
            "minimum order",
            f"The minimum-order check fails because {quote.min_order} units is over the {hb.max_min_order}-unit limit.",
        )
    return (
        "recommend",
        "all four",
        f"All four checks pass: {quote.vendor} is approved, ${quote.unit_price:.2f} is within the ${cap:.2f} cap, "
        f"{quote.lead_time_days} days is within {hb.max_lead_days}, and {quote.min_order} units is within {hb.max_min_order}.",
    )


# ---------------------------------------------------------------- reason hygiene

_LINK_PATTERNS = [
    re.compile(r"\[[^\]]*\]\([^)]*\)"),  # markdown link
    re.compile(r"https?://\S+", re.I),
    re.compile(r"\bwww\.\S+", re.I),
    re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),  # email address
    re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b(?:/\S*)?", re.I),  # bare domain
]


def scrub_reason(reason: str) -> str:
    for pattern in _LINK_PATTERNS:
        reason = pattern.sub("", reason)
    return re.sub(r"\s{2,}", " ", reason).strip()


# ---------------------------------------------------------------- put it together


def finalize(model_answer: VendorQuote, document_text: str, hb: Handbook | None = None) -> tuple[VendorQuote, dict[str, Any]]:
    """Verify the model's facts, compute the decision in Python, and tidy the reason."""
    hb = hb or load_handbook()
    verified, problems = verify_facts(model_answer, document_text)
    decision, check, python_reason = decide(verified, hb)

    scrubbed = scrub_reason(model_answer.reason)
    keep_model_reason = decision == model_answer.decision and not problems and len(scrubbed) >= 10
    reason = scrubbed if keep_model_reason else python_reason
    final = verified.model_copy(update={"decision": decision, "reason": reason})
    notes = {
        "model_answer": model_answer.model_dump(),
        "unverified_facts_removed": problems,
        "python_decision": decision,
        "deciding_check": check,
        "decision_overridden": decision != model_answer.decision,
        "reason_source": "model" if keep_model_reason else "python",
        "reason_had_links_stripped": scrubbed != model_answer.reason,
    }
    return final, notes
