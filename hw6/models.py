"""Data shapes for the vendor desk agent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class VendorQuote(BaseModel):
    """What the agent returns for one vendor document."""

    vendor: str | None = Field(None, description="Vendor name as written in the document; null if not stated.")
    sku: str | None = Field(None, description="Campus Customs SKU, e.g. CC-HD-006; null if not stated.")
    unit_price: float | None = Field(None, description="Quoted price per unit in dollars; null if not stated.")
    lead_time_days: int | None = Field(None, description="Lead time in days, number only; null if not stated.")
    min_order: int | None = Field(None, description="Minimum order in units; null if not stated.")
    decision: Literal["recommend", "do_not_recommend"]
    reason: str = Field(description="One sentence naming the handbook purchasing check that decided it.")


class RequestUsageEntry(BaseModel):
    """Token counts the API reported for ONE model request, and what that request cost."""

    request_number: int
    instructions_start: str
    input_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float


class UsageEntry(BaseModel):
    """One row of output/usage_log.json: per-request usage plus the per-run total."""

    document: str
    time: str
    run_id: str
    setup: str
    cache_busted: bool
    layers: list[str] = Field(default_factory=list)
    requests: int
    input_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float
    per_request: list[RequestUsageEntry] = Field(default_factory=list)


class ToolCallRecord(BaseModel):
    tool: str
    arguments: dict[str, Any]
    result: str


class AuditEntry(BaseModel):
    """One row of output/audit_trail.json: what the agent did and what it answered."""

    document: str
    time: str
    run_id: str
    setup: str
    tool_calls: list[ToolCallRecord]
    final_answer: dict[str, Any] | None
    layers: list[str] = Field(default_factory=list)
    defense: dict[str, Any] | None = None
    error: str | None = None
