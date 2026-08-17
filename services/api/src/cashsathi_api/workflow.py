from __future__ import annotations

import hashlib
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import structlog
from fastapi.concurrency import run_in_threadpool

from cashsathi_api.config import Settings
from cashsathi_api.decisioning import (
    DecisionAdapter,
    DecisionSchemaFailure,
    DecisionTransportFailure,
)
from cashsathi_api.domain import (
    Action,
    ActionAttempt,
    ActionCancel,
    ActionResolution,
    ActionResolve,
    ActionState,
    AgentDecision,
    AgentRun,
    AgentRunStatus,
    DataClassification,
    EvaluationResult,
    EvidenceEventType,
    Invoice,
    InvoiceState,
    InvoiceWorkflowStatus,
    MembershipRole,
    ModelDecision,
    PolicyOutcome,
    PolicyResult,
    TenantContext,
)
from cashsathi_api.errors import ApiError
from cashsathi_api.gmail import (
    GmailAdapter,
    GmailAmbiguousFailure,
    GmailDefiniteFailure,
    GmailUnavailableError,
    TokenCipher,
)
from cashsathi_api.policy import evaluate_policy
from cashsathi_api.reminders import render_reminder
from cashsathi_api.repository import Repository
from cashsathi_api.state_engine import calculate_invoice_state

KOLKATA = ZoneInfo("Asia/Kolkata")


def initial_next_check(invoice: Invoice, now: datetime | None = None) -> datetime | None:
    current = now or datetime.now(UTC)
    if invoice.due_date is None or invoice.review_required:
        return None
    due = datetime.combine(invoice.due_date, time(hour=9), KOLKATA).astimezone(UTC)
    return due if due > current else current


class CollectionWorkflow:
    def __init__(
        self,
        *,
        repository: Repository,
        decision_adapter: DecisionAdapter,
        settings: Settings,
        gmail_adapter: GmailAdapter | None,
        token_cipher: TokenCipher | None,
    ) -> None:
        self._repo = repository
        self._decision = decision_adapter
        self._settings = settings
        self._gmail = gmail_adapter
        self._cipher = token_cipher

    async def evaluate(self, tenant: TenantContext, invoice_id: str) -> EvaluationResult:
        invoice = self._repo.get_invoice(tenant, invoice_id)
        actions = self._repo.list_actions(tenant, invoice.id)
        policy_settings = self._repo.get_policy_settings(tenant)
        state = calculate_invoice_state(
            invoice, actions, cooldown_hours=policy_settings.reminder_cooldown_hours
        )
        run_id = f"run_{uuid4().hex}"
        created_at = datetime.now(UTC)
        try:
            output = await run_in_threadpool(
                self._decision.decide, invoice, state, actions, policy_settings
            )
        except DecisionSchemaFailure as failure:
            structlog.get_logger("gemini").warning(
                "decision_schema_failure", category="schema_or_model_failure"
            )
            proposal = ModelDecision(
                decision=AgentDecision.REQUEST_HUMAN_REVIEW,
                rationale="Gemini returned invalid structured output twice.",
                risk_flags=["INVALID_MODEL_OUTPUT"],
                requires_human_approval=True,
            )
            policy_result = PolicyResult(
                outcome=PolicyOutcome.BLOCK,
                final_decision=AgentDecision.REQUEST_HUMAN_REVIEW,
                matched_rules=["invalid_model_output"],
                requires_approval=True,
                next_check_at=None,
                policy_version=self._settings.policy_version,
            )
            run = AgentRun(
                id=run_id,
                invoice_id=invoice.id,
                business_id=tenant.business_id,
                status=AgentRunStatus.HUMAN_REVIEW,
                invoice_state=state,
                model_proposal=proposal,
                policy_result=policy_result,
                model_id=self._decision.model_id,
                prompt_version=self._decision.prompt_version,
                attempt_count=failure.attempt_count,
                latency_ms=failure.latency_ms,
                failure_code="invalid_model_output",
                created_at=created_at,
            )
            self._repo.save_evaluation(tenant, run, None)
            self._pause_invoice(tenant, invoice)
            return EvaluationResult(agent_run=run, action=None)
        except DecisionTransportFailure as failure:
            structlog.get_logger("gemini").error(
                "decision_transport_failure", category="schema_or_model_failure"
            )
            run = AgentRun(
                id=run_id,
                invoice_id=invoice.id,
                business_id=tenant.business_id,
                status=AgentRunStatus.FAILED,
                invoice_state=state,
                model_proposal=None,
                policy_result=None,
                model_id=self._decision.model_id,
                prompt_version=self._decision.prompt_version,
                attempt_count=1,
                latency_ms=failure.latency_ms,
                failure_code="model_transport_failure",
                created_at=created_at,
            )
            self._repo.save_evaluation(tenant, run, None)
            self._repo.update_invoice(
                tenant,
                invoice.model_copy(
                    update={
                        "next_check_at": created_at + timedelta(hours=1),
                        "evaluation_lease_until": None,
                        "updated_at": created_at,
                    }
                ),
            )
            raise ApiError(
                503,
                "decision_unavailable",
                "Agent decisioning is temporarily unavailable. No action was created.",
            ) from None

        proposal_run = AgentRun(
            id=run_id,
            invoice_id=invoice.id,
            business_id=tenant.business_id,
            status=AgentRunStatus.PROPOSED,
            invoice_state=state,
            model_proposal=output.proposal,
            policy_result=None,
            model_id=self._decision.model_id,
            prompt_version=self._decision.prompt_version,
            attempt_count=output.attempt_count,
            latency_ms=output.latency_ms,
            input_tokens=output.input_tokens,
            output_tokens=output.output_tokens,
            created_at=created_at,
            function_call_id=output.function_call_id,
            proposed_function=output.proposed_function,
            function_arguments=output.function_arguments,
        )
        self._repo.save_agent_proposal(tenant, proposal_run)
        policy_result = evaluate_policy(
            invoice=invoice,
            invoice_state=state,
            proposal=output.proposal,
            settings=policy_settings,
            actions=actions,
            policy_version=self._settings.policy_version,
        )
        action: Action | None = None
        if policy_result.final_decision == AgentDecision.SEND_REMINDER:
            action, policy_result = self._build_action(
                tenant, invoice, run_id, created_at, output.proposal, policy_result
            )
        run = AgentRun(
            id=run_id,
            invoice_id=invoice.id,
            business_id=tenant.business_id,
            status=AgentRunStatus.SUCCEEDED,
            invoice_state=state,
            model_proposal=output.proposal,
            policy_result=policy_result,
            model_id=self._decision.model_id,
            prompt_version=self._decision.prompt_version,
            attempt_count=output.attempt_count,
            latency_ms=output.latency_ms,
            input_tokens=output.input_tokens,
            output_tokens=output.output_tokens,
            created_at=created_at,
            action_id=action.id if action else None,
            function_call_id=output.function_call_id,
            proposed_function=output.proposed_function,
            function_arguments=output.function_arguments,
        )
        stored_action = self._repo.save_evaluation(tenant, run, action)
        if stored_action:
            if (
                action is not None
                and stored_action.state == ActionState.PROPOSED
                and stored_action.id == action.id
            ):
                stored_action = await self.execute(tenant, stored_action, actor_type="AGENT")
        else:
            self._schedule_from_policy(tenant, invoice, policy_result, created_at)
        return EvaluationResult(agent_run=run, action=stored_action)

    def _build_action(
        self,
        tenant: TenantContext,
        invoice: Invoice,
        run_id: str,
        created_at: datetime,
        proposal: ModelDecision,
        policy: PolicyResult,
    ) -> tuple[Action, PolicyResult]:
        business = self._repo.get_business_by_id(tenant.business_id)
        settings = self._repo.get_policy_settings(tenant)
        connection = self._repo.get_gmail_connection(tenant.business_id)
        connected = bool(
            connection and not connection.disconnected_at and connection.encrypted_refresh_token
        )
        matched = list(policy.matched_rules)
        if not settings.automation_enabled:
            matched.append("automation_disabled")
        if business.data_classification != DataClassification.REAL:
            matched.append("non_real_data_requires_approval")
        if not connected:
            matched.append("gmail_not_connected")
        automatic = policy.outcome == PolicyOutcome.ALLOW and not matched
        if not automatic:
            policy = policy.model_copy(
                update={
                    "outcome": PolicyOutcome.REQUIRE_APPROVAL,
                    "requires_approval": True,
                    "matched_rules": matched,
                }
            )
        sequence = invoice.reminder_sequence + 1
        raw_key = f"{tenant.business_id}:{invoice.id}:SEND_REMINDER:{sequence}"
        action_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        message = render_reminder(
            invoice, tone=proposal.reminder_tone, intent=proposal.reminder_intent
        )
        return (
            Action(
                id=f"act_{action_key[:28]}",
                invoice_id=invoice.id,
                business_id=tenant.business_id,
                agent_run_id=run_id,
                state=ActionState.PROPOSED if automatic else ActionState.AWAITING_APPROVAL,
                created_at=created_at,
                action_key=action_key,
                recipient_email=invoice.customer.email,
                subject=message.subject,
                body=message.body,
                automatic=automatic,
                reminder_sequence=sequence,
                locale=message.locale,
                template_version=message.template_version,
            ),
            policy,
        )

    def _schedule_from_policy(
        self, tenant: TenantContext, invoice: Invoice, policy: PolicyResult, now: datetime
    ) -> None:
        status = (
            InvoiceWorkflowStatus.PAUSED
            if policy.final_decision == AgentDecision.REQUEST_HUMAN_REVIEW
            else InvoiceWorkflowStatus.OPEN
        )
        self._repo.update_invoice(
            tenant,
            invoice.model_copy(
                update={
                    "next_check_at": policy.next_check_at,
                    "workflow_status": status,
                    "evaluation_lease_until": None,
                    "updated_at": now,
                }
            ),
        )

    def _pause_invoice(self, tenant: TenantContext, invoice: Invoice) -> None:
        now = datetime.now(UTC)
        self._repo.update_invoice(
            tenant,
            invoice.model_copy(
                update={
                    "workflow_status": InvoiceWorkflowStatus.PAUSED,
                    "next_check_at": None,
                    "evaluation_lease_until": None,
                    "updated_at": now,
                }
            ),
        )

    async def execute(
        self,
        tenant: TenantContext,
        action: Action,
        *,
        actor_type: str,
        expected_state: ActionState | None = None,
    ) -> Action:
        executing = self._repo.reserve_action_execution(
            tenant,
            action,
            expected_state or action.state,
            actor_type,
        )
        now = executing.execution_started_at or datetime.now(UTC)
        attempt_number = executing.attempt_count
        try:
            if self._gmail is None or self._cipher is None:
                raise GmailDefiniteFailure("gmail_not_configured", "Gmail is not configured.")
            connection = self._repo.get_gmail_connection(tenant.business_id)
            if (
                connection is None
                or connection.disconnected_at is not None
                or not connection.encrypted_refresh_token
            ):
                raise GmailDefiniteFailure("gmail_not_connected", "Connect Gmail before sending.")
            refresh_token = await run_in_threadpool(
                self._cipher.decrypt, connection.encrypted_refresh_token
            )
            if not executing.recipient_email or not executing.subject or not executing.body:
                raise GmailDefiniteFailure("message_incomplete", "The reminder is incomplete.")
            provider_id = await run_in_threadpool(
                self._gmail.send,
                refresh_token,
                executing.recipient_email,
                executing.subject,
                executing.body,
            )
            completed = datetime.now(UTC)
            final = executing.model_copy(
                update={
                    "state": ActionState.SUCCEEDED,
                    "provider_message_id": provider_id,
                    "execution_completed_at": completed,
                    "delivery_possible": True,
                }
            )
        except GmailDefiniteFailure as failure:
            structlog.get_logger("gmail").error(
                "gmail_delivery_failed", category="gmail_failure", failure_code=failure.code
            )
            completed = datetime.now(UTC)
            final = executing.model_copy(
                update={
                    "state": ActionState.FAILED,
                    "failure_code": failure.code,
                    "failure_message": str(failure),
                    "execution_completed_at": completed,
                    "delivery_possible": False,
                }
            )
        except (GmailAmbiguousFailure, GmailUnavailableError):
            structlog.get_logger("gmail").error("gmail_delivery_unknown", category="gmail_failure")
            completed = datetime.now(UTC)
            final = executing.model_copy(
                update={
                    "state": ActionState.UNKNOWN,
                    "failure_code": "delivery_unknown",
                    "failure_message": "Delivery could not be confirmed; verify the Sent folder.",
                    "execution_completed_at": completed,
                    "delivery_possible": True,
                }
            )
        attempt = ActionAttempt(
            id=f"attempt_{action.id}_{attempt_number}",
            action_id=action.id,
            attempt_number=attempt_number,
            state=final.state,
            automatic=final.automatic,
            provider_message_id=final.provider_message_id,
            failure_code=final.failure_code,
            delivery_possible=final.delivery_possible,
            started_at=now,
            completed_at=completed,
        )
        stored = self._repo.update_action(
            tenant, final, attempt, EvidenceEventType.ACTION_EXECUTED, actor_type
        )
        invoice = self._repo.get_invoice(tenant, final.invoice_id)
        if final.state == ActionState.SUCCEEDED:
            cooldown = self._repo.get_policy_settings(tenant).reminder_cooldown_hours
            self._repo.update_invoice(
                tenant,
                invoice.model_copy(
                    update={
                        "workflow_status": InvoiceWorkflowStatus.OPEN,
                        "next_check_at": completed + timedelta(hours=cooldown),
                        "active_action_id": None,
                        "evaluation_lease_until": None,
                        "updated_at": completed,
                    }
                ),
            )
        else:
            self._pause_invoice(tenant, invoice)
        return stored

    async def approve(self, tenant: TenantContext, action_id: str) -> Action:
        action = self._repo.get_action(tenant, action_id)
        if action.state != ActionState.AWAITING_APPROVAL:
            raise ApiError(
                409, "action_not_awaiting_approval", "The action is not awaiting approval."
            )
        invoice = self._repo.get_invoice(tenant, action.invoice_id)
        cooldown = self._repo.get_policy_settings(tenant).reminder_cooldown_hours
        state = calculate_invoice_state(
            invoice, self._repo.list_actions(tenant, invoice.id), cooldown_hours=cooldown
        )
        if state in {InvoiceState.PAID, InvoiceState.DISPUTED, InvoiceState.HUMAN_REVIEW}:
            raise ApiError(409, "action_policy_changed", "The invoice can no longer be reminded.")
        now = datetime.now(UTC)
        deliveries = [
            item
            for item in self._repo.list_actions(tenant, invoice.id)
            if item.id != action.id and item.state in {ActionState.SUCCEEDED, ActionState.UNKNOWN}
        ]
        if deliveries:
            latest = max(item.execution_completed_at or item.created_at for item in deliveries)
            if latest + timedelta(hours=cooldown) > now:
                raise ApiError(409, "reminder_cooldown", "A recent reminder is still cooling down.")
        approved = action.model_copy(
            update={
                "approved_by": tenant.user_id,
                "approved_at": now,
                "automatic": False,
            }
        )
        return await self.execute(
            tenant,
            approved,
            actor_type="USER",
            expected_state=ActionState.AWAITING_APPROVAL,
        )

    def cancel(self, tenant: TenantContext, action_id: str, payload: ActionCancel) -> Action:
        action = self._repo.get_action(tenant, action_id)
        if action.state not in {
            ActionState.AWAITING_APPROVAL,
            ActionState.PROPOSED,
            ActionState.FAILED,
        }:
            raise ApiError(409, "action_not_cancellable", "The action cannot be cancelled.")
        now = datetime.now(UTC)
        cancelled = action.model_copy(
            update={
                "state": ActionState.CANCELLED,
                "cancelled_by": tenant.user_id,
                "cancelled_at": now,
                "cancel_reason": payload.reason,
            }
        )
        stored = self._repo.update_action(
            tenant, cancelled, None, EvidenceEventType.ACTION_CANCELLED, "USER"
        )
        self._pause_invoice(tenant, self._repo.get_invoice(tenant, action.invoice_id))
        return stored

    async def retry(self, tenant: TenantContext, action_id: str) -> Action:
        action = self._repo.get_action(tenant, action_id)
        if action.state != ActionState.FAILED or action.delivery_possible is not False:
            raise ApiError(
                409, "action_retry_unsafe", "Only a definite non-delivery can be retried."
            )
        proposed = action.model_copy(
            update={
                "approved_by": tenant.user_id,
                "approved_at": datetime.now(UTC),
                "automatic": False,
            }
        )
        return await self.execute(
            tenant,
            proposed,
            actor_type="USER",
            expected_state=ActionState.FAILED,
        )

    def resolve(self, tenant: TenantContext, action_id: str, payload: ActionResolve) -> Action:
        action = self._repo.get_action(tenant, action_id)
        allowed = action.state == ActionState.UNKNOWN or (
            action.state == ActionState.FAILED
            and payload.resolution == ActionResolution.MANUALLY_SENT
        )
        if not allowed:
            raise ApiError(409, "action_not_resolvable", "The action does not require resolution.")
        now = datetime.now(UTC)
        delivered = payload.resolution in {
            ActionResolution.CONFIRMED_DELIVERED,
            ActionResolution.MANUALLY_SENT,
        }
        resolved = action.model_copy(
            update={
                "state": ActionState.SUCCEEDED if delivered else ActionState.FAILED,
                "resolution": payload.resolution,
                "resolved_by": tenant.user_id,
                "resolved_at": now,
                "automatic": False
                if payload.resolution == ActionResolution.MANUALLY_SENT
                else action.automatic,
                "delivery_possible": delivered,
                "failure_code": None if delivered else "confirmed_not_delivered",
                "failure_message": None
                if delivered
                else "The owner confirmed that delivery did not occur.",
            }
        )
        stored = self._repo.update_action(
            tenant, resolved, None, EvidenceEventType.ACTION_RESOLVED, "USER"
        )
        invoice = self._repo.get_invoice(tenant, action.invoice_id)
        if delivered:
            cooldown = self._repo.get_policy_settings(tenant).reminder_cooldown_hours
            self._repo.update_invoice(
                tenant,
                invoice.model_copy(
                    update={
                        "workflow_status": InvoiceWorkflowStatus.OPEN,
                        "next_check_at": now + timedelta(hours=cooldown),
                        "active_action_id": None,
                        "updated_at": now,
                    }
                ),
            )
        else:
            self._pause_invoice(tenant, invoice)
        return stored

    def reconcile_stale_actions(self, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(minutes=self._settings.action_execution_timeout_minutes)
        changed = 0
        for action in self._repo.list_stale_executing_actions(cutoff):
            if not action.business_id:
                continue
            tenant = TenantContext("cloud-scheduler", action.business_id, MembershipRole.OWNER)
            invoice = self._repo.get_invoice(tenant, action.invoice_id)
            unknown = action.model_copy(
                update={
                    "state": ActionState.UNKNOWN,
                    "failure_code": "stale_execution",
                    "failure_message": "Execution ended without a confirmed provider result.",
                    "delivery_possible": True,
                    "execution_completed_at": current,
                }
            )
            self._repo.update_action(
                tenant, unknown, None, EvidenceEventType.ACTION_EXECUTED, "SYSTEM"
            )
            self._pause_invoice(tenant, invoice)
            changed += 1
        return changed
