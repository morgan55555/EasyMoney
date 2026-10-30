#!/usr/bin/env python3
"""Generate a VAPID key pair for Web Push (AGENTS.md §2.14).

Prints ready-to-paste ``.env`` lines. The keys are per-deployment secrets (like
``JWT_SECRET``): share them across all backend replicas, never commit them.

The output format is exactly what the push stack expects:

* ``VAPID_PRIVATE_KEY`` — the EC P-256 private key, DER/PKCS8, base64url (no
  padding). This is what ``py_vapid.Vapid.from_string`` (via ``pywebpush``)
  consumes; the public key is the frontend's ``applicationServerKey``.
* ``VAPID_PUBLIC_KEY``  — the public point, X9.62 uncompressed (0x04 || X || Y),
  base64url (no padding). This is what the browser uses in
  ``PushManager.subscribe({ applicationServerKey })``.

Usage::

    python backend/scripts/generate_vapid.py

``cryptography`` is a transitive dependency of ``pywebpush`` (``py-vapid``).
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64u(data: bytes) -> str:
    """base64url without padding — the form both VAPID and Web Push use."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    private = key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    print("VAPID_PUBLIC_KEY=" + b64u(public))
    print("VAPID_PRIVATE_KEY=" + b64u(private))
    print("VAPID_SUBJECT=mailto:you@yourdomain.example")


if __name__ == "__main__":
    main()
