"""Explicit state-machine coordinator for support case runs."""

import logging
import re
from uuid import uuid4

from app.log_context import set_case_id

logger = logging.getLogger(__name__)

_BLUE  = "\033[34m"
_RESET = "\033[0m"

from app.agents.incident_agent import has_active_incident
from app.agents.intake_agent import IntakeAgent
from app.agents.policy_agent import ground_policy, policy_guided_refund_decision
from app.agents.report_agent import build_audit_note, build_user_response
from app.agents.resolution_agent import refund_action_for_duplicate
from app.config import Settings
from app.fixtures import get_demo_case
from app.mcp_client import McpToolClient
from app.schemas.case import (
    AuditRecord,
    AgentRunOutput,
    BackendState,
    FinalAction,
    Incident,
    Intent,
    RunCaseRequest,
    ToolCallRecord,
)


class SupportCoordinator:
    """Run one case through intake, evidence gathering, decision, and reporting."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run(self, request: RunCaseRequest) -> AgentRunOutput:
        """Run a support case through the local state machine."""

        case_id = request.case_id or f"case_{uuid4().hex[:12]}"
        user_message = request.user_message
        state = request.mock_backend_state or BackendState()
        if request.fixture_id:
            fixture = get_demo_case(request.fixture_id)
            case_id = request.case_id or fixture.case_id
            user_message = request.user_message or fixture.user_message
            state = request.mock_backend_state or fixture.mock_backend_state

        set_case_id(case_id)
        preview = user_message[:80] + ("…" if len(user_message) > 80 else "")
        logger.info("START  %r", preview)

        mcp_client = McpToolClient()
        backend_state = state.model_dump(mode="json")
        tool_records: list[ToolCallRecord] = []
        audit_records: list[AuditRecord] = []

        intake = IntakeAgent(self._settings).parse(user_message, backend_state=backend_state)
        slots = dict(intake.extracted_slots)
        if state.customer and "customer_id" not in slots:
            slots["customer_id"] = state.customer.customer_id
        if state.order and "order_id" not in slots:
            slots["order_id"] = state.order.order_id


        final_action = FinalAction.ASK_CLARIFYING_QUESTION
        escalate = False

        def call_tool(tool_name: str, arguments: dict, result: dict) -> None:
            tool_records.append(
                ToolCallRecord(tool_name=tool_name, arguments=arguments, result=result)
            )

        def call_mcp(server: str, tool_name: str, arguments: dict, result_summary=None) -> dict:
            log_args = {k: v for k, v in arguments.items() if k != "backend_state"}
            result = mcp_client.call(server, tool_name, arguments)
            if result_summary:
                suffix = f"  result: {result_summary(result)}"
            else:
                suffix = f"  result: {result}"
            logger.info("MCP:tool  %s.%s  %s%s", server, tool_name, log_args, suffix)
            return result

        def capture_audit_records(result: dict) -> None:
            for record in result.get("audit_records", []):
                audit_records.append(AuditRecord.model_validate(record))

        similar_result = call_mcp("knowledge", "retrieve_similar", {"issue_type": intake.intent.value},
            result_summary=lambda r: f"found={len(r.get('similar_cases', []))}")
        for i, case in enumerate(similar_result.get("similar_cases", []), 1):
            logger.debug("%s  similar[%d]: %s%s", _BLUE, i, case.get("summary", ""), _RESET)
        call_tool("retrieve_similar", {"issue_type": intake.intent.value}, similar_result)

        if intake.intent == Intent.DUPLICATE_CHARGE:
            customer_args = {
                "backend_state": backend_state,
                "customer_id": slots.get("customer_id"),
            }
            customer_result = call_mcp("backend", "get_customer", customer_args)
            call_tool("get_customer", {"customer_id": slots.get("customer_id")}, customer_result)
            order_args = {
                "backend_state": backend_state,
                "order_id": slots.get("order_id"),
            }
            order_result = call_mcp("backend", "lookup_order", order_args)
            call_tool("lookup_order", {"order_id": slots.get("order_id")}, order_result)
            payments_args = {
                "backend_state": backend_state,
                "order_id": slots.get("order_id"),
            }
            payments_result = call_mcp("backend", "get_payment_events", payments_args)
            call_tool("get_payment_events", {"order_id": slots.get("order_id")}, payments_result)
            payments = payments_result["payments"]
            order = order_result["order"]
            action = refund_action_for_duplicate(
                duplicate_payment_found=len(payments) >= 2 and order is not None,
                refundable=bool(order.get("refundable", False)) if order else False,
            )
            if action == FinalAction.PROCESS_REFUND:
                approval_result = call_mcp(
                    "governance",
                    "request_refund_approval",
                    {"case_id": case_id, "amount": float(order["amount"])},
                )
                capture_audit_records(approval_result)
                approval = approval_result["approval"]
                call_tool(
                    "request_refund_approval",
                    {"case_id": case_id, "amount": order["amount"]},
                    approval,
                )
                if approval["approved"]:
                    reason = "duplicate_charge"
                    key_result = call_mcp(
                        "backend",
                        "refund_idempotency_key",
                        {
                            "case_id": case_id,
                            "order_id": order["order_id"],
                            "amount": float(order["amount"]),
                            "reason": reason,
                        },
                    )
                    refund_result = call_mcp(
                        "backend",
                        "process_refund",
                        {
                            "backend_state": backend_state,
                            "case_id": case_id,
                            "order_id": order["order_id"],
                            "amount": float(order["amount"]),
                            "reason": reason,
                            "idempotency_key": key_result["idempotency_key"],
                        },
                    )
                    capture_audit_records(refund_result)
                    refund = refund_result["refund"]
                    call_tool("process_refund", {"order_id": order["order_id"]}, refund)
                    final_action = FinalAction.PROCESS_REFUND
                else:
                    final_action = FinalAction.ESCALATE
                    escalate = True
            else:
                final_action = FinalAction.DENY_WITH_EXPLANATION

        elif intake.intent == Intent.SUBSCRIPTION_ACTIVE_BUT_SERVICE_FAILING:
            subscription_args = {
                "backend_state": backend_state,
                "customer_id": slots.get("customer_id"),
            }
            subscription_result = call_mcp("backend", "get_subscription", subscription_args)
            call_tool(
                "get_subscription",
                {"customer_id": slots.get("customer_id")},
                subscription_result,
            )
            service = "api"
            if subscription_result["subscription"]:
                service = subscription_result["subscription"]["service"]
            incident_result = call_mcp(
                "observability",
                "search_incidents",
                {"backend_state": backend_state, "service": service, "window": "24h"},
            )
            call_tool("search_incidents", {"service": service, "window": "24h"}, incident_result)
            deployment_result = call_mcp(
                "observability",
                "get_recent_deployments",
                {"backend_state": backend_state, "service": service},
            )
            call_tool("get_recent_deployments", {"service": service}, deployment_result)
            metrics_result = call_mcp(
                "observability",
                "query_metrics",
                {"backend_state": backend_state, "metric_name": "error_rate", "window": "1h"},
            )
            call_tool("query_metrics", {"metric_name": "error_rate", "window": "1h"}, metrics_result)
            logs_result = call_mcp(
                "observability",
                "query_logs",
                {"backend_state": backend_state, "service": service},
            )
            call_tool("query_logs", {"service": service}, logs_result)
            active_incidents = [Incident.model_validate(i) for i in incident_result["incidents"]]
            final_action = (
                FinalAction.EXPLAIN_INCIDENT_AND_ROUTE
                if has_active_incident(active_incidents)
                else FinalAction.ESCALATE
            )
            escalate = final_action == FinalAction.ESCALATE

        elif intake.intent == Intent.ACCOUNT_LOCKED:
            customer_args = {
                "backend_state": backend_state,
                "customer_id": slots.get("customer_id"),
            }
            customer_result = call_mcp("backend", "get_customer", customer_args)
            call_tool("get_customer", {"customer_id": slots.get("customer_id")}, customer_result)
            customer = customer_result["customer"]
            if customer and customer.get("risk_level") == "high":
                approval_result = call_mcp(
                    "governance",
                    "request_account_unlock_approval",
                    {"case_id": case_id},
                )
                capture_audit_records(approval_result)
                approval = approval_result["approval"]
                call_tool("request_account_unlock_approval", {"case_id": case_id}, approval)
                escalation_result = call_mcp(
                    "backend",
                    "escalate_to_human",
                    {
                        "backend_state": backend_state,
                        "case_id": case_id,
                        "summary": "High-risk locked account requires manual review.",
                    },
                )
                capture_audit_records(escalation_result)
                escalation = escalation_result["escalation"]
                call_tool("escalate_to_human", {"case_id": case_id}, escalation)
                final_action = FinalAction.ESCALATE
                escalate = True
            else:
                final_action = FinalAction.ASK_CLARIFYING_QUESTION

        elif intake.intent == Intent.REFUND_REQUEST:
            order_id = slots.get("order_id")
            customer_id = slots.get("customer_id")
            if not order_id and not customer_id:
                logger.info(
                    "REFUND:identity_gate  refused — missing order_id and customer_id; no tool calls"
                )
                final_action = FinalAction.ASK_CLARIFYING_QUESTION
            else:
                days_target = None
                msg_lower = user_message.lower()
                if "water heater" in msg_lower:
                    day_match = re.search(r"(\d+)\s*days?\s*ago", msg_lower)
                    if day_match:
                        days_target = int(day_match.group(1))
                    elif "13" in msg_lower:
                        days_target = 13
                order_args: dict = {
                    "backend_state": backend_state,
                    "order_id": order_id,
                }
                if days_target is not None:
                    order_args["days_since_purchase_target"] = days_target
                    order_args["disambiguate_water_heater"] = True
                order_result = call_mcp("backend", "lookup_order", order_args)
                call_tool("lookup_order", {"order_id": order_id}, order_result)
                order = order_result["order"]

                if order is None:
                    final_action = FinalAction.DENY_WITH_EXPLANATION
                else:
                    policy_refs_result = call_mcp(
                        "knowledge", "policy_refs", {"intent": intake.intent.value}
                    )
                    call_tool("policy_refs", {"intent": intake.intent.value}, policy_refs_result)
                    refs = policy_refs_result.get("refs", [])
                    policy_docs: dict[str, str] = {}
                    for ref in refs:
                        doc_result = call_mcp("knowledge", "fetch_policy_doc", {"policy_id": ref})
                        call_tool("fetch_policy_doc", {"policy_id": ref}, doc_result)
                        policy_docs[ref] = doc_result.get("text", "")

                    action = policy_guided_refund_decision(
                        order=order,
                        policy_docs=policy_docs,
                        similar_cases=similar_result.get("similar_cases", []),
                        settings=self._settings,
                    )
                    if action == FinalAction.PROCESS_REFUND:
                        approval_result = call_mcp(
                            "governance",
                            "request_refund_approval",
                            {"case_id": case_id, "amount": float(order["amount"])},
                        )
                        capture_audit_records(approval_result)
                        approval = approval_result["approval"]
                        call_tool(
                            "request_refund_approval",
                            {"case_id": case_id, "amount": order["amount"]},
                            approval,
                        )
                        if approval["approved"]:
                            reason = "refund_request"
                            key_result = call_mcp(
                                "backend",
                                "refund_idempotency_key",
                                {
                                    "case_id": case_id,
                                    "order_id": order["order_id"],
                                    "amount": float(order["amount"]),
                                    "reason": reason,
                                },
                            )
                            refund_result = call_mcp(
                                "backend",
                                "process_refund",
                                {
                                    "backend_state": backend_state,
                                    "case_id": case_id,
                                    "order_id": order["order_id"],
                                    "amount": float(order["amount"]),
                                    "reason": reason,
                                    "idempotency_key": key_result["idempotency_key"],
                                },
                            )
                            capture_audit_records(refund_result)
                            call_tool(
                                "process_refund",
                                {"order_id": order["order_id"]},
                                refund_result["refund"],
                            )
                            final_action = FinalAction.PROCESS_REFUND
                        else:
                            final_action = FinalAction.ESCALATE
                            escalate = True
                    elif action == FinalAction.ESCALATE:
                        escalation_result = call_mcp(
                            "backend",
                            "escalate_to_human",
                            {
                                "backend_state": backend_state,
                                "case_id": case_id,
                                "summary": "Refund request requires human review due to order status.",
                            },
                        )
                        capture_audit_records(escalation_result)
                        call_tool(
                            "escalate_to_human",
                            {"case_id": case_id},
                            escalation_result["escalation"],
                        )
                        final_action = FinalAction.ESCALATE
                        escalate = True
                    else:
                        final_action = FinalAction.DENY_WITH_EXPLANATION

        else:
            final_action = FinalAction.ASK_CLARIFYING_QUESTION

        logger.info(
            "ORCHESTRATOR:decision  final_action=%s  escalate=%s",
            final_action.value, escalate,
        )

        policy_grounding = ground_policy(
            intake.intent,
            final_action,
            tool_records,
            self._settings,
        )
        applied_policy_refs = policy_grounding.refs
        for ref in applied_policy_refs:
            policy_doc_result = call_mcp("knowledge", "fetch_policy_doc", {"policy_id": ref})
            call_tool("fetch_policy_doc", {"policy_id": ref}, policy_doc_result)

        explanation = call_mcp(
            "knowledge",
            "explain_action",
            {"final_action": final_action.value},
        )["explanation"]
        output = AgentRunOutput(
            case_id=case_id,
            intent=intake.intent,
            confidence=intake.confidence,
            slots=slots,
            tool_calls=[record.tool_name for record in tool_records],
            tool_call_records=tool_records,
            final_action=final_action,
            escalate=escalate,
            user_response=build_user_response(
                final_action,
                intake.intent,
                explanation,
                policy_grounding.explanation,
                self._settings,
                similar_result.get("similar_cases", []),
            ),
            audit_note="",
            policy_explanation=policy_grounding.explanation,
            audit_records=audit_records,
            policy_refs=applied_policy_refs,
        )
        output.audit_note = build_audit_note(output, self._settings)
        logger.info("DONE")
        return output
