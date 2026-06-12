Project made for assessment for BusinessLabs.org
My id is 2026-2601

# Customer-service-AI-agent — Refund Triage

A proof-of-concept customer support triage service demonstrating how LLMs can be integrated into a deterministic workflow. It processes refund requests using a state machine backed by the Claude API — Claude classifies intent via MCP tool_use, evaluates eligibility against policy, and generates a policy-grounded explanation. Not intended for production use.

> **Scope:** Only the refund request flow is fully implemented. Other intents (duplicate charge, account unlock, service incident) exist in the codebase but are not the focus of this project.

> **Requires:** An Anthropic API key for Claude-backed intent parsing and policy evaluation. All flows fall back to deterministic rules when the key is absent, so tests pass without one.

---

## Why Use an LLM

A rule-based engine or a human agent can handle refund triage, but each has a ceiling this project is designed to illustrate.

**Understanding what the customer actually means.** Real customers write "I was double-billed", "can I get my money back", or "this charge looks wrong" — all meaning a refund request. `IntakeAgent` uses Claude to extract intent and order ID regardless of phrasing, without a brittle keyword list.

**Reading policy as text, not as code.** Policy is stored as a plain English document (`refund_policy.standard`) and Claude reads it directly to make the eligibility decision. Updating the policy means editing a text file, not rewriting logic.

**Explaining decisions in natural language.** `ground_policy` asks Claude to write a customer-facing explanation citing the specific policy clause that applies — consistent, policy-grounded prose at scale.

**Learning from past cases.** The `retrieve_similar` tool fetches resolved cases from history and passes them to Claude when making the decision, providing concrete examples of how edge cases were handled previously.

**Requesting data only when needed.** The intake agent registers `lookup_order` with the Claude API call and lets Claude decide whether to call it. If the message already makes intent clear, no lookup happens; if an order ID is mentioned, Claude calls the tool mid-classification.

**Keeping humans in the loop for genuine ambiguity.** Orders with a `shipped` status or refunds above the governance threshold are escalated rather than auto-decided. The LLM filters clear-cut cases so human agents only see the ones that genuinely need them.

---

## Installation

### Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)

### Setup

```bash
git clone <repo-url>
cd ClaudeWorkFlowCustomerService

# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY=sk-ant-...
```

### Run the services

Two processes must be running simultaneously:

```bash
# Terminal 1 — main API (port 8000)
LOG_LEVEL=DEBUG DEBUG_MODE=true uv run uvicorn app.main:app --reload --log-level debug

# Terminal 2 — MCP tool server (port 8765)
uv run uvicorn app.mcp_http:app --host 127.0.0.1 --port 8765
```

### Test manually

```bash
curl -s -X POST http://127.0.0.1:8000/cases/run \
  -H 'content-type: application/json' \
  -d '{"user_message": "I want a refund for my order #121"}' \
  | python3 -m json.tool
```

Order data is seeded deterministically per order ID. Useful test orders:

| Order | Scenario | Details |
|-------|----------|---------|
| `#121` | Auto-refund | $77.87, paid, refundable, 13 days |
| `#121` + water heaters | Disambiguate by purchase date | Customer bought 3 water heaters (5d, **13d**, 45d ago) — agent picks the 13-day order |
| `#12` | Escalate — over $100 | $107.79, paid, refundable, 14 days |
| `#9` | Deny — order not found | Returns null from backend |
| `#1` | Deny — outside 30-day window | 45 days since purchase |
| `#121` with `status=shipped` | Escalate — non-paid status | Requires human review |

#### Workflow of #121

##### Intake
![Intake](images/image_121_intake.png)

##### Policy (making decision)

-- Get some related data to assist decision making
![alt text](images/image_121_policy_info.png)

-- Make decision
![alt text](images/image_121_policy_decision.png)

##### Report

-- Response to customer
![alt text](images/image_121_report_user.png)

-- Internal audit report
![alt text](images/image_121_report_internal_use.png)

### Run tests

```bash
uv run pytest
```

Tests spin up their own MCP server automatically and do not require `ANTHROPIC_API_KEY`.

---

## Refund Workflow

```
POST /cases/run
  │
  ├─ IntakeAgent            Parse message with Claude (tool_use loop)
  │   └─ [if order mentioned] lookup_order  ← Claude calls this during intake
  │                         → intent=refund_request, slots={order_id, amount, status, …}
  │                         Fallback: regex keyword matching when API key absent
  │
  ├─ retrieve_similar       MCP tool: fetch past similar cases for context
  │
  ├─ lookup_order           MCP tool: fetch order from mock backend
  │   └─ order not found ──────────────────────────────────────────► DENY
  │
  ├─ policy_refs            MCP tool: resolve applicable policy refs for refund_request
  ├─ fetch_policy_doc       MCP tool: fetch full text of refund_policy.standard
  │
  ├─ PolicyAgent            Evaluate order against policy with Claude
  │   ├─ days_since_purchase > 30  ──────────────────────────────► DENY
  │   ├─ refundable = false  ────────────────────────────────────► DENY
  │   ├─ status ≠ "paid"    ─────────────────────────────────────► ESCALATE
  │   └─ all conditions met  ────────────────────────────────────► PROCESS_REFUND
  │
  ├─ [PROCESS_REFUND path]
  │   ├─ request_refund_approval   Governance MCP tool (auto-approve if amount ≤ $100)
  │   │   └─ amount > $100 ──────────────────────────────────────► ESCALATE
  │   ├─ refund_idempotency_key    Generate dedup key
  │   └─ process_refund            Execute refund via MCP tool
  │
  ├─ [ESCALATE path]
  │   └─ escalate_to_human         MCP tool: record handoff to human agent
  │
  └─ ground_policy          Claude generates a policy-grounded explanation for the customer
```

### Decision matrix

| Condition | Action |
|-----------|--------|
| Order not found | Deny — skip policy fetch entirely |
| Purchase > 30 days ago | Deny |
| Order not marked refundable | Deny |
| Order status is `shipped` or `pending` | Escalate to human |
| Amount > $100 (governance limit) | Escalate to human |
| All conditions met, amount ≤ $100 | Process refund automatically |

---

## LLM Techniques

### Orchestrator — deterministic state machine

All orchestration lives in `app/orchestration/state_machine.py`. It is an explicit `if/elif` chain keyed on `Intent` — not a free-form agent loop. Each branch calls MCP tools in a fixed sequence, captures tool records and audit records, and sets `final_action`. Adding a new case type means adding a new branch.

```
SupportCoordinator.run()
  └─ IntakeAgent.parse()          → intent + slots
  └─ McpToolClient.call(...)      → tool results
  └─ PolicyAgent.decide()         → FinalAction
  └─ ReportAgent.build()          → user response + audit note
```

### Agents

Agents are single-responsibility modules that wrap Claude calls with deterministic fallbacks:

| Agent | File | Role |
|-------|------|------|
| `IntakeAgent` | `app/agents/intake_agent.py` | Parse intent and slots; calls `lookup_order` via Claude tool_use when order details are needed |
| `PolicyAgent` | `app/agents/policy_agent.py` | Evaluate order against policy; ground explanation |
| `ReportAgent` | `app/agents/report_agent.py` | Generate customer response and audit note |

Every agent has a fallback path that activates when `ANTHROPIC_API_KEY` is absent or Claude fails, keeping the API functional without credentials.

### MCP tools

Tool logic lives in `app/tools/` and is exposed through four MCP servers served by `app/mcp_http.py` on port 8765:

| Server | Path | Tools |
|--------|------|-------|
| `backend` | `/backend/mcp` | `lookup_order`, `process_refund`, `escalate_to_human` |
| `governance` | `/governance/mcp` | `request_refund_approval` |
| `observability` | `/observability/mcp` | Incident and deployment queries |
| `knowledge` | `/knowledge/mcp` | `policy_refs`, `fetch_policy_doc`, `retrieve_similar` |

MCP servers are thin wrappers — they never call Claude. All Claude calls go through `app/llm/claude_client.py`.

### Agentic intake — tool use during classification

The `IntakeAgent` registers the `lookup_order` MCP tool with the Claude API call. Claude decides whether to use it based on the customer message:

```
User message → Claude (with tools registered)
                 └─ [tool_use: lookup_order] → McpToolClient.call() → tool_result
                 └─ Claude continues with order data → IntakeResult JSON
```

Tool schemas are fetched live via `McpToolClient.list_tools("backend")` and passed to `messages.create(tools=[...])` — nothing is hardcoded. Only `lookup_order` is exposed during intake; write operations (`process_refund`, `escalate_to_human`) are never registered here.

### JSON prefill

Structured JSON output uses an assistant prefill instead of a JSON mode:

```python
messages=[
    {"role": "user",      "content": "<prompt + variables>"},
    {"role": "assistant", "content": "The json result is ```json"},  # prefill
]
stop_sequences=["```"]
```

Claude continues from the prefill and stops at the closing fence, yielding clean JSON without wrapper text. The result is immediately validated with Pydantic. On the tool-use path, the same fence-stripping logic runs after all tool rounds complete.

### Per-agent temperature

Each agent uses a temperature tuned to its task rather than a single global value:

| Agent | Temperature | Reason |
|-------|-------------|--------|
| `IntakeAgent` | 0.1 | Classification must be consistent and deterministic |
| `PolicyAgent` | 0.1 | Policy evaluation and grounding require strict adherence to provided facts |
| `ReportAgent` | 0.3 | Customer-facing responses benefit from slight variation in phrasing |

The `ClaudeClient` methods (`generate_json`, `generate_text`, `complete_with_tools`) all accept an optional `temperature` parameter that overrides the global `CLAUDE_TEMPERATURE` setting.

### Prompt caching

Every Claude call is structured with cache breakpoints so repeated tokens are not reprocessed. Fixed content is placed before variable content:

```
system prompt          ← cache_control: ephemeral   fixed prompt text
respond_with schema    ← cache_control: ephemeral   fixed per response model
policy_text            ← cache_control: ephemeral   rarely changes
variable data          ←                            changes every request
```

For the refund decision call specifically, the full policy document sits in its own cached block between the schema and the per-request order data. This means the three largest fixed components are cached, and only the order details and similar cases are sent fresh each time. Cache hits appear in the API response as `usage.cache_read_input_tokens`.
