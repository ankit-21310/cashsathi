from __future__ import annotations

import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import Any, Protocol, cast

import structlog
from google import genai
from google.genai import types
from pydantic import ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from cashsathi_api.config import Settings
from cashsathi_api.domain import (
    Confidence,
    ExtractedInvoiceDraft,
    ExtractionWarning,
)
from cashsathi_api.errors import ApiError
from cashsathi_api.money import decimal_to_minor


@dataclass(frozen=True, slots=True)
class ValidatedPdf:
    data: bytes
    page_count: int
    byte_count: int


@dataclass(frozen=True, slots=True)
class ExtractionOutput:
    draft: ExtractedInvoiceDraft
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None


class InvoiceExtractor(Protocol):
    model_id: str
    prompt_version: str

    def extract(self, pdf: ValidatedPdf) -> ExtractionOutput: ...


class ExtractionUnavailableError(Exception):
    pass


_CONFIDENCE_FIELDS = (
    "invoice_number",
    "customer_name",
    "customer_email",
    "amount_decimal",
    "currency",
    "issue_date",
    "due_date",
    "payment_terms",
)


def gemini_extraction_schema() -> dict[str, Any]:
    """Return a Gemini Developer API compatible structured-output schema.

    Pydantic represents ``dict[str, Confidence]`` with ``additionalProperties``,
    which the Developer API rejects before sending the request. Confidence keys
    are intentionally constrained to the invoice fields the product displays.
    """

    schema = ExtractedInvoiceDraft.model_json_schema()
    schema["properties"]["confidence"] = {
        "type": "object",
        "title": "Confidence",
        "properties": {
            field_name: {
                "type": "string",
                "enum": [confidence.value for confidence in Confidence],
            }
            for field_name in _CONFIDENCE_FIELDS
        },
    }
    return schema


def validate_pdf(
    *, filename: str | None, content_type: str | None, data: bytes, settings: Settings
) -> ValidatedPdf:
    suffix = PurePath(filename or "").suffix.lower()
    if suffix != ".pdf":
        raise ApiError(415, "invalid_file_extension", "Upload a file with a .pdf extension.")
    if (content_type or "").split(";", 1)[0].strip().lower() != "application/pdf":
        raise ApiError(415, "invalid_mime_type", "Upload a PDF with application/pdf MIME type.")
    if len(data) > settings.max_pdf_bytes:
        raise ApiError(413, "file_too_large", "PDF files may not exceed 10 MiB.")
    if not data.startswith(b"%PDF-"):
        raise ApiError(422, "invalid_pdf_signature", "The uploaded file is not a valid PDF.")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ApiError(422, "encrypted_pdf", "Encrypted PDFs are not supported.")
        page_count = len(reader.pages)
    except ApiError:
        raise
    except (PdfReadError, ValueError, TypeError, OSError):
        raise ApiError(422, "malformed_pdf", "The PDF is malformed or unreadable.") from None
    if page_count < 1:
        raise ApiError(422, "empty_pdf", "The PDF must contain at least one page.")
    if page_count > settings.max_pdf_pages:
        raise ApiError(
            422,
            "too_many_pages",
            f"PDF files may contain at most {settings.max_pdf_pages} pages.",
        )
    return ValidatedPdf(data=data, page_count=page_count, byte_count=len(data))


def normalize_extraction(draft: ExtractedInvoiceDraft) -> ExtractedInvoiceDraft:
    warnings = list(draft.warnings)
    required = {
        "invoice_number": draft.invoice_number,
        "customer_name": draft.customer_name,
        "amount_decimal": draft.amount_decimal,
        "currency": draft.currency,
    }
    for field_name, value in required.items():
        if value is None:
            warnings.append(
                ExtractionWarning(
                    code="missing_field",
                    field=field_name,
                    message="This field was not found. Review and enter it before confirmation.",
                )
            )
    if draft.due_date is None:
        warnings.append(
            ExtractionWarning(
                code="missing_due_date",
                field="due_date",
                message="No due date was found; this invoice will require human review.",
            )
        )
    if draft.issue_date and draft.due_date and draft.due_date < draft.issue_date:
        warnings.append(
            ExtractionWarning(
                code="inconsistent_dates",
                field="due_date",
                message=(
                    "The due date is earlier than the issue date. Correct it before confirmation."
                ),
            )
        )
    if draft.amount_decimal and draft.currency:
        try:
            decimal_to_minor(draft.amount_decimal, draft.currency)
        except ApiError as exc:
            warnings.append(
                ExtractionWarning(code=exc.code, field="amount_decimal", message=exc.message)
            )
    for field_name, confidence in draft.confidence.items():
        if confidence == Confidence.LOW:
            warnings.append(
                ExtractionWarning(
                    code="low_confidence",
                    field=field_name,
                    message=(
                        "Gemini marked this field as low confidence. Verify it against the PDF."
                    ),
                )
            )
    unique: dict[tuple[str, str | None], ExtractionWarning] = {}
    for warning in warnings:
        unique[(warning.code, warning.field)] = warning
    return draft.model_copy(update={"warnings": list(unique.values())})


class GeminiInvoiceExtractor:
    def __init__(self, settings: Settings) -> None:
        if (
            settings.gemini_api_key is None
            or not settings.gemini_api_key.get_secret_value().strip()
        ):
            raise ExtractionUnavailableError("Gemini is not configured")
        self.model_id = settings.gemini_model
        self.prompt_version = settings.extraction_prompt_version
        self._client = genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(timeout=settings.gemini_timeout_seconds * 1000),
        )

    def extract(self, pdf: ValidatedPdf) -> ExtractionOutput:
        prompt = (
            "Extract only facts visible in this invoice. Never infer or fabricate missing values. "
            "Use ISO dates and an ISO 4217 currency code. Preserve the printed decimal amount. "
            "Set absent values to null and add concise warnings for uncertainty or inconsistency."
        )
        started = time.perf_counter()
        schema_error: Exception | None = None
        transport_error: Exception | None = None
        response = None
        draft = None
        for _attempt in (1, 2):
            try:
                response = self._client.models.generate_content(
                    model=self.model_id,
                    contents=[
                        types.Part.from_bytes(data=pdf.data, mime_type="application/pdf"),
                        prompt,
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=gemini_extraction_schema(),
                        temperature=0,
                    ),
                )
                parsed = response.parsed
                if isinstance(parsed, ExtractedInvoiceDraft):
                    draft = parsed
                elif response.text:
                    draft = ExtractedInvoiceDraft.model_validate_json(response.text)
                else:
                    raise ValueError("empty structured response")
                break
            except (ValidationError, ValueError, TypeError) as exc:
                schema_error = exc
            except Exception as exc:
                transport_error = exc
        if draft is None or response is None:
            structlog.get_logger("gemini").warning(
                "invoice_extraction_attempts_exhausted",
                category="schema_or_model_failure",
                attempts=2,
                failure_kind="schema" if schema_error is not None else "transport",
            )
            if schema_error is not None:
                raise ApiError(
                    502,
                    "invalid_extraction_response",
                    (
                        "Gemini returned an invalid extraction. Try again or enter the "
                        "invoice manually."
                    ),
                ) from schema_error
            raise ExtractionUnavailableError("Gemini extraction failed") from transport_error
        usage = response.usage_metadata
        return ExtractionOutput(
            draft=normalize_extraction(draft),
            latency_ms=round((time.perf_counter() - started) * 1000),
            input_tokens=cast(int | None, getattr(usage, "prompt_token_count", None)),
            output_tokens=cast(int | None, getattr(usage, "candidates_token_count", None)),
        )
