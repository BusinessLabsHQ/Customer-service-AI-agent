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


def test_call_invokes_post_tool_use_hook(monkeypatch, caplog) -> None:
    def fake_run(_fn, server, url, tool_name, arguments):
        return {"order": {"order_id": "wh_121_13d"}}

    monkeypatch.setattr("app.mcp_client.anyio.run", fake_run)
    client = McpToolClient()

    with caplog.at_level(logging.INFO, logger="app.mcp_client"):
        out = client.call("backend", "lookup_order", {"order_id": "wh_121_13d"})

    assert out["order"]["order_id"] == "wh_121_13d"
    assert "post_tool_use  server=backend  tool=lookup_order" in caplog.text


def test_call_logs_retry_on_503(monkeypatch, caplog) -> None:
    calls = {"n": 0}

    def fake_run(self, server, url, tool_name, arguments):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("MCP tool lookup_order failed: HTTP 503 Service Unavailable")
        return {"order": {"order_id": "wh_121_13d"}}

    monkeypatch.setattr("app.mcp_client.anyio.run", fake_run)
    client = McpToolClient()

    with caplog.at_level(logging.INFO, logger="app.mcp_client"):
        out = client.call("backend", "lookup_order", {"order_id": "wh_121_13d"})

    assert out["order"]["order_id"] == "wh_121_13d"
    assert calls["n"] == 2
    assert "retrying tool call  server=backend  tool=lookup_order" in caplog.text
