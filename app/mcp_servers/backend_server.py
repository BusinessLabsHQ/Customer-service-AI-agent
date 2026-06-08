"""Backend MCP server implemented with the MCP Python SDK."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.mcp_servers.common import dump_audit_records, parse_backend_state
from app.tools.audit_tools import AuditLog
from app.tools.backend_tools import BackendTools, build_refund_idempotency_key

mcp = FastMCP("support-backend")


@mcp.tool()
def get_customer(
    backend_state: dict[str, Any] | None = None,
    customer_id: str | None = None,
) -> dict[str, Any]:
    """Look up a customer in the supplied backend state."""

    audit_log = AuditLog()
    return BackendTools(parse_backend_state(backend_state), audit_log).get_customer(customer_id)


@mcp.tool()
def lookup_order(
    backend_state: dict[str, Any] | None = None,
    order_id: str | None = None,
    days_since_purchase_target: int | None = None,
    disambiguate_water_heater: bool = False,
) -> dict[str, Any]:
    """Look up an order in the supplied backend state."""

    audit_log = AuditLog()
    return BackendTools(parse_backend_state(backend_state), audit_log).lookup_order(
        order_id,
        days_since_purchase_target=days_since_purchase_target,
        disambiguate_water_heater=disambiguate_water_heater,
    )


@mcp.tool()
def get_subscription(
    backend_state: dict[str, Any] | None = None,
    customer_id: str | None = None,
) -> dict[str, Any]:
    """Look up a subscription in the supplied backend state."""

    audit_log = AuditLog()
    return BackendTools(parse_backend_state(backend_state), audit_log).get_subscription(customer_id)


@mcp.tool()
def get_payment_events(
    backend_state: dict[str, Any] | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    """List payment events in the supplied backend state."""

    audit_log = AuditLog()
    return BackendTools(parse_backend_state(backend_state), audit_log).get_payment_events(order_id)


@mcp.tool()
def process_refund(
    *,
    case_id: str,
    order_id: str,
    amount: float,
    reason: str,
    idempotency_key: str,
    backend_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Process a governed refund with an idempotency key."""

    audit_log = AuditLog()
    refund = BackendTools(parse_backend_state(backend_state), audit_log).process_refund(
        case_id=case_id,
        order_id=order_id,
        amount=amount,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    return {"refund": refund, "audit_records": dump_audit_records(audit_log)}


@mcp.tool()
def escalate_to_human(
    *,
    case_id: str,
    summary: str,
    backend_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue a case for human review."""

    audit_log = AuditLog()
    escalation = BackendTools(parse_backend_state(backend_state), audit_log).escalate_to_human(
        case_id=case_id,
        summary=summary,
    )
    return {"escalation": escalation, "audit_records": dump_audit_records(audit_log)}


@mcp.tool()
def refund_idempotency_key(
    *,
    case_id: str,
    order_id: str,
    amount: float,
    reason: str,
) -> dict[str, str]:
    """Build the deterministic idempotency key required for refund writes."""

    return {
        "idempotency_key": build_refund_idempotency_key(
            case_id=case_id,
            order_id=order_id,
            amount=amount,
            reason=reason,
        )
    }


def main() -> None:
    """Run the backend MCP server over streamable HTTP."""

    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
