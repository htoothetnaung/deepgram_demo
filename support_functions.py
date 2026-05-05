"""In-memory support tools for the Deepgram voice customer-support demo."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_priority(priority: str) -> str:
    normalized = (priority or "normal").strip().lower()
    return normalized if normalized in {"low", "normal", "high", "urgent"} else "normal"


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


CUSTOMERS_DB: dict[str, dict[str, Any]] = {
    "CUST-1001": {
        "customer_id": "CUST-1001",
        "name": "Maya Patel",
        "email": "maya.patel@example.com",
        "phone": "+1-415-555-0101",
        "tier": "gold",
        "status": "active",
        "preferred_channel": "voice",
        "open_cases": 1,
        "last_contact_at": "2026-04-20T14:12:00+00:00",
    },
    "CUST-1002": {
        "customer_id": "CUST-1002",
        "name": "Jordan Lee",
        "email": "jordan.lee@example.com",
        "phone": "+1-646-555-0129",
        "tier": "standard",
        "status": "active",
        "preferred_channel": "chat",
        "open_cases": 0,
        "last_contact_at": "2026-04-21T09:40:00+00:00",
    },
}

ORDERS_DB: dict[str, dict[str, Any]] = {
    "ORD-9001": {
        "order_id": "ORD-9001",
        "customer_id": "CUST-1001",
        "status": "in_transit",
        "items": ["Wireless Headset"],
        "carrier": "UPS",
        "tracking_number": "1Z999AA10123456784",
        "eta": "2026-04-25",
        "amount_paid": 129.99,
        "refund_eligible": True,
    },
    "ORD-9002": {
        "order_id": "ORD-9002",
        "customer_id": "CUST-1002",
        "status": "delivered",
        "items": ["USB-C Dock"],
        "carrier": "FedEx",
        "tracking_number": "449044304137821",
        "eta": "2026-04-22",
        "amount_paid": 89.00,
        "refund_eligible": True,
    },
}

REFUNDS_DB: dict[str, dict[str, Any]] = {"refunds": {}, "next_id": 1}
ESCALATIONS_DB: dict[str, dict[str, Any]] = {"tickets": {}, "next_id": 1}


def get_customer_data(customer_id: str) -> dict[str, Any]:
    customer_key = (customer_id or "").strip().upper()
    customer = CUSTOMERS_DB.get(customer_key)
    if not customer:
        return {"error": f"Customer '{customer_id}' not found"}
    return {"customer": customer}


def check_order(order_id: str) -> dict[str, Any]:
    order_key = (order_id or "").strip().upper()
    order = ORDERS_DB.get(order_key)
    if not order:
        return {"error": f"Order '{order_id}' not found"}
    return {"order": order}


def refund(
    amount: float | str,
    order_id: str | None = None,
    reason: str = "customer_request",
) -> dict[str, Any]:
    normalized_amount = _safe_float(amount)
    if normalized_amount is None or normalized_amount <= 0:
        return {"error": "Invalid refund amount. Amount must be greater than 0."}

    if not order_id:
        return {"error": "order_id is required to process a refund."}

    order_key = order_id.strip().upper()
    order = ORDERS_DB.get(order_key)
    if not order:
        return {"error": f"Order '{order_id}' not found"}
    if not order.get("refund_eligible", False):
        return {"error": f"Order '{order_id}' is not eligible for refund."}

    max_amount = float(order["amount_paid"])
    if normalized_amount > max_amount:
        return {
            "error": (
                f"Requested refund ${normalized_amount:.2f} exceeds paid amount ${max_amount:.2f}."
            )
        }

    refund_id = f"RF-{REFUNDS_DB['next_id']:05d}"
    REFUNDS_DB["next_id"] += 1
    refund_record = {
        "refund_id": refund_id,
        "order_id": order_key,
        "amount": round(normalized_amount, 2),
        "reason": reason,
        "status": "approved",
        "created_at": _utc_now(),
    }
    REFUNDS_DB["refunds"][refund_id] = refund_record

    return {
        "refund": refund_record,
        "message": f"Refund {refund_id} approved for ${normalized_amount:.2f}.",
    }


def escalate(reason: str, priority: str = "normal", customer_id: str | None = None) -> dict[str, Any]:
    ticket_id = f"ESC-{ESCALATIONS_DB['next_id']:05d}"
    ESCALATIONS_DB["next_id"] += 1

    normalized_customer_id = (customer_id or "").strip().upper() or None
    if normalized_customer_id and normalized_customer_id not in CUSTOMERS_DB:
        return {"error": f"Customer '{customer_id}' not found for escalation."}

    ticket = {
        "ticket_id": ticket_id,
        "reason": reason or "No reason provided",
        "priority": _normalize_priority(priority),
        "customer_id": normalized_customer_id,
        "queue": "specialist_support",
        "status": "open",
        "created_at": _utc_now(),
    }
    ESCALATIONS_DB["tickets"][ticket_id] = ticket
    return {"escalation": ticket, "message": f"Escalation {ticket_id} created."}


# Common aliases used across prompts and configs
check_order_status = check_order
initiate_refund = refund


FUNCTION_MAP = {
    "get_customer_data": get_customer_data,
    "check_order": check_order,
    "check_order_status": check_order_status,
    "refund": refund,
    "initiate_refund": initiate_refund,
    "escalate": escalate,
}
