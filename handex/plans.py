from __future__ import annotations

import json
from typing import Any

from .context import redact_text


VALID_PLAN_STATUSES = {"pending", "in_progress", "completed"}


class PlanError(ValueError):
    pass


def compact(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated by Handex plan]..."


def normalize_status(value: Any) -> str:
    status = str(value or "pending").strip().lower().replace("-", "_")
    if status not in VALID_PLAN_STATUSES:
        raise PlanError(f"Unsupported plan status: {value}")
    return status


def normalize_plan_item(item: Any) -> dict[str, str]:
    if isinstance(item, str):
        step = item
        status = "pending"
    elif isinstance(item, dict):
        step = str(item.get("step") or item.get("task") or "").strip()
        status = normalize_status(item.get("status") or "pending")
    else:
        raise PlanError("Each plan item must be an object or string")
    step = compact(redact_text(step), 500)
    if not step:
        raise PlanError("Plan item step is required")
    return {"step": step, "status": status}


def normalize_plan_payload(payload: Any) -> tuple[str, list[dict[str, str]]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PlanError(f"Plan JSON is invalid: {exc}") from exc
    if isinstance(payload, list):
        explanation = ""
        raw_items = payload
    elif isinstance(payload, dict):
        explanation = compact(redact_text(str(payload.get("explanation") or "")), 1000)
        raw_items = payload.get("plan")
        if raw_items is None:
            raw_items = payload.get("items", [])
    else:
        raise PlanError("Plan payload must be a JSON object or array")
    if not isinstance(raw_items, list):
        raise PlanError("Plan payload field 'plan' must be a list")
    if len(raw_items) > 50:
        raise PlanError("Plan can contain at most 50 items")
    items = [normalize_plan_item(item) for item in raw_items]
    in_progress = [item for item in items if item["status"] == "in_progress"]
    if len(in_progress) > 1:
        raise PlanError("Plan can contain at most one in_progress item")
    return explanation, items


def serializable_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "explanation": redact_text(str(plan.get("explanation") or "")),
        "updated_at": plan.get("updated_at") or "",
        "plan": [
            {
                "step": redact_text(str(item.get("step") or "")),
                "status": normalize_status(item.get("status") or "pending"),
            }
            for item in plan.get("items", [])
            if str(item.get("step") or "").strip()
        ],
    }


def plan_form_json(plan: dict[str, Any]) -> str:
    return json.dumps(serializable_plan(plan), ensure_ascii=False, indent=2)


def plan_markdown(plan: dict[str, Any]) -> str:
    payload = serializable_plan(plan)
    lines = []
    if payload["explanation"]:
        lines.extend([payload["explanation"], ""])
    if not payload["plan"]:
        return "No active plan."
    for item in payload["plan"]:
        lines.append(f"- [{item['status']}] {item['step']}")
    return "\n".join(lines)
