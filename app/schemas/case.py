"""Case, backend-state, and runtime output schemas."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Intent(StrEnum):
    """Supported support issue types."""

    DUPLICATE_CHARGE = "duplicate_charge"
    REFUND_REQUEST = "refund_request"
    ACCOUNT_LOCKED = "account_locked"
    SUBSCRIPTION_ACTIVE_BUT_SERVICE_FAILING = "subscription_active_but_service_failing"
    BILLING_DISPUTE = "billing_dispute"
    AMBIGUOUS_REQUEST = "ambiguous_request"
    SUSPECTED_INCIDENT = "suspected_incident"


class FinalAction(StrEnum):
    """Actions the orchestrator is allowed to produce."""

    PROCESS_REFUND = "process_refund"
    DENY_WITH_EXPLANATION = "deny_with_explanation"
    EXPLAIN_INCIDENT_AND_ROUTE = "explain_incident_and_route"
    ESCALATE = "escalate"
    ASK_CLARIFYING_QUESTION = "ask_clarifying_question"
    NO_ACTION = "no_action"


class CaseContext(BaseModel):
    """Request context supplied with a support case."""

    channel: str = "api"
    locale: str = "en-US"


class Customer(BaseModel):
    """Mock customer record."""

    customer_id: str
    email: str
    status: str = "active"
    risk_level: str = "low"


class Order(BaseModel):
    """Mock order record."""

    order_id: str
    customer_id: str
    amount: float
    currency: str = "USD"
    status: str = "paid"
    refundable: bool = True
    days_since_purchase: int | None = None


class Subscription(BaseModel):
    """Mock subscription record."""

    customer_id: str
    status: str = "inactive"
    service: str = "api"


class PaymentEvent(BaseModel):
    """Mock payment event."""

    payment_id: str
    order_id: str
    amount: float
    currency: str = "USD"
    status: str = "succeeded"
    created_at: str


class Incident(BaseModel):
    """Mock incident record."""

    incident_id: str
    service: str
    severity: str
    status: str
    summary: str


class Deployment(BaseModel):
    """Mock deployment record."""

    deployment_id: str
    service: str
    status: str
    deployed_at: str


class BackendState(BaseModel):
    """All local data available to mock tools for a case."""

    customer: Customer | None = None
    order: Order | None = None
    subscription: Subscription | None = None
    payments: list[PaymentEvent] = Field(default_factory=list)
    incidents: list[Incident] = Field(default_factory=list)
    deployments: list[Deployment] = Field(default_factory=list)


class ExpectedOutput(BaseModel):
    """Expected labels for eval cases."""

    intent: Intent
    slots: dict[str, str] = Field(default_factory=dict)
    tool_sequence: list[str] = Field(default_factory=list)
    final_action: FinalAction
    escalate: bool
    policy_refs: list[str] = Field(default_factory=list)


class SupportCase(BaseModel):
    """Input case format used by fixtures and eval data."""

    case_id: str
    user_message: str
    context: CaseContext = Field(default_factory=CaseContext)
    mock_backend_state: BackendState = Field(default_factory=BackendState)
    expected: ExpectedOutput | None = None


class RunCaseRequest(BaseModel):
    """API request for a single support-agent run."""

    user_message: str
    case_id: str | None = None
    context: CaseContext = Field(default_factory=CaseContext)
    mock_backend_state: BackendState | None = None
    fixture_id: str | None = None


class FixtureSummary(BaseModel):
    """Public fixture metadata for demo discovery."""

    fixture_id: str
    case_id: str
    user_message: str
    expected_intent: Intent | None = None
    expected_final_action: FinalAction | None = None
    expected_escalate: bool | None = None


class IntakeResult(BaseModel):
    """Structured intake parse."""

    intent: Intent
    confidence: float = Field(ge=0, le=1)
    extracted_slots: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    suggested_next_step: str = "gather_evidence"


class ToolCallRecord(BaseModel):
    """Audit-friendly record of one tool invocation."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class AuditRecord(BaseModel):
    """Audit entry produced by controlled write operations."""

    case_id: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class PolicyGroundingOutput(BaseModel):
    explanation: str
    refs: list[str] = Field(default_factory=list)


class PolicyRefundDecision(BaseModel):
    recommended_action: FinalAction
    reason: str


class UserResponseOutput(BaseModel):
    response: str


class AuditNoteOutput(BaseModel):
    audit_note: str


class SyntheticVariant(BaseModel):
    rewritten_user_message: str
    variant_type: str
    preserved_truth_statement: str


class AgentRunOutput(BaseModel):
    """Public runtime output returned by the API and eval runner."""

    case_id: str
    customer_id: str | None = None
    order_id: str | None = None
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    slots: dict[str, str] = Field(default_factory=dict)
    tool_calls: list[str] = Field(default_factory=list)
    tool_call_records: list[ToolCallRecord] = Field(default_factory=list)
    final_action: FinalAction
    escalate: bool
    user_response: str
    audit_note: str
    policy_explanation: str | None = None
    audit_records: list[AuditRecord] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
