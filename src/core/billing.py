"""Billing helpers (Pro checkout) — talks to the Omni Worker backend.

This creates a Dodo Payments Checkout Session server-side and returns a hosted checkout_url.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
import webbrowser

from src.core.config import BACKEND_URL, CHECKOUT_SESSION_URL, DEVICE_ID, OMNI_SECRET


def create_pro_checkout_url(*, interval: str, access_token: str | None, customer_email: str | None = None, customer_name: str | None = None) -> str:
    interval = (interval or "").strip().lower()
    if interval not in ("monthly", "yearly"):
        raise ValueError("interval must be 'monthly' or 'yearly'")

    payload: dict = {"interval": interval}
    if customer_email:
        payload["customer_email"] = customer_email
    if customer_name:
        payload["customer_name"] = customer_name

    headers = {
        "Content-Type": "application/json",
        "X-Omni-Secret": OMNI_SECRET,
        "X-Device-ID": DEVICE_ID,
        "User-Agent": "OmniApp/0.5.0",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    print(f"[Billing] Creating session at {CHECKOUT_SESSION_URL}")
    print(f"[Billing] Payload: {json.dumps(payload)}")
    print(f"[Billing] Headers: {headers}")

    req = urllib.request.Request(
        CHECKOUT_SESSION_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            raw_resp = r.read()
            print(f"[Billing] Raw response: {raw_resp}")
            data = json.loads(raw_resp)
    except Exception as e:
        if hasattr(e, "read"):
            try:
                err_body = e.read().decode("utf-8")
                print(f"[Billing] Error response body: {err_body}")
            except Exception as read_err:
                print(f"[Billing] Could not read error body: {read_err}")
        if hasattr(e, "headers"):
            print(f"[Billing] Error headers: {e.headers}")
        print(f"[Billing] Network error: {e}")
        raise

    checkout_url = (data or {}).get("checkout_url") or ""
    if not checkout_url:
        print(f"[Billing] Failed response data: {data}")
        raise RuntimeError(f"Checkout session failed: {data!r}")
    
    print(f"[Billing] Success! URL: {checkout_url}")
    return checkout_url


def poll_checkout_payment(
    on_payment,
    stop_event: threading.Event | None = None,
    interval_secs: float = 3,
    timeout_secs: float = 300,
) -> threading.Thread:
    """Poll /v1/billing/session_status until payment is confirmed.

    Calls *on_payment(data)* once when the server returns ``paid: true``.
    Stops automatically after *timeout_secs* or when *stop_event* is set.
    Returns the daemon thread so the caller can join/cancel if needed.
    """
    def _run():
        deadline = time.time() + timeout_secs
        while time.time() < deadline:
            if stop_event and stop_event.is_set():
                break
            try:
                req = urllib.request.Request(
                    f"{BACKEND_URL}/v1/billing/session_status",
                    headers={
                        "X-Omni-Secret": OMNI_SECRET,
                        "X-Device-ID":   DEVICE_ID,
                    },
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = json.loads(r.read())
                if data.get("paid"):
                    on_payment(data)
                    break
            except Exception:
                pass
            time.sleep(interval_secs)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def open_pro_checkout(*, interval: str, access_token: str | None, customer_email: str | None = None, customer_name: str | None = None) -> str:
    """Create a session and open the checkout URL in the browser. Returns the URL."""
    url = create_pro_checkout_url(
        interval=interval,
        access_token=access_token,
        customer_email=customer_email,
        customer_name=customer_name,
    )
    webbrowser.open(url)
    return url

