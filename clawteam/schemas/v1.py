"""
ClawTeam structured output schemas — v1.

Used as `response_format` payloads when calling LiteLLM for machine-consumed
outputs (classification, coordination, health checks). NOT used for freeform
agent outputs (compaction summaries, narrative responses, brainstorming).

Feature flags controlling which schemas are active are in ~/.clawteam/config.json
under `structured_outputs`. See STRUCTURED_OUTPUTS_README in this package.

Version history:
  v1 — 2026-03-31: Initial schemas (ToolInvocation, PlanStep, Plan,
                    BacklogItem, HealthCheckResult, BenchSummary)

Migration: v1 → v2 will add union types. Old disk files with no schema_version
field are treated as "legacy" and migrated on first write.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── ToolInvocation v1 ────────────────────────────────────────────────────────

class ToolInvocation(BaseModel):
    """Structured tool call envelope for agent-to-agent dispatch.

    Use when an LLM must select a tool by name and populate its args without
    using the native tool_calls mechanism (e.g. SIMPLE/MEDIUM tier, or when
    passing tool dispatch as a coordination message body).
    """

    schema_version: Literal["v1"] = "v1"
    tool_name: str = Field(..., description="Exact tool identifier as registered in the agent")
    args: dict[str, Any] = Field(default_factory=dict, description="Tool arguments, validated against the tool's own schema")
    rationale: str = Field("", description="One-sentence justification for choosing this tool")


# ── Plan / PlanStep v1 ───────────────────────────────────────────────────────

class StepStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    blocked = "blocked"
    skipped = "skipped"


class PlanStep(BaseModel):
    """A single step in a machine-consumed execution plan.

    Only used for automation-driven plan files — not for conversational plans
    shown to the user. STRUCTURED_OUTPUTS.Plan must be enabled in config to
    activate response_format enforcement.
    """

    schema_version: Literal["v1"] = "v1"
    step_id: str = Field(..., description="Short kebab-case identifier, e.g. 'setup-db'")
    description: str = Field(..., description="What this step does")
    owner: str = Field("", description="ClawTeam role name, e.g. 'platform', 'revenue'")
    depends_on: list[str] = Field(default_factory=list, description="step_ids that must complete first")
    success_criteria: str = Field("", description="Measurable condition for this step to be marked done")
    status: StepStatus = StepStatus.pending
    estimated_minutes: Optional[int] = Field(None, description="Optional time estimate")


class Plan(BaseModel):
    """A machine-consumed execution plan composed of ordered steps."""

    schema_version: Literal["v1"] = "v1"
    goal: str = Field(..., description="What the plan achieves in one sentence")
    steps: list[PlanStep] = Field(..., min_length=1)
    context: str = Field("", description="Additional context or constraints")


# ── BacklogItem v1 ───────────────────────────────────────────────────────────

class BacklogItem(BaseModel):
    """ClawTeam task classification output.

    Produced by the cu-clickup-triage classifier when assigning an incoming
    task to an owner and priority. Maps directly to TaskItem on disk (the
    existing Pydantic model in clawteam/team/models.py).

    This is the highest-value structured output target — a misclassification
    propagates to 4-5 downstream agents.
    """

    schema_version: Literal["v1"] = "v1"
    subject: str = Field(..., description="Short human-readable task title")
    description: str = Field("", description="Full task context, acceptance criteria, links")
    priority: Literal["urgent", "high", "normal", "low"] = Field(
        "normal", description="Task priority"
    )
    owner: str = Field(
        ...,
        description="ClawTeam role name: 'revenue', 'platform', 'projects', 'research', 'finance', 'personal', or 'leader'",
    )
    status: Literal["pending", "in_progress", "completed", "blocked"] = "pending"
    next_action: str = Field("", description="Concrete next step for the owner")
    tags: list[str] = Field(default_factory=list, description="Relevant tags from the source task")


# ── HealthCheckResult v1 ─────────────────────────────────────────────────────

class ServiceStatus(str, Enum):
    healthy = "healthy"
    degraded = "degraded"
    down = "down"


class HealthCheckResult(BaseModel):
    """Result of a system health check, consumed by monitoring gates and Discord alerts.

    Used by structured_output_health_gate.py and any LLM-generated health
    summary. score is 0–100 (matches bench KPI convention).
    """

    schema_version: Literal["v1"] = "v1"
    service: str = Field(..., description="Service or component name, e.g. 'cqc-litellm', 'redis', 'mem0'")
    status: ServiceStatus
    score: float = Field(..., ge=0.0, le=100.0, description="Health score 0–100")
    issues: list[str] = Field(default_factory=list, description="Active issues or warnings")
    recommendations: list[str] = Field(default_factory=list, description="Suggested remediation steps")
    timestamp_utc: str = Field("", description="ISO 8601 UTC timestamp")


# ── BenchSummary v1 ──────────────────────────────────────────────────────────

class BenchSummary(BaseModel):
    """Structured summary of a benchmark run, extending the existing bench JSON format.

    Adds schema_validity_rate and repair_rate as first-class KPIs.
    These are populated by Suites 18–20 (added in structured-outputs Stage 1).
    """

    schema_version: Literal["v1"] = "v1"
    benchmark_version: str
    timestamp: str
    overall_score: float = Field(..., ge=0.0, le=100.0)
    pass_count: int
    fail_count: int
    schema_validity_rate: float = Field(
        100.0, ge=0.0, le=100.0,
        description="Percentage of structured output requests that returned valid schema-compliant JSON on first attempt"
    )
    repair_rate: float = Field(
        0.0, ge=0.0, le=100.0,
        description="Percentage of structured output requests that triggered a repair retry or fallback"
    )
    issues: list[str] = Field(default_factory=list)
    auto_structured_disabled: list[str] = Field(
        default_factory=list,
        description="Schema names auto-disabled by the health gate due to sustained failures"
    )


# ── ClawTeam Runtime Schemas v1 ──────────────────────────────────────────────

class ClawTeamSpawnResult(BaseModel):
    """Result of spawning a ClawTeam via webhook or CLI."""

    schema_version: Literal["v1"] = "v1"
    team_name: str
    template: str
    session_key: str = ""
    tmux_session: str = ""
    spawned_at: str
    success: bool
    error: Optional[str] = None


class ClawTeamTaskReport(BaseModel):
    """Report from a single team member agent on task completion."""

    schema_version: Literal["v1"] = "v1"
    task_id: str
    team_name: str
    agent_name: str
    status: Literal["pending", "in_progress", "completed", "failed"]
    summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    completed_at: Optional[str] = None


class ClawTeamWorkflowResult(BaseModel):
    """End-to-end result of a ClawTeam workflow triggered by a ClickUp webhook."""

    schema_version: Literal["v1"] = "v1"
    team_name: str
    template: str
    clickup_task_id: str
    corr_id: str
    started_at: str
    completed_at: Optional[str] = None
    status: Literal["running", "completed", "timeout", "aborted"]
    terminal_reason: Optional[str] = None
    tasks_total: int = 0
    tasks_done: int = 0
    deliverables: list[str] = Field(default_factory=list)


class BenchmarkScenarioResult(BaseModel):
    """Result of a single ClawTeam benchmark scenario."""

    schema_version: Literal["v1"] = "v1"
    scenario: str
    team_name: str
    spawn_success: bool
    first_task_in_progress_seconds: Optional[float] = None
    all_tasks_done_seconds: Optional[float] = None
    passed: bool
    error: Optional[str] = None
    ran_at: str


# ── JSON Schema exports for LiteLLM response_format ─────────────────────────
# Pre-computed at import time so they can be referenced directly in completion()
# calls without re-generating on every request.

BACKLOG_ITEM_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "BacklogItem",
        "schema": BacklogItem.model_json_schema(),
        "strict": True,
    },
}

HEALTH_CHECK_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "HealthCheckResult",
        "schema": HealthCheckResult.model_json_schema(),
        "strict": True,
    },
}

PLAN_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "Plan",
        "schema": Plan.model_json_schema(),
        "strict": False,  # soft — log failures, don't block
    },
}

TOOL_INVOCATION_RESPONSE_FORMAT: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "ToolInvocation",
        "schema": ToolInvocation.model_json_schema(),
        "strict": False,
    },
}
