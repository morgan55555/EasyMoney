"""Web Push schemas (AGENTS.md §2.14).

The subscribe body mirrors the browser's ``PushSubscription.toJSON()`` shape
(``endpoint`` + ``keys.p256dh`` / ``keys.auth``), so the frontend can POST the
subscription straight through.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PushSubscriptionKeys(BaseModel):
    """The browser's push subscription keys (base64url, no padding)."""

    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=512)
    keys: PushSubscriptionKeys


class PushUnsubscribeRequest(BaseModel):
    """Which subscription to forget: the endpoint URL."""

    endpoint: str = Field(min_length=1, max_length=512)


class PushVapidKeyResponse(BaseModel):
    """The base64url (no padding) VAPID public key."""

    key: str
