from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, cast

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from cashsathi_api.config import Settings
from cashsathi_api.domain import (
    Action,
    AgentDecision,
    Invoice,
    InvoiceState,
    ModelDecision,
    PolicyDefaults,
    ReminderIntent,
    ReminderTone,
)

ALLOWED_FUNCTIONS = (
    "wait",
    "send_payment_reminder",
    "schedule_recheck",
    "request_human_review",
    "close_as_paid",
)
DecisionReason = Literal[
    "DUE_DATE_NOT_REACHED",
    "COOLDOWN_ACTIVE",
    "RECENT_CUSTOMER_RESPONSE",
    "SAFE_RECHECK",
    "OVERDUE_ELIGIBLE",
    "MISSING_DATA",
    "DISPUTE_REPORTED",
    "PAYMENT_REPORTED_UNVERIFIED",
    "OWNER_CONFIRMED_PAYMENT",
]
RiskFlag = Literal[
    "LEGAL_LANGUAGE",
    "PAYMENT_UNCONFIRMED",
    "CUSTOMER_DISPUTE",
    "MISSING_DATA",
    "HIGH_VALUE",
]


class _FunctionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: str = Field(min_length=1, max_length=100)


class _WaitArguments(_FunctionArguments):
    reason_code: DecisionReason
    next_check_at: datetime

    @field_validator("next_check_at")
    @classmethod
    def next_check_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("next_check_at must include a timezone")
        return value.astimezone(UTC)


class _ReminderArguments(_FunctionArguments):
    tone: ReminderTone
    intent: ReminderIntent
    risk_flags: list[RiskFlag] = Field(default_factory=list, max_length=10)


class _HumanReviewArguments(_FunctionArguments):
    reason_code: DecisionReason
    risk_flags: list[RiskFlag] = Field(default_factory=list, max_length=10)


class _ClosePaidArguments(_FunctionArguments):
    reason_code: Literal["OWNER_CONFIRMED_PAYMENT"]


_REASON_TEXT: dict[str, str] = {
    "DUE_DATE_NOT_REACHED": "The confirmed due date has not been reached.",
    "COOLDOWN_ACTIVE": "A prior reminder is still inside the configured cooldown.",
    "RECENT_CUSTOMER_RESPONSE": "A recent customer response requires waiting.",
    "SAFE_RECHECK": "The invoice should be checked again at the proposed time.",
    "OVERDUE_ELIGIBLE": "The invoice is eligible for a deterministic payment reminder.",
    "MISSING_DATA": "Required invoice data is missing or unresolved.",
    "DISPUTE_REPORTED": "A dispute requires human review.",
    "PAYMENT_REPORTED_UNVERIFIED": "A reported payment has not been owner-confirmed.",
    "OWNER_CONFIRMED_PAYMENT": "Owner-confirmed payments cover the invoice.",
}


def _object_schema(properties: dict[str, dict[str, Any]], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "invoice_id": {
                "type": "string",
                "description": "The exact invoice_id supplied in decision facts.",
            },
            **properties,
        },
        "required": ["invoice_id", *required],
        "additionalProperties": False,
    }


_REASON_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": list(_REASON_TEXT),
}
_RISK_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "string",
        "enum": [
            "LEGAL_LANGUAGE",
            "PAYMENT_UNCONFIRMED",
            "CUSTOMER_DISPUTE",
            "MISSING_DATA",
            "HIGH_VALUE",
        ],
    },
    "maxItems": 10,
}
FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name="wait",
        description="Wait without creating or sending a reminder, then check at a bounded time.",
        parameters_json_schema=_object_schema(
            {
                "reason_code": _REASON_SCHEMA,
                "next_check_at": {"type": "string", "format": "date-time"},
            },
            ["reason_code", "next_check_at"],
        ),
    ),
    types.FunctionDeclaration(
        name="send_payment_reminder",
        description=(
            "Propose a deterministic payment reminder. Select only tone and intent; never "
            "provide message text or send an email."
        ),
        parameters_json_schema=_object_schema(
            {
                "tone": {"type": "string", "enum": ["WARM", "NEUTRAL"]},
                "intent": {
                    "type": "string",
                    "enum": ["DUE_SOON", "DUE_TODAY", "OVERDUE_FOLLOWUP"],
                },
                "risk_flags": _RISK_SCHEMA,
            },
            ["tone", "intent", "risk_flags"],
        ),
    ),
    types.FunctionDeclaration(
        name="schedule_recheck",
        description="Schedule a bounded future invoice check without creating an action.",
        parameters_json_schema=_object_schema(
            {
                "reason_code": _REASON_SCHEMA,
                "next_check_at": {"type": "string", "format": "date-time"},
            },
            ["reason_code", "next_check_at"],
        ),
    ),
    types.FunctionDeclaration(
        name="request_human_review",
        description="Pause automation when facts, dispute, payment, or safety need human review.",
        parameters_json_schema=_object_schema(
            {"reason_code": _REASON_SCHEMA, "risk_flags": _RISK_SCHEMA},
            ["reason_code", "risk_flags"],
        ),
    ),
    types.FunctionDeclaration(
        name="close_as_paid",
        description=(
            "Propose closure only when decision facts show owner-confirmed payments cover the "
            "full invoice. Policy code independently verifies this."
        ),
        parameters_json_schema=_object_schema(
            {
                "reason_code": {
                    "type": "string",
                    "enum": ["OWNER_CONFIRMED_PAYMENT"],
                }
            },
            ["reason_code"],
        ),
    ),
]


@dataclass(frozen=True, slots=True)
class DecisionOutput:
    proposal: ModelDecision
    attempt_count: int
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    function_call_id: str
    proposed_function: str
    function_arguments: dict[str, Any]


class DecisionAdapter(Protocol):
    model_id: str
    prompt_version: str

    def decide(
        self,
        invoice: Invoice,
        state: InvoiceState,
        actions: list[Action],
        policy: PolicyDefaults,
    ) -> DecisionOutput: ...


class DecisionSchemaFailure(Exception):
    def __init__(self, attempt_count: int, latency_ms: int) -> None:
        super().__init__("Gemini returned two invalid function calls")
        self.attempt_count = attempt_count
        self.latency_ms = latency_ms


class DecisionTransportFailure(Exception):
    def __init__(self, latency_ms: int) -> None:
        super().__init__("Gemini decision request failed")
        self.latency_ms = latency_ms


class DecisionUnavailableError(Exception):
    pass


def _function_calls(response: Any) -> list[Any]:
    calls: list[Any] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            call = getattr(part, "function_call", None)
            if call is not None:
                calls.append(call)
    return calls


def _sanitize_call_id(raw: object, name: str, arguments: dict[str, Any]) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]", "", str(raw or ""))[:128]
    if cleaned:
        return cleaned
    encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return f"call_{hashlib.sha256(f'{name}:{encoded}'.encode()).hexdigest()[:28]}"


def _parse_call(
    call: Any, invoice: Invoice, now: datetime
) -> tuple[ModelDecision, str, str, dict[str, Any]]:
    name = getattr(call, "name", None)
    raw_arguments = getattr(call, "args", None)
    if name not in ALLOWED_FUNCTIONS or not isinstance(raw_arguments, dict):
        raise ValueError("unknown function or invalid arguments")
    if name in {"wait", "schedule_recheck"}:
        parsed: BaseModel = _WaitArguments.model_validate(raw_arguments)
        wait_arguments = cast(_WaitArguments, parsed)
        if wait_arguments.next_check_at <= now or wait_arguments.next_check_at > now + timedelta(
            days=90
        ):
            raise ValueError("next_check_at is outside the allowed 90-day window")
        decision = AgentDecision.WAIT if name == "wait" else AgentDecision.SCHEDULE_RECHECK
        proposal = ModelDecision(
            decision=decision,
            rationale=_REASON_TEXT[wait_arguments.reason_code],
            next_check_at=wait_arguments.next_check_at,
        )
    elif name == "send_payment_reminder":
        parsed = _ReminderArguments.model_validate(raw_arguments)
        reminder = parsed
        proposal = ModelDecision(
            decision=AgentDecision.SEND_REMINDER,
            rationale=_REASON_TEXT["OVERDUE_ELIGIBLE"],
            risk_flags=list(reminder.risk_flags),
            reminder_tone=reminder.tone,
            reminder_intent=reminder.intent,
        )
    elif name == "request_human_review":
        parsed = _HumanReviewArguments.model_validate(raw_arguments)
        review = parsed
        proposal = ModelDecision(
            decision=AgentDecision.REQUEST_HUMAN_REVIEW,
            rationale=_REASON_TEXT[review.reason_code],
            risk_flags=list(review.risk_flags),
            requires_human_approval=True,
        )
    else:
        parsed = _ClosePaidArguments.model_validate(raw_arguments)
        proposal = ModelDecision(
            decision=AgentDecision.CLOSE_AS_PAID,
            rationale=_REASON_TEXT["OWNER_CONFIRMED_PAYMENT"],
        )
    if cast(_FunctionArguments, parsed).invoice_id != invoice.id:
        raise ValueError("function invoice_id does not match decision context")
    sanitized = parsed.model_dump(mode="json")
    return (
        proposal,
        _sanitize_call_id(getattr(call, "id", None), name, sanitized),
        name,
        sanitized,
    )


class GeminiDecisionAdapter:
    def __init__(self, settings: Settings) -> None:
        if (
            settings.gemini_api_key is None
            or not settings.gemini_api_key.get_secret_value().strip()
        ):
            raise DecisionUnavailableError("Gemini is not configured")
        self.model_id = settings.gemini_model
        self.prompt_version = settings.decision_prompt_version
        self._client = genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(timeout=settings.gemini_timeout_seconds * 1000),
        )

    def decide(
        self,
        invoice: Invoice,
        state: InvoiceState,
        actions: list[Action],
        policy: PolicyDefaults,
    ) -> DecisionOutput:
        facts = {
            "invoice_id": invoice.id,
            "customer_id": invoice.customer.id,
            "amount_minor": invoice.amount_minor,
            "verified_paid_minor": invoice.verified_paid_minor,
            "currency": invoice.currency,
            "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "state": state.value,
            "customer": {
                "manual_only": invoice.customer.manual_only,
                "locale": invoice.customer.locale.value,
            },
            "restrictive_policy": policy.model_dump(mode="json"),
            "last_20_actions": [
                {
                    "type": action.action_type,
                    "state": action.state.value,
                    "created_at": action.created_at.isoformat(),
                    "execution_completed_at": (
                        action.execution_completed_at.isoformat()
                        if action.execution_completed_at
                        else None
                    ),
                }
                for action in actions[:20]
            ],
        }
        prompt = (
            "Return exactly one allowed function call. Do not output prose, write reminder "
            "content, execute tools, make legal claims, infer payment, or add facts. For a "
            "reminder select only bounded tone, intent, and risk flags; deterministic application "
            "code renders and separately approves any message. "
            f"Decision facts: {json.dumps(facts, separators=(',', ':'))}"
        )
        started = time.perf_counter()
        last_schema_error: Exception | None = None
        last_transport_error: Exception | None = None
        for attempt in (1, 2):
            try:
                response = self._client.models.generate_content(
                    model=self.model_id,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(function_declarations=FUNCTION_DECLARATIONS)],
                        tool_config=types.ToolConfig(
                            function_calling_config=types.FunctionCallingConfig(
                                mode="ANY",
                                allowed_function_names=list(ALLOWED_FUNCTIONS),
                            )
                        ),
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                        temperature=0,
                    ),
                )
                calls = _function_calls(response)
                if len(calls) != 1:
                    raise ValueError("exactly one function call is required")
                proposal, call_id, function_name, arguments = _parse_call(
                    calls[0], invoice, datetime.now(UTC)
                )
                usage = response.usage_metadata
                return DecisionOutput(
                    proposal=proposal,
                    attempt_count=attempt,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    input_tokens=cast(int | None, getattr(usage, "prompt_token_count", None)),
                    output_tokens=cast(int | None, getattr(usage, "candidates_token_count", None)),
                    function_call_id=call_id,
                    proposed_function=function_name,
                    function_arguments=arguments,
                )
            except (ValidationError, ValueError, TypeError) as exc:
                last_schema_error = exc
                continue
            except Exception as exc:
                last_transport_error = exc
                continue
        latency_ms = round((time.perf_counter() - started) * 1000)
        if last_transport_error is not None and last_schema_error is None:
            raise DecisionTransportFailure(latency_ms) from last_transport_error
        raise DecisionSchemaFailure(2, latency_ms) from (last_schema_error or last_transport_error)
