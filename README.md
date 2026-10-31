# Customer Support AI Agent

A customer support agent built with the [Strands Agents SDK](https://strandsagents.com/) and deployed on [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/). It remembers customers across sessions, answers product and policy questions from a knowledge base, calculates loyalty discounts in a secure sandbox, browses the web, and calls backend tools through an AgentCore Gateway.

---

## Features

| Capability | How it works |
|------------|--------------|
| **Long-term memory** | A `MemoryHook` retrieves relevant customer facts and preferences before each turn, and saves each completed turn afterward. |
| **Knowledge base search** | `search_knowledge_base` queries an Amazon Bedrock Knowledge Base for product specs, return policies, warranty info, loyalty program details, and order status definitions. |
| **Loyalty discount calculator** | `calculate_loyalty_discount` runs exact arithmetic in the AgentCore Code Interpreter, with a tier-discount-only fallback if the sandbox is unavailable. |
| **Web browsing** | `AgentCoreBrowser` gives the agent a live browser tool. |
| **Gateway tools** | Additional tools are discovered at runtime from an AgentCore Gateway over MCP. |

---

## Architecture

```
                 ┌──────────────────────────────┐
  Customer ───▶  │  AgentCore Runtime (invoke)  │
                 └──────────────┬───────────────┘
                                │
                      ┌─────────▼─────────┐
                      │  Strands Agent    │  Nova 2 Lite (Bedrock)
                      └─────────┬─────────┘
        ┌──────────────┬────────┴───────┬───────────────┬──────────────┐
        ▼              ▼                ▼               ▼              ▼
  MemoryHook     Knowledge Base   Code Interpreter   Browser     Gateway (MCP)
 (AgentCore       (Bedrock         (loyalty          (AgentCore   (backend tools)
  Memory)          retrieve)        discounts)        Browser)
```

---

## Loyalty Discount Rules

The calculator takes the customer's points balance, tier, order total, and product category.

| Category | Points earned per dollar |
|----------|--------------------------|
| `standard` | 1 |
| `device` | 2 |
| `fresh` | 5 |

| Tier | Discount |
|------|----------|
| Silver | 0% |
| Gold | 10% |
| Platinum | 15% |

- Points redeemed are floored to the nearest 500 and capped at 50% of the order.
- The tier discount is applied to the subtotal **after** points are redeemed.
- The result includes the final total, total savings, points earned, and remaining points.

---

## Prerequisites

- An AWS account with Bedrock model access enabled for Amazon Nova 2 Lite
- AWS credentials configured locally
- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/)
- The AgentCore CLI (`agentcore`)

AWS resources to create beforehand:

1. **AgentCore Memory** with semantic and user-preference strategies
2. **Bedrock Knowledge Base** loaded with your product and support documents
3. **AgentCore Gateway** with its tool targets (this starter uses an unsigned MCP connection, so the Gateway must use the `NONE` authorizer)

---

## Configuration

Edit the values near the top of `main.py`:

```python
GATEWAY_URL = "https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp"
KB_ID       = "<10-character knowledge base ID>"
REGION      = "us-east-1"
MEMORY_ID   = "<memory ID>"
```

> **Do not commit real resource IDs or credentials.** Keep placeholders in the repository, or read the values from environment variables.

---

## Running

**Locally**

```bash
uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
```

For local CLI testing, comment out `app.run()` and uncomment `main()` at the bottom of `main.py`.

**Deploy to AgentCore**

```bash
agentcore deploy
```

**Invoke the deployed agent**

```bash
agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
```

### Request payload

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `prompt` | string | yes | The customer's message |
| `customer_id` | string | no | Unique customer identifier, used as the memory actor ID |
| `session_id` | string | no | Session identifier. A UUID is generated if absent |

### Example prompts

```bash
# Policy question (knowledge base)
agentcore invoke '{"prompt": "What is your return policy for devices?", "customer_id": "CUST-123", "session_id": "s1"}'

# Loyalty calculation (code interpreter)
agentcore invoke '{"prompt": "I am a Gold member with 2400 points. What is my price on a $300 device order?", "customer_id": "CUST-123", "session_id": "s1"}'

# Memory across sessions
agentcore invoke '{"prompt": "I prefer email over phone support.", "customer_id": "CUST-123", "session_id": "s1"}'
agentcore invoke '{"prompt": "How should you contact me?", "customer_id": "CUST-123", "session_id": "s2"}'
```

---

## Project Structure

```
.
├── main.py       # Agent, memory hook, tools, and AgentCore entrypoint
└── README.md
```

### Key components in `main.py`

- `get_namespaces()` maps each memory strategy type to its namespace template.
- `MemoryHook` is a `HookProvider` that retrieves memories on `MessageAddedEvent` and saves turns on `AfterInvocationEvent`.
- `search_knowledge_base()` is a `@tool` wrapping the Bedrock `retrieve` API.
- `calculate_loyalty_discount()` is a `@tool` that runs generated Python in the Code Interpreter.
- `invoke()` is the `@app.entrypoint` that wires the tools, hooks, and Gateway into the agent.

---

## Tech Stack

Python · Strands Agents SDK · Amazon Bedrock (Nova 2 Lite) · AgentCore Runtime, Memory, Gateway, Code Interpreter, Browser · Bedrock Knowledge Bases · Model Context Protocol (MCP)

---

## Cost and Cleanup

Running this agent incurs AWS charges. When you finish, delete the Runtime deployment, Gateway, Memory, and Knowledge Base resources.

---

## License

Add a license of your choice (for example MIT) before publishing.
