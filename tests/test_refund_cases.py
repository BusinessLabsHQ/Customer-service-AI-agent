from app.config import Settings
from app.orchestration.state_machine import SupportCoordinator
from app.schemas.case import BackendState, FinalAction, Order, RunCaseRequest


def run_refund(order: Order | None) -> object:
    return SupportCoordinator(Settings(APP_ENV="test")).run(
        RunCaseRequest(
            user_message="I want a refund for my order #TEST-1",
            mock_backend_state=BackendState(order=order),
        )
    )


def test_refund_without_identity_refuses_no_tools() -> None:
    output = SupportCoordinator(Settings(APP_ENV="test")).run(
        RunCaseRequest(
            user_message="I want a refund please",
            mock_backend_state=BackendState(order=None),
        )
    )

    assert output.final_action == FinalAction.ASK_CLARIFYING_QUESTION
    assert output.escalate is False
    assert "order number or customer ID" in output.user_response
    assert "lookup_order" not in output.tool_calls
    assert "process_refund" not in output.tool_calls
    assert "request_refund_approval" not in output.tool_calls


def test_refund_order_not_found_denies() -> None:
    output = run_refund(order=None)

    assert output.final_action == FinalAction.DENY_WITH_EXPLANATION
    assert output.escalate is False
    assert "lookup_order" in output.tool_calls
    assert "policy_refs" not in output.tool_calls
    assert "request_refund_approval" not in output.tool_calls


def test_refund_amount_over_100_escalates() -> None:
    output = run_refund(order=Order(order_id="TEST-1", customer_id="cus_1", amount=250.00, refundable=True))

    assert output.final_action == FinalAction.ESCALATE
    assert output.escalate is True
    assert "policy_refs" in output.tool_calls
    assert "fetch_policy_doc" in output.tool_calls
    assert "request_refund_approval" in output.tool_calls
    assert any(r.action == "request_refund_approval" for r in output.audit_records)
    idx_refs = output.tool_calls.index("policy_refs")
    idx_approval = output.tool_calls.index("request_refund_approval")
    assert idx_refs < idx_approval


def test_refund_amount_under_100_auto_refunds() -> None:
    output = run_refund(order=Order(order_id="TEST-1", customer_id="cus_1", amount=49.99, refundable=True))

    assert output.final_action == FinalAction.PROCESS_REFUND
    assert output.escalate is False
    assert "policy_refs" in output.tool_calls
    assert "fetch_policy_doc" in output.tool_calls
    assert "process_refund" in output.tool_calls
    assert any(r.action == "process_refund" for r in output.audit_records)
    idx_refs = output.tool_calls.index("policy_refs")
    idx_refund = output.tool_calls.index("process_refund")
    assert idx_refs < idx_refund


def test_refund_outside_window_denies() -> None:
    output = run_refund(
        order=Order(
            order_id="TEST-1",
            customer_id="cus_1",
            amount=49.99,
            refundable=True,
            days_since_purchase=45,
        )
    )

    assert output.final_action == FinalAction.DENY_WITH_EXPLANATION
    assert output.escalate is False
    assert "policy_refs" in output.tool_calls
    assert "fetch_policy_doc" in output.tool_calls
    assert "process_refund" not in output.tool_calls
    assert "request_refund_approval" not in output.tool_calls


def test_refund_non_paid_status_escalates() -> None:
    output = run_refund(
        order=Order(
            order_id="TEST-1",
            customer_id="cus_1",
            amount=49.99,
            refundable=True,
            status="shipped",
            days_since_purchase=5,
        )
    )

    assert output.final_action == FinalAction.ESCALATE
    assert output.escalate is True
    assert "policy_refs" in output.tool_calls
    assert "escalate_to_human" in output.tool_calls
    assert "process_refund" not in output.tool_calls
    assert any(r.action == "escalate_to_human" for r in output.audit_records)


def test_refund_within_window_approves() -> None:
    output = run_refund(
        order=Order(
            order_id="TEST-1",
            customer_id="cus_1",
            amount=49.99,
            refundable=True,
            days_since_purchase=10,
        )
    )

    assert output.final_action == FinalAction.PROCESS_REFUND
    assert "policy_refs" in output.tool_calls
    assert "process_refund" in output.tool_calls
