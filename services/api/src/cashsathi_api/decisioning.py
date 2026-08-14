from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol, cast

from google import genai
from google.genai import types
from pydantic import ValidationError

from cashsathi_api.config import Settings
from cashsathi_api.domain import Action, Invoice, InvoiceState, ModelDecision


@dataclass(frozen=True, slots=True)
class DecisionOutput:
    proposal: ModelDecision
    attempt_count: int
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


class DecisionAdapter(Protocol):
    model_id: str
    prompt_version: str

    def decide(
        self, invoice: Invoice, state: InvoiceState, actions: list[Action]
    ) -> DecisionOutput: ...


class DecisionSchemaFailure(Exception):
    def __init__(self, attempt_count: int, latency_ms: int) -> None:
        super().__init__("Gemini returned two invalid structured decisions")
        self.attempt_count = attempt_count
        self.latency_ms = latency_ms


class DecisionTransportFailure(Exception):
    def __init__(self, latency_ms: int) -> None:
        super().__init__("Gemini decision request failed")
        self.latency_ms = latency_ms


class DecisionUnavailableError(Exception):
    pass


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
        self, invoice: Invoice, state: InvoiceState, actions: list[Action]
    ) -> DecisionOutput:
        facts = {
            "invoice_id": invoice.id,
            "customer_id": invoice.customer.id,
            "amount_minor": invoice.amount_minor,
            "currency": invoice.currency,
            "issue_date": invoice.issue_date.isoformat() if invoice.issue_date else None,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "state": state.value,
            "manual_only": invoice.customer.manual_only,
            "action_history": [
                {
                    "type": action.action_type,
                    "state": action.state.value,
                    "created_at": action.created_at.isoformat(),
                }
                for action in actions[-20:]
            ],
        }
        prompt = (
            "Choose exactly one safe receivables decision from the schema. Do not write an email, "
            "make legal claims, infer payment, or add facts. For SEND_REMINDER choose only a "
            "WARM or NEUTRAL reminder tone and a due-state reminder intent; deterministic code "
            "will render the message. Keep rationale concise and internal. "
            f"Decision facts: {json.dumps(facts, separators=(',', ':'))}"
        )
        started = time.perf_counter()
        last_schema_error: Exception | None = None
        for attempt in (1, 2):
            try:
                response = self._client.models.generate_content(
                    model=self.model_id,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=ModelDecision,
                        temperature=0,
                    ),
                )
                if isinstance(response.parsed, ModelDecision):
                    proposal = response.parsed
                elif response.text:
                    proposal = ModelDecision.model_validate_json(response.text)
                else:
                    raise ValueError("empty structured response")
                usage = response.usage_metadata
                return DecisionOutput(
                    proposal=proposal,
                    attempt_count=attempt,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    input_tokens=cast(int | None, getattr(usage, "prompt_token_count", None)),
                    output_tokens=cast(int | None, getattr(usage, "candidates_token_count", None)),
                )
            except (ValidationError, ValueError, TypeError) as exc:
                last_schema_error = exc
                continue
            except Exception as exc:
                raise DecisionTransportFailure(
                    round((time.perf_counter() - started) * 1000)
                ) from exc
        raise DecisionSchemaFailure(2, round((time.perf_counter() - started) * 1000)) from (
            last_schema_error
        )
