"""Intake parsing with deterministic fallback."""

import json
import logging
import re
from typing import Any

from app.config import Settings
from app.llm.claude_client import ClaudeClient
from app.llm.prompts import get_prompt
from app.llm.structured_outputs import parse_json_model
from app.mcp_client import McpToolClient
from app.schemas.case import IntakeResult, Intent

logger = logging.getLogger(__name__)

_READ_ONLY_TOOLS = {"lookup_order"}

_SCHEMA_BLOCK = json.dumps({
    "intent": "string, one of: " + ", ".join(i.value for i in Intent),
    "confidence": "float 0.0-1.0",
    "extracted_slots": "object of string key/value pairs",
    "missing_fields": "array of strings (optional)",
    "suggested_next_step": "string (optional, default: gather_evidence)",
})


class IntakeAgent:
    """Extract intent and slots from the incoming user message."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, user_message: str, backend_state: dict[str, Any] | None = None) -> IntakeResult:
        """Parse intake using Claude + MCP tool access when configured, rules otherwise."""

        if self._settings.has_claude_credentials and not self._settings.debug_mode:
            try:
                result = self._parse_with_claude(user_message, backend_state or {})
                logger.info(
                    "AGENT:intake  parser=claude  task=extract intent and slots from user message",
                    extra={"anthropic_api_status": "ok"},
                )
                logger.info(
                    "AGENT:intake  intent=%s  confidence=%.2f  slots=%s",
                    result.intent.value, result.confidence, result.extracted_slots,
                )
                return result
            except Exception as exc:
                logger.warning(
                    "AGENT:intake  parser=claude  status=failed  error=%s  falling back to rules",
                    exc,
                    exc_info=exc,
                    extra={"anthropic_api_status": "failed"},
                )
        else:
            mode = "debug_mock" if self._settings.debug_mode else "no_api_key"
            logger.info("AGENT:intake  parser=rules  reason=%s", mode)
        return self._fallback_parse(user_message)

    def _parse_with_claude(self, user_message: str, backend_state: dict[str, Any]) -> IntakeResult:
        mcp = McpToolClient()
        all_tools = mcp.list_tools("backend")
        tools = [t for t in all_tools if t["name"] in _READ_ONLY_TOOLS]

        def tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            arguments["backend_state"] = backend_state
            return mcp.call("backend", tool_name, arguments)

        client = ClaudeClient(self._settings)
        system_prompt = get_prompt("intake_parse_prompt")
        user_content = [
            {
                "type": "text",
                "text": json.dumps({"respond_with": _SCHEMA_BLOCK}),
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": json.dumps({"user_message": user_message}),
            },
        ]

        raw_text = client.complete_with_tools(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            tools=tools,
            tool_executor=tool_executor,
            final_prefill="The json result is ```json",
            stop_sequences=["```"],
            temperature=0.1,
        )

        return parse_json_model(raw_text.strip(), IntakeResult)

    def _fallback_parse(self, user_message: str) -> IntakeResult:
        text = user_message.lower()
        slots: dict[str, str] = {}
        hash_order = re.search(r"#\s*(\d+)\b", user_message)
        if hash_order:
            slots["order_id"] = hash_order.group(1)
        for prefix, key in (("cus", "customer_id"), ("ord", "order_id")):
            match = re.search(rf"\b{prefix}_[a-z0-9]+\b", text)
            if match:
                slots[key] = match.group(0)

        if "charged twice" in text or "duplicate charge" in text or "charged double" in text:
            intent = Intent.DUPLICATE_CHARGE
            confidence = 0.9
        elif "locked" in text or "unlock" in text:
            intent = Intent.ACCOUNT_LOCKED
            confidence = 0.86
        elif "500" in text or "api" in text or "outage" in text or "service" in text:
            intent = Intent.SUBSCRIPTION_ACTIVE_BUT_SERVICE_FAILING
            confidence = 0.82
        elif "refund" in text:
            intent = Intent.REFUND_REQUEST
            confidence = 0.78
        elif "billing" in text or "invoice" in text or "charge" in text:
            intent = Intent.BILLING_DISPUTE
            confidence = 0.72
        else:
            intent = Intent.AMBIGUOUS_REQUEST
            confidence = 0.55

        missing_fields = []
        if intent in {Intent.DUPLICATE_CHARGE, Intent.REFUND_REQUEST} and "order_id" not in slots:
            missing_fields.append("order_id")
        if "customer_id" not in slots:
            missing_fields.append("customer_id")

        return IntakeResult(
            intent=intent,
            confidence=confidence,
            extracted_slots=slots,
            missing_fields=missing_fields,
            suggested_next_step="clarify_if_needed" if confidence < 0.6 else "gather_evidence",
        )
