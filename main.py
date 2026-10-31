"""
Customer Support AI Agent — Completed Implementation
==========================================
All TODO sections have been implemented below.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent, BeforeToolCallEvent, AfterToolCallEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")
logger.setLevel(logging.INFO)

# ── App Initialisation ────────────────────────────────────────────────────────
app = BedrockAgentCoreApp()


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── Configuration ──────────────────────────────────────────────────────────────
# Replace the placeholder strings with your actual AWS resource values.
# You collected these in the infrastructure setup section of the project instructions.
#
# GATEWAY_URL format: https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
# This starter uses an unsigned MCP connection and therefore assumes the
# project Gateway is configured with the NONE authorizer.
# KB_ID       format: 10-character alphanumeric string from the KB console
# REGION:     your AWS region, e.g. "us-east-1"
# MEMORY_ID   format: shown in the AgentCore Memory console

GATEWAY_URL = "https://customersupportgateway-dtrgqlgg5e.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
KB_ID       = "PTJIGQV5AL"
REGION      = "us-east-1"
MEMORY_ID   = "CustomerSupportMemory-TSCzMTExsK"


# ── Model and Clients ──────────────────────────────────────────────────────────
model_id = "global.amazon.nova-2-lite-v1:0"

model = BedrockModel(model_id=model_id)

memory_client = MemoryClient(region_name=REGION)

_bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)


# ── Namespace Helper ───────────────────────────────────────────────────────────
def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    strategies = mem_client.get_memory_strategies(memory_id)
    namespaces: Dict[str, str] = {}
    for strategy in strategies:
        strategy_type = strategy.get("type")
        namespace_templates = strategy.get("namespaceTemplates")
        if namespace_templates:
            namespace = namespace_templates[0]
        else:
            # Fall back to older AgentCore response shape
            namespace = strategy.get("namespaces", [None])[0]
        if strategy_type and namespace:
            namespaces[strategy_type] = namespace
    return namespaces


# ── Memory Hook ─────────────────────────────────────────────────────────────────
class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.namespaces = get_namespaces(self.memory_client, self.memory_id)
        logger.info("Namespaces loaded: %s", self.namespaces)

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        messages = event.agent.messages
        if not messages:
            return

        last_message = messages[-1]
        if last_message.get("role") != "user":
            return

        content = last_message.get("content", [])
        if not content or not isinstance(content[0], dict) or "text" not in content[0]:
            # Not a plain-text user message (likely a tool result)
            return

        user_query = content[0]["text"]
        self.original_query = user_query

        try:
            context_lines = []
            for strategy_type, namespace_template in self.namespaces.items():
                resolved_namespace = namespace_template.format(actorId=self.actor_id)
                memories = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=resolved_namespace,
                    query=user_query,
                    top_k=5,
                )
                for memory in memories:
                    if isinstance(memory, dict):
                        text = memory.get("content", {}).get("text", "").strip()
                        if text:
                            context_lines.append(f"[{strategy_type}] {text}")

            if context_lines:
                context_block = "\n".join(context_lines)
                last_message["content"][0]["text"] = (
                    f"Customer Context:\n{context_block}\n\n{user_query}"
                )
                logger.info(
                    "Retrieved %d memory items for actor %s",
                    len(context_lines),
                    self.actor_id,
                )
        except Exception as exc:
            logger.error("Failed to retrieve customer context: %s", exc)

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        try:
            messages = event.agent.messages
            customer_query = None
            agent_response = None

            for message in reversed(messages):
                role = message.get("role")
                content = message.get("content", [])

                if role == "assistant" and agent_response is None:
                    if isinstance(content, list) and content:
                        agent_response = content[0].get("text", "")
                    else:
                        agent_response = str(content)

                elif role == "user" and customer_query is None:
                    if (
                        isinstance(content, list)
                        and content
                        and isinstance(content[0], dict)
                        and "text" in content[0]
                    ):
                        customer_query = content[0]["text"]
                        break
                    

            customer_query = getattr(self, "original_query", None) or customer_query   
            if customer_query and agent_response:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=self.actor_id,
                    session_id=self.session_id,
                    messages=[
                        (customer_query, "USER"),
                        (agent_response, "ASSISTANT"),
                    ],
                )
                logger.info("Saved interaction to memory for actor %s", self.actor_id)
        except Exception as exc:
            logger.error("Failed to save support interaction: %s", exc)

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


class ToolTraceHook(HookProvider):
    """Logs every tool request and result so call IDs can be matched."""

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        registry.add_callback(BeforeToolCallEvent, self.log_call)
        registry.add_callback(AfterToolCallEvent, self.log_result)

    def log_call(self, event: BeforeToolCallEvent):
        tool_use = event.tool_use
        logger.info(
            "Tool Invocation -> ID: %s, Name: %s, Args: %s",
            tool_use.get("toolUseId"), tool_use.get("name"), tool_use.get("input"),
        )

    def log_result(self, event: AfterToolCallEvent):
        result = event.result or {}
        logger.info(
            "Tool Result     -> ID: %s, Name: %s, Status: %s, Payload: %s",
            result.get("toolUseId"), event.tool_use.get("name"),
            result.get("status"), json.dumps(result.get("content"), default=str),
        )
# ── Knowledge Base Tool ────────────────────────────────────────────────────────
@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    if not KB_ID or KB_ID == "<kbid>":
        return "Knowledge base not configured."

    try:
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )
    except Exception as exc:
        logger.error("Knowledge base retrieval failed: %s", exc)
        return "Knowledge base is temporarily unavailable."

    results = resp.get("retrievalResults", [])
    if not results:
        return f"No information found for: {query}"

    chunks = [r["content"]["text"] for r in results]
    return "\n---\n".join(chunks)


# ── Loyalty Discount Tool (Code Interpreter) ───────────────────────────────────
def _parse_interpreter_json(response) -> dict:
    """Extract the business JSON printed by the sandbox script."""
    for event in response["stream"]:
        result = event.get("result", {})
        if result.get("isError"):
            raise RuntimeError(f"Code Interpreter error: {result}")
        texts = []
        structured = result.get("structuredContent") or {}
        if structured.get("stdout"):
            texts.append(structured["stdout"])
        for item in result.get("content", []):
            if item.get("type") == "text" and item.get("text"):
                texts.append(item["text"])
        for text in texts:
            for line in reversed(text.strip().splitlines()):
                if line.strip().startswith("{"):
                    return json.loads(line.strip())
    raise ValueError("No JSON found in Code Interpreter output")

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """Calculate the loyalty discount for a customer order using the AgentCore Code Interpreter.

    Args:
        loyalty_points: Customer's current points balance
        tier: Customer tier — Silver, Gold, or Platinum
        order_total: Order total in USD
        product_category: standard, device, or fresh

    Returns:
        JSON string containing points_redeemed, tier_discount_pct, final_total,
        remaining_points
    """
    tier_cap = str(tier).capitalize()
    order_val = float(order_total)
    pts_val = int(loyalty_points)
    cat_val = str(product_category).lower()

    # Self-contained calculation code for sandbox execution
    code = f"""
import json
earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}
tier = "{tier_cap}"
order_total = {order_val}
loyalty_points = {pts_val}
category = "{cat_val}"
tier_pct = tier_rates.get(tier, 0.0)

# 100 points = $1; cap at 50% of order; then floor to 500-point blocks
max_points_by_cap = int(order_total * 0.50 * 100)
usable_points = min(loyalty_points, max_points_by_cap)
points_redeemed = (usable_points // 500) * 500
redeemed_val = points_redeemed / 100
subtotal_after_points = order_total - redeemed_val
tier_discount_amount = subtotal_after_points * tier_pct
final_total = round(subtotal_after_points - tier_discount_amount, 2)
total_savings = round(order_total - final_total, 2)
points_earned = int(final_total * earn_rates.get(category, 1))
remaining_points = loyalty_points - points_redeemed + points_earned

print(json.dumps({{
    "points_redeemed": points_redeemed,
    "tier_discount_pct": int(round(tier_pct * 100)),
    "final_total": final_total,
    "remaining_points": remaining_points,
    "total_savings": total_savings,
    "points_earned": points_earned,
}}))
"""

    try:
        with code_session(REGION) as code_client:
            response = code_client.invoke("executeCode", {
                "code": code,
                "language": "python",
                "clearContext": True,
            })
            parsed = _parse_interpreter_json(response)

        return json.dumps({
            "points_redeemed": int(parsed["points_redeemed"]),
            "tier_discount_pct": int(parsed["tier_discount_pct"]),
            "final_total": float(parsed["final_total"]),
            "remaining_points": int(parsed["remaining_points"]),
            "total_savings": float(parsed.get("total_savings", 0.0)),
            "points_earned": int(parsed.get("points_earned", 0)),
        })

    except Exception as exc:
        logger.warning("Code Interpreter unavailable (%s). Applying tier-only fallback.", exc)
        rate = {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}.get(tier_cap, 0.0)
        final_total = round(order_val * (1.0 - rate), 2)
        return json.dumps({
            "points_redeemed": 0,
            "tier_discount_pct": int(round(rate * 100)),
            "final_total": final_total,
            "remaining_points": pts_val,
            "total_savings": round(order_val - final_total, 2),
            "points_earned": 0,
            "note": "Code interpreter unavailable; points redemption skipped, tier discount applied.",
        })


# ── Agent Entrypoint ─────────────────────────────────────────────────────────────
@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    user_input = payload.get("prompt", "Hello!")
    actor_id = payload.get("customer_id", "anonymous-customer")
    session_id = payload.get("session_id") or str(uuid.uuid4())

    logger.info("Actor %s | Session %s | Prompt: %s", actor_id, session_id, user_input[:100])

    try:
        memory_hook = MemoryHook(
            actor_id=actor_id,
            session_id=session_id,
            memory_client=memory_client,
            memory_id=MEMORY_ID,
        )

        agent_core_browser = AgentCoreBrowser(region=REGION)

        tools = [
            search_knowledge_base,
            calculate_loyalty_discount,
            agent_core_browser.browser,
        ]

        client = MCPClient(lambda: streamable_http_client(url=GATEWAY_URL))
        with client:
            gateway_tools = client.list_tools_sync()
            logger.info("Discovered %d tools from Gateway", len(gateway_tools))
            tools.extend(gateway_tools)

            agent = Agent(
                model=model,
                system_prompt=(
                    "You are a helpful customer support assistant for an "
                    "e-commerce company. You can track orders and process "
                    "refunds through the Gateway tools, answer product and "
                    "policy questions using search_knowledge_base, calculate "
                    "exact loyalty discounts using calculate_loyalty_discount, "
                    "and browse the web for real-time information when needed. "
                    "Always use the appropriate tool rather than guessing. "
                    "Before processing any refund, first look up the order with "
                    "the order tracking tool and pass its exact item price as the "
                    "refund amount; never submit a refund with a missing or zero amount. "
                    "Be clear, concise, and empathetic."
                ),
                tools=tools,
                hooks=[memory_hook, ToolTraceHook()],
            )

            response = agent(user_input)

        return response.message["content"][0]["text"]

    except Exception as exc:
        logger.error("Agent invocation failed: %s", exc)
        return (
            "I'm sorry, something went wrong while processing your request. "
            "Please try again in a moment."
        )


# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()