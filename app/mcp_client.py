"""Synchronous MCP HTTP client for the support workflow demo."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpHttpSettings(BaseSettings):
    """HTTP endpoints for MCP tool servers."""

    mcp_backend_url: str = "http://127.0.0.1:8765/backend/mcp"
    mcp_governance_url: str = "http://127.0.0.1:8765/governance/mcp"
    mcp_observability_url: str = "http://127.0.0.1:8765/observability/mcp"
    mcp_knowledge_url: str = "http://127.0.0.1:8765/knowledge/mcp"

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
    )

    def url_for(self, server: str) -> str:
        """Return the configured URL for one MCP server group."""

        return {
            "backend": self.mcp_backend_url,
            "governance": self.mcp_governance_url,
            "observability": self.mcp_observability_url,
            "knowledge": self.mcp_knowledge_url,
        }[server]


class McpToolClient:
    """Call HTTP MCP servers from synchronous orchestration code."""

    def __init__(self, settings: McpHttpSettings | None = None) -> None:
        self._settings = settings or McpHttpSettings()

    def list_tools(self, server: str) -> list[dict[str, Any]]:
        """Return tool schemas from an MCP server in Anthropic tool format."""

        return anyio.run(self._list_tools_async, self._settings.url_for(server))

    async def _list_tools_async(self, url: str) -> list[dict[str, Any]]:
        async with streamable_http_client(url) as (read, write, _session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in result.tools
        ]

    def post_tool_use(
        self,
        server: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """PostToolUse hook — runs after every MCP tool result (assessment Step 4)."""

        safe_args = {key: value for key, value in arguments.items() if key != "backend_state"}
        logger.info(
            "post_tool_use  server=%s  tool=%s  args=%s  result_keys=%s",
            server,
            tool_name,
            safe_args,
            sorted(result.keys()),
        )
        return result

    def call(self, server: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one MCP tool and return its structured JSON result."""

        return anyio.run(
            self._call_async,
            server,
            self._settings.url_for(server),
            tool_name,
            arguments,
        )

    async def _call_async(
        self,
        server: str,
        url: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        async with streamable_http_client(url) as (read, write, _session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

        if result.isError:
            text = self._result_text(result)
            raise RuntimeError(f"MCP tool {tool_name} failed: {text}")

        if result.structuredContent is not None:
            payload = dict(result.structuredContent)
        else:
            text = self._result_text(result)
            if not text:
                payload = {}
            else:
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise TypeError(f"MCP tool {tool_name} returned non-object JSON.")
                payload = parsed

        return self.post_tool_use(server, tool_name, arguments, payload)

    def _result_text(self, result: Any) -> str:
        return "\n".join(
            content.text
            for content in result.content
            if getattr(content, "type", None) == "text" and getattr(content, "text", None)
        )
