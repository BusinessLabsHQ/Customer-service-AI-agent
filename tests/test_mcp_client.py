import logging

from app.mcp_client import McpToolClient


def test_post_tool_use_hook_logs_and_returns_result(caplog) -> None:
    client = McpToolClient()

    with caplog.at_level(logging.INFO, logger="app.mcp_client"):
        out = client.post_tool_use(
            "backend",
            "lookup_order",
            {"order_id": "wh_121_13d"},
            {"order": {"order_id": "wh_121_13d", "customer_id": "cus_121"}},
        )

    assert out["order"]["order_id"] == "wh_121_13d"
    assert "post_tool_use  server=backend  tool=lookup_order" in caplog.text
