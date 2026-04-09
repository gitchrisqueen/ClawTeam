"""
ClawTeam structured task classifier.

Provides classify_task() — sends an incoming task description to the
tools-standard model with a BacklogItem response_format, returning a
validated BacklogItem Pydantic object.

Feature flag: structured_outputs.schemas.BacklogItem.enabled in
~/.clawteam/config.json. When disabled, falls back to returning a
minimal BacklogItem with owner='leader' and priority='normal' so the
caller can apply its own heuristic routing.

Stage rollout: this file is wired in Stage 2 of the structured-outputs
rollout plan. The function is safe to call now — it reads the feature
flag and falls back gracefully if disabled.

Rollback: set structured_outputs.schemas.BacklogItem.enabled = false
in ~/.clawteam/config.json. No code changes or restarts required.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

try:
    _cqc_scripts = '/home/quin/scripts'
    if _cqc_scripts not in sys.path:
        sys.path.insert(0, _cqc_scripts)
    from cqc_posthog import ph, SYSTEM, set_context_tags
    _POSTHOG_AVAILABLE = True
except ImportError:
    _POSTHOG_AVAILABLE = False
    class _NoopPH:
        def capture(self, *a, **kw): pass
        def capture_exception(self, *a, **kw): pass
        def flush(self, *a, **kw): pass
        def new_context(self): return __import__('contextlib').nullcontext()
        def identify_context(self, *a): pass
    ph = _NoopPH()
    SYSTEM = 'system:cqc'
    def set_context_tags(tags: dict) -> None: pass

from clawteam.schemas.v1 import BACKLOG_ITEM_RESPONSE_FORMAT, BacklogItem

CLAWTEAM_CONFIG = Path.home() / ".clawteam" / "config.json"

# Model alias used for classification calls (maps to t3-ministral-14b via litellm alias)
CLASSIFIER_MODEL = os.environ.get("CLAWTEAM_CLASSIFIER_MODEL", "litellm/tools-standard")

CLASSIFIER_SYSTEM_PROMPT = """\
You are a ClawTeam task classifier. Given an incoming task description, return a
structured BacklogItem JSON that assigns the task to the correct owner and priority.

Owner values (pick exactly one):
  revenue   — client revenue, proposals, invoicing, sales
  platform  — infrastructure, code, CQC platform, developer tooling, webhooks
  projects  — project management, client delivery, timelines, coordination
  research  — research, analysis, discovery, competitive intelligence
  finance   — financial reporting, bookkeeping, expenses, payroll
  personal  — personal / life admin for Chris
  leader    — strategy, multi-domain decisions, unclear ownership → escalate to leader

Priority values: urgent | high | normal | low

Rules:
- If unclear, assign to leader with priority normal.
- next_action must be a concrete, actionable sentence.
- tags: copy relevant tags from the source task (lowercase, no spaces).
- Do NOT invent information not present in the task description.
"""


def _structured_outputs_enabled() -> bool:
    """Check if BacklogItem structured output is enabled in config."""
    try:
        cfg = json.loads(CLAWTEAM_CONFIG.read_text()) if CLAWTEAM_CONFIG.exists() else {}
        so = cfg.get("structured_outputs", {})
        if not so.get("enabled", True):
            return False
        schema_flags = so.get("schemas", {})
        backlog_flags = schema_flags.get("BacklogItem", {})
        return backlog_flags.get("enabled", True)
    except Exception:
        return False


def classify_task(
    task_text: str,
    litellm_client: Any,
    *,
    team_name: str = "cu-clickup-triage",
) -> BacklogItem:
    """
    Classify an incoming task description into a structured BacklogItem.

    Args:
        task_text: Raw task text (subject + description + tags, as a single string).
        litellm_client: A litellm module or compatible client (must have .completion()).
        team_name: ClawTeam name for per-team flag lookup.

    Returns:
        BacklogItem — always returns a valid object. Falls back to defaults on error.
    """
    with ph.new_context():
        ph.identify_context(SYSTEM)
        set_context_tags({"component": "clawteam_classify"})

        if not _structured_outputs_enabled():
            # Graceful degradation: return a minimal item with leader ownership
            return BacklogItem(
                subject=task_text[:120],
                description=task_text,
                owner="leader",
                priority="normal",
                next_action="Review and assign to correct owner.",
            )

        t0 = time.monotonic()
        try:
            response = litellm_client.completion(
                model=CLASSIFIER_MODEL,
                messages=[
                    {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
                    {"role": "user", "content": task_text},
                ],
                response_format=BACKLOG_ITEM_RESPONSE_FORMAT,
                # No caching for classification — task content is always unique
                metadata={"no-cache": True},
            )
            content = response.choices[0].message.content
            result = BacklogItem.model_validate_json(content)
            _latency_ms = round((time.monotonic() - t0) * 1000, 1)

            ph.capture(
                "task_classified",
                distinct_id=SYSTEM,
                properties={
                    "task_type": result.owner,
                    "routed_to_tier": getattr(result, "priority", None),
                    "classification_latency_ms": _latency_ms,
                    "team_name": team_name,
                },
            )

            return result

        except Exception as exc:
            # Capture exception to PostHog Error Tracking
            ph.capture_exception(exc, distinct_id=SYSTEM, properties={
                "component": "clawteam_classify",
                "team_name": team_name,
                "model": CLASSIFIER_MODEL,
            })
            # Log and fall back — never raise from a classifier; triage must continue
            print(f"[classify_task] structured classification failed ({exc!r}); falling back to defaults")
            return BacklogItem(
                subject=task_text[:120],
                description=task_text,
                owner="leader",
                priority="normal",
                next_action="Structured classification failed — review and assign manually.",
            )
