"""Customer support demo agent instrumented with AgentProbe SDK."""

from __future__ import annotations

import json
import os
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import agentprobe as ap
import httpx

# Sample document repository for search_docs
DOCS_KNOWLEDGE_BASE = [
    {
        "id": "doc_shipping",
        "title": "Shipping and Delivery Timelines",
        "content": (
            "Standard delivery takes 3-5 business days. Express delivery takes 1-2 business days. "
            "Tracking details are emailed once the courier scans the package. Deliveries operate Monday through Saturday."
        ),
    },
    {
        "id": "doc_refunds",
        "title": "Software License Refund Policy",
        "content": (
            "Digital software licenses that have been opened or activated are non-refundable. "
            "Unopened or unactivated keys can be returned for a full refund within 14 days of purchase."
        ),
    },
    {
        "id": "doc_mfa",
        "title": "Multi-Factor Authentication (MFA) Reset Guide",
        "content": (
            "To reset your MFA credentials: navigate to Account Settings > Security > Two-Factor Authentication. "
            "Click 'Reset Device' and verify via your backup recovery code. If recovery codes are lost, contact support."
        ),
    },
    {
        "id": "doc_damaged",
        "title": "Damaged or Defective Item Replacement Policy",
        "content": (
            "If an order arrives damaged, report it within 48 hours of delivery. Include photos of the item and box. "
            "Our support team will generate a free return label and dispatch an immediate priority replacement."
        ),
    },
]

# Sample order database
ORDERS_DATABASE: dict[str, dict[str, Any]] = {
    "ORD-99241": {
        "order_id": "ORD-99241",
        "customer": "Sarah Jenkins",
        "email": "sarah.j@example.com",
        "status": "In Transit",
        "items": ["Ergonomic Mechanical Keyboard", "Wrist Rest"],
        "estimated_arrival": "Tomorrow by 5:00 PM",
        "carrier": "FedEx Express (Tracking #982341829)",
    },
    "ORD-10052": {
        "order_id": "ORD-10052",
        "customer": "Michael Chang",
        "email": "user@example.com",
        "status": "Processing in Warehouse",
        "items": ["4K Ultra-Wide Monitor"],
        "shipping_address": "123 Market St, San Francisco, CA 94105",
        "estimated_arrival": "In 3 business days",
    },
    "ORD-88123": {
        "order_id": "ORD-88123",
        "customer": "Alex Rivera",
        "email": "support@example.com",
        "status": "Delivered",
        "items": ["Noise Cancelling Studio Headphones"],
        "delivered_at": "Yesterday at 2:15 PM",
        "condition_reported": "Damaged in transit",
    },
}


# --- Agent Tools instrumented with @ap.trace_tool ---


@ap.trace_tool(name="search_docs")
def search_docs(query: str) -> list[dict[str, Any]]:
    """Search internal documentation and knowledge base articles."""
    q_words = set(re.findall(r"\w+", query.lower()))
    matches: list[dict[str, Any]] = []

    for doc in DOCS_KNOWLEDGE_BASE:
        doc_words = set(
            re.findall(r"\w+", (doc["title"] + " " + doc["content"]).lower())
        )
        overlap = len(q_words.intersection(doc_words))
        if overlap > 0:
            matches.append(
                {"doc_id": doc["id"], "title": doc["title"], "content": doc["content"]}
            )

    return matches if matches else [{"message": "No matching documentation found."}]


@ap.trace_tool(name="get_order_status")
def get_order_status(order_id: str) -> dict[str, Any]:
    """Retrieve detailed order, shipping, and fulfillment information by order ID."""
    clean_id = order_id.upper().strip()
    if clean_id in ORDERS_DATABASE:
        return {"found": True, "order": ORDERS_DATABASE[clean_id]}
    return {"found": False, "message": f"Order {order_id} not found in database."}


@ap.trace_tool(name="send_email")
def send_email(recipient: str, subject: str, body: str) -> dict[str, Any]:
    """Send an automated confirmation or notification email to a customer."""
    message_id = f"msg_{uuid.uuid4().hex[:8]}"
    return {
        "success": True,
        "message_id": message_id,
        "recipient": recipient,
        "subject": subject,
        "status": "delivered",
    }


# --- LLM Client Interfaces ---


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 150
    completion_tokens: int = 50
    cost: float = 0.001


class BaseLLM(ABC):
    """Abstract interface for LLM providers."""

    model: str = "mock-model"

    @abstractmethod
    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        """Generate response or tool calls."""


class MockLLM(BaseLLM):
    """Deterministic scripted mock LLM generating realistic tool calls and syntheses."""

    model: str = "mock-agentprobe-v1"

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        # Determine the user question from initial message
        user_msg = next((m["content"] for m in messages if m.get("role") == "user"), "")
        tool_results = [m for m in messages if m.get("role") == "tool"]

        # Case 1: Order tracking (ORD-99241)
        if "ORD-99241" in user_msg.upper():
            if not tool_results:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            name="get_order_status", arguments={"order_id": "ORD-99241"}
                        )
                    ],
                    prompt_tokens=180,
                    completion_tokens=30,
                    cost=0.0008,
                )
            return LLMResponse(
                content=(
                    "Your order ORD-99241 (Ergonomic Mechanical Keyboard, Wrist Rest) is currently In Transit "
                    "via FedEx Express (Tracking #982341829) and is scheduled to arrive tomorrow by 5:00 PM."
                ),
                prompt_tokens=240,
                completion_tokens=65,
                cost=0.0012,
            )

        # Case 2: Refund policy
        if "refund" in user_msg.lower() or "license" in user_msg.lower():
            if not tool_results:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            name="search_docs",
                            arguments={"query": "software license refund policy"},
                        )
                    ],
                    prompt_tokens=150,
                    completion_tokens=25,
                    cost=0.0006,
                )
            return LLMResponse(
                content=(
                    "According to our policy, digital software licenses that have been opened or activated are "
                    "non-refundable. Unopened or unactivated license keys can be returned for a full refund "
                    "within 14 days of purchase."
                ),
                prompt_tokens=280,
                completion_tokens=55,
                cost=0.0011,
            )

        # Case 3: Update shipping address and email confirmation (ORD-10052)
        if "ORD-10052" in user_msg.upper():
            if len(tool_results) == 0:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            name="get_order_status", arguments={"order_id": "ORD-10052"}
                        )
                    ],
                    prompt_tokens=190,
                    completion_tokens=30,
                    cost=0.0008,
                )
            if len(tool_results) == 1:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            name="send_email",
                            arguments={
                                "recipient": "user@example.com",
                                "subject": "Shipping Address Confirmed for Order ORD-10052",
                                "body": "Your shipping address has been confirmed and updated in our system.",
                            },
                        )
                    ],
                    prompt_tokens=220,
                    completion_tokens=45,
                    cost=0.0010,
                )
            return LLMResponse(
                content=(
                    "Order ORD-10052 is currently Processing in Warehouse. A confirmation email has been sent "
                    "to user@example.com confirming your shipping details."
                ),
                prompt_tokens=310,
                completion_tokens=50,
                cost=0.0013,
            )

        # Case 4: MFA Reset
        if (
            "mfa" in user_msg.lower()
            or "authentication" in user_msg.lower()
            or "multi-factor" in user_msg.lower()
        ):
            if not tool_results:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            name="search_docs",
                            arguments={
                                "query": "multi-factor authentication mfa reset"
                            },
                        )
                    ],
                    prompt_tokens=140,
                    completion_tokens=25,
                    cost=0.0006,
                )
            return LLMResponse(
                content=(
                    "To reset your MFA credentials: go to Account Settings > Security > Two-Factor Authentication. "
                    "Click 'Reset Device' and verify your identity using your backup recovery code."
                ),
                prompt_tokens=260,
                completion_tokens=60,
                cost=0.0011,
            )

        # Case 5: Damaged item replacement (ORD-88123)
        if "ORD-88123" in user_msg.upper():
            if len(tool_results) == 0:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            name="get_order_status", arguments={"order_id": "ORD-88123"}
                        )
                    ],
                    prompt_tokens=180,
                    completion_tokens=30,
                    cost=0.0008,
                )
            if len(tool_results) == 1:
                return LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            name="send_email",
                            arguments={
                                "recipient": "support@example.com",
                                "subject": "Replacement Request: Order ORD-88123 Damaged in Transit",
                                "body": "Customer reported headphones damaged in transit. Generating replacement order.",
                            },
                        )
                    ],
                    prompt_tokens=230,
                    completion_tokens=40,
                    cost=0.0010,
                )
            return LLMResponse(
                content=(
                    "I verified order ORD-88123 was delivered yesterday. A priority replacement ticket and "
                    "notification have been dispatched to support@example.com to send you a new pair of headphones."
                ),
                prompt_tokens=320,
                completion_tokens=65,
                cost=0.0014,
            )

        # Default fallback answer
        return LLMResponse(
            content="I am here to assist with your inquiries, order status lookups, and account support.",
            prompt_tokens=100,
            completion_tokens=25,
            cost=0.0004,
        )


class OpenAILikeLLM(BaseLLM):
    """Real provider client compatible with OpenAI /chat/completions API format."""

    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = (
            os.getenv("OPENAI_BASE_URL")
            or os.getenv("OPENAI_API_BASE")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30.0,
        )

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
        }
        res = self.client.post("/chat/completions", json=payload)
        res.raise_for_status()
        data = res.json()
        choice = data["choices"][0]["message"]
        tool_calls: list[ToolCall] = []

        if choice.get("tool_calls"):
            for tc in choice["tool_calls"]:
                tool_calls.append(
                    ToolCall(
                        name=tc["function"]["name"],
                        arguments=json.loads(tc["function"]["arguments"]),
                    )
                )

        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        # Approximate cost calculation for standard models
        cost = (prompt_tokens * 0.00000015) + (completion_tokens * 0.0000006)

        return LLMResponse(
            content=choice.get("content"),
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=round(cost, 6),
        )


def get_llm() -> BaseLLM:
    """Return real OpenAI-compatible provider if configured, else default to deterministic MockLLM."""
    if os.getenv("OPENAI_API_KEY"):
        return OpenAILikeLLM()
    return MockLLM()


# Available tools map
AVAILABLE_TOOLS = {
    "search_docs": search_docs,
    "get_order_status": get_order_status,
    "send_email": send_email,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search documentation and policies",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Look up order information and shipping details",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send confirmation or notification email to a customer",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["recipient", "subject", "body"],
            },
        },
    },
]


@ap.trace_agent(name="customer_support_agent", version="1.0", tags=["demo", "support"])
def run_support_agent(question: str) -> str:
    """Run customer support agent with tool calling loop."""
    llm = get_llm()
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": "You are a polite, helpful customer service agent.",
        },
        {"role": "user", "content": question},
    ]

    for _ in range(5):
        with ap.span("llm_reasoning", type="llm", model=llm.model) as s:
            s.set_input({"messages": messages})
            response = llm.generate(messages, TOOL_DEFINITIONS)
            s.set_output(
                {
                    "content": response.content,
                    "tool_calls": [tc.__dict__ for tc in response.tool_calls],
                }
            )
            s.set_usage(
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                cost=response.cost,
            )

        if response.tool_calls:
            for tc in response.tool_calls:
                tool_fn = AVAILABLE_TOOLS.get(tc.name)
                if tool_fn:
                    tool_output = tool_fn(**tc.arguments)
                    messages.append(
                        {"role": "tool", "name": tc.name, "content": tool_output}
                    )
        else:
            return response.content or "No response generated."

    return "Max conversation turns reached."
