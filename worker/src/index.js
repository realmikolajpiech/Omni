/**
 * Omni Backend — Cloudflare Worker
 *
 * Routes:
 *   POST /v1/chat/completions   AI chat proxy (xAI Grok or Groq, rate-limited for free tier)
 *   POST /v1/search             Serper web search proxy
 *   GET  /v1/status             Subscription status + daily usage
 *   POST /v1/webhook/payment    Generic payment provider webhook
 *
 * Auth:
 *   Every request must carry X-Omni-Secret (shared app secret).
 *   If Authorization: Bearer <supabase_jwt> is present, the request is treated as
 *   an authenticated user (plan looked up by user_id).
 *   Otherwise falls back to X-Device-ID for anonymous free-tier rate limiting.
 *
 * KV namespaces (bound in wrangler.toml):
 *   USAGE         key: "{id}:{YYYY-MM-DD}" → usage count string  (TTL 2 days)
 *   SUBSCRIPTIONS key: "{user_id}"         → "pro" | "free"
 *
 * Secrets (set via `wrangler secret put`):
 *   OMNI_SECRET            shared secret between app binary and this worker
 *   XAI_API_KEY            xAI Grok API key
 *   GROQ_API_KEY           Groq API key
 *   SERPER_MAIN_API_KEY    Serper.dev key for main model tool calls
 *   SERPER_FAST_API_KEY    Serper.dev key for fast action classifier
 *   SUPABASE_URL           Your Supabase project URL  (https://xxxx.supabase.co)
 *   SUPABASE_ANON_KEY      Supabase anon/publishable key
 *   SUPABASE_SERVICE_KEY   Supabase service role key (for Admin API in webhook)
 *   PAYMENT_WEBHOOK_SECRET webhook signing secret from payment provider
 */

const FREE_DAILY_LIMIT = 10;
const XAI_BASE = "https://api.x.ai/v1";
const GROQ_BASE = "https://api.groq.com/openai/v1";

// ── Entry point ───────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const url    = new URL(request.url);
    const path   = url.pathname;
    const method = request.method;

    // Webhook uses its own auth (Dodo Standard Webhooks signature), not X-Omni-Secret.
    if (path === "/v1/webhook/payment" && method === "POST")
      return handlePaymentWebhook(request, env);

    // All other endpoints require the shared app secret.
    if (request.headers.get("X-Omni-Secret") !== env.OMNI_SECRET) {
      return resp({ error: "Unauthorized" }, 401);
    }

    if (path === "/v1/chat/completions" && method === "POST")
      return handleChat(request, env);

    if (path === "/v1/search" && method === "POST")
      return handleSearch(request, env);

    if (path === "/v1/status" && method === "GET")
      return handleStatus(request, env);

    if (path === "/v1/billing/checkout_session" && method === "POST")
      return handleCreateCheckoutSession(request, env);

    if (path === "/v1/billing/session_status" && method === "GET")
      return handleSessionStatus(request, env);

    return resp({ error: "Not found" }, 404);
  },
};

// ── Chat proxy ────────────────────────────────────────────────────────────────

async function handleChat(request, env) {
  const body = await request.json();
  const model = body.model || "";
  const isStream = !!body.stream;

  // Route by model: anything with "grok" → xAI (main model, rate-limited for free)
  const isMainModel = model.toLowerCase().includes("grok");

  if (isMainModel) {
    const { id, isPro } = await resolveIdentity(request, env);

    if (!isPro) {
      const usage = await getUsage(id, env);
      if (usage >= FREE_DAILY_LIMIT) {
        return limitReachedResponse(isStream);
      }
      await incrementUsage(id, env);
    }

    return proxyChat(body, XAI_BASE, env.XAI_API_KEY);
  }

  // Fast model (Groq) — unlimited, used for internal action classification
  return proxyChat(body, GROQ_BASE, env.GROQ_API_KEY);
}

async function proxyChat(body, baseUrl, apiKey) {
  const upstream = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("Content-Type") ?? "application/json",
    },
  });
}

function limitReachedResponse(stream) {
  const msg = "You've reached the free tier limit of 10 AI queries for today. Upgrade to Pro for unlimited usage.";

  if (stream) {
    const chunk = JSON.stringify({
      choices: [{ delta: { content: msg }, finish_reason: "stop" }],
    });
    return new Response(`data: ${chunk}\n\ndata: [DONE]\n\n`, {
      headers: { "Content-Type": "text/event-stream" },
    });
  }

  return resp({
    choices: [{ message: { role: "assistant", content: msg }, finish_reason: "stop" }],
  });
}

// ── Search proxy ──────────────────────────────────────────────────────────────

async function handleSearch(request, env) {
  const body = await request.json();
  const endpoint = body._endpoint || "/search";
  const fast = !!body._fast;
  delete body._endpoint;
  delete body._fast;

  const apiKey = fast ? env.SERPER_FAST_API_KEY : env.SERPER_MAIN_API_KEY;

  const upstream = await fetch(`https://google.serper.dev${endpoint}`, {
    method: "POST",
    headers: { "X-API-KEY": apiKey, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}

// ── Status ────────────────────────────────────────────────────────────────────

async function handleStatus(request, env) {
  const { id, isPro } = await resolveIdentity(request, env);
  const usage = isPro ? 0 : await getUsage(id, env);

  return resp({
    plan: isPro ? "pro" : "free",
    daily_usage: usage,
    daily_limit: FREE_DAILY_LIMIT,
  });
}

// ── Billing: create hosted checkout session (Dodo Payments) ────────────────────
// Body: { interval: "monthly" | "yearly" }
// Returns: { checkout_url, session_id }
async function handleCreateCheckoutSession(request, env) {
  const apiKey = env.DODO_PAYMENTS_API_KEY;
  if (!apiKey) {
    return resp({ error: "DODO_PAYMENTS_API_KEY is not configured" }, 500);
  }

  let body = {};
  try {
    body = await request.json();
  } catch {
    return resp({ error: "Bad JSON" }, 400);
  }

  const interval = (body.interval || "").toLowerCase();
  if (interval !== "monthly" && interval !== "yearly") {
    return resp({ error: "interval must be 'monthly' or 'yearly'" }, 400);
  }

  // Hard-coded product IDs (as provided by the desktop app configuration).
  const MONTHLY_PRODUCT_ID = "pdt_0NZcz4InTRNEd5oEaxhJU";
  const YEARLY_PRODUCT_ID  = "pdt_0NZd2eUr7lb0XydkZATkO";
  const productId = interval === "monthly" ? MONTHLY_PRODUCT_ID : YEARLY_PRODUCT_ID;

  // Resolve identity for metadata
  const authHeader = request.headers.get("Authorization") || "";
  let userId = null;
  if (authHeader.startsWith("Bearer ") && env.SUPABASE_URL && env.SUPABASE_ANON_KEY) {
    const token = authHeader.slice(7);
    userId = await validateSupabaseToken(token, env);
  }
  const deviceId = request.headers.get("X-Device-ID") || "unknown";

  // Optional customer prefill (sent from app; safe to accept)
  const customer = {};
  if (body.customer_email) customer.email = String(body.customer_email);
  if (body.customer_name)  customer.name  = String(body.customer_name);

  const metadata = {
    source: "omni_desktop",
    interval,
    device_id: deviceId,
  };
  if (userId) metadata.user_id = userId;

  const mode = (env.DODO_ENVIRONMENT || env.DODO_ENV || "test_mode").toLowerCase();
  const base =
    mode.includes("test") ? "https://test.dodopayments.com" : "https://live.dodopayments.com";

  const returnUrl =
    env.DODO_RETURN_URL ||
    env.CHECKOUT_RETURN_URL ||
    "https://heyomni.app/thanks";

  const upstream = await fetch(`${base}/checkouts`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      product_cart: [{ product_id: productId, quantity: 1 }],
      ...(Object.keys(customer).length ? { customer } : {}),
      return_url: returnUrl,
      metadata,
    }),
  });

  const text = await upstream.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!upstream.ok) {
    return resp({ error: "Failed to create checkout session", details: data || text }, upstream.status);
  }

  return resp({
    checkout_url: data && data.checkout_url,
    session_id:   data && (data.session_id || data.id),
  });
}

// ── Payment webhook ───────────────────────────────────────────────────────────
// Expected payload: { event: "payment.completed", user_id: "<supabase_user_uuid>" }

async function handlePaymentWebhook(request, env) {
  // Read raw body first so we can verify Dodo (Standard Webhooks) signatures.
  const rawBody = await request.text();
  let body;
  try {
    body = rawBody ? JSON.parse(rawBody) : {};
  } catch {
    return resp({ error: "Invalid JSON" }, 400);
  }

  // 1) Legacy simple webhook shape (kept for backwards compatibility)
  if (body.event === "payment.completed" && body.user_id) {
    const userId = body.user_id;
    await markProForId(userId, null, env);
    return resp({ received: true, source: "legacy" });
  }

  // 2) Dodo Payments — Standard Webhooks
  //    Verify signature when a webhook secret is configured.
  const secret = env.PAYMENT_WEBHOOK_SECRET || env.DODO_WEBHOOK_SECRET;
  const hasStandardWebhookHeaders =
    request.headers.get("webhook-id") &&
    request.headers.get("webhook-timestamp") &&
    request.headers.get("webhook-signature");

  if (secret && hasStandardWebhookHeaders) {
    const ok = await verifyStandardWebhookSignature(rawBody, request, secret);
    if (!ok) {
      return resp({ error: "Invalid signature" }, 400);
    }
  }

  const eventType =
    body.type ||
    body.event ||
    (body.data && body.data.type) ||
    "";

  // Only react to successful payments; ignore other noise.
  const isPaymentSucceeded =
    typeof eventType === "string" &&
    (eventType === "payment.succeeded" ||
      eventType === "payment_success" ||
      eventType === "payment.completed");

  if (!isPaymentSucceeded) {
    return resp({ received: true, ignored: true });
  }

  // Extract product and identity from likely Dodo payload shapes.
  const data = body.data || {};
  const payment = data.payload_type === "Payment" ? data : data.payment || data.object || data;

  // Product / plan resolution
  const productId =
    payment.product_id ||
    payment.productId ||
    (payment.product && (payment.product.id || payment.product.product_id)) ||
    (payment.line_items && payment.line_items[0] && (payment.line_items[0].product_id || payment.line_items[0].productId)) ||
    (payment.product_cart && payment.product_cart[0] && payment.product_cart[0].product_id) ||
    null;

  // Map Dodo product IDs → Omni plans.
  const MONTHLY_PRODUCT_ID = "pdt_0NZcz4InTRNEd5oEaxhJU";
  const YEARLY_PRODUCT_ID  = "pdt_0NZd2eUr7lb0XydkZATkO";

  let billingInterval = null;
  if (productId === MONTHLY_PRODUCT_ID) {
    billingInterval = "monthly";
  } else if (productId === YEARLY_PRODUCT_ID) {
    billingInterval = "yearly";
  } else {
    return resp({ received: true, ignored: true, reason: "unknown_product", product_id: productId });
  }

  // Identity resolution: prefer explicit user_id from metadata, then device_id.
  const meta = payment.metadata || payment.custom_data || body.metadata || {};
  const explicitUserId =
    body.user_id ||
    meta.user_id ||
    meta.supabase_user_id ||
    null;
  const deviceId =
    meta.device_id ||
    meta.omni_device_id ||
    null;

  // Customer email from Dodo payload (used to find/create Supabase account).
  const customerEmail =
    (payment.customer && payment.customer.email) ||
    (data.customer && data.customer.email) ||
    payment.customer_email ||
    null;

  // Find or create a Supabase user for this email, unless one was already specified.
  let supabaseUserId = explicitUserId;
  if (!supabaseUserId && customerEmail) {
    supabaseUserId = await findOrCreateSupabaseUser(customerEmail, env);
  }

  // Mark pro: prefer supabaseUserId so rate limiting uses the user-scoped key.
  const primaryId = supabaseUserId || deviceId;
  if (!primaryId) {
    return resp({ received: true, ignored: true, reason: "missing_identity" });
  }
  await markProForId(primaryId, billingInterval, env);

  // Also immediately unlock the device (user may not have logged in yet).
  if (deviceId && deviceId !== primaryId) {
    await env.SUBSCRIPTIONS.put(deviceId, "pro");
  }

  // Generate a magic link so the app can auto-login the user, then store it in KV
  // keyed by the device ID so the app can poll for it.
  if (deviceId) {
    const magicLink = (supabaseUserId && customerEmail)
      ? await generateMagicLink(customerEmail, env)
      : null;
    await env.SUBSCRIPTIONS.put(
      `checkout:${deviceId}`,
      JSON.stringify({
        paid: true,
        email: customerEmail || "",
        user_id: supabaseUserId || "",
        magic_link: magicLink || "",
      }),
      { expirationTtl: 600 }, // 10-minute window for the app to pick this up
    );
  }

  return resp({ received: true, source: "dodo", billing_interval: billingInterval });
}

// Mark an id (Supabase user_id or device_id) as Pro in KV and (optionally) Supabase user_metadata.
async function markProForId(id, billingInterval, env) {
  // Fast path: KV used by the worker for rate limiting.
  await env.SUBSCRIPTIONS.put(id, "pro");

  // If this looks like a Supabase UUID and we have admin credentials, store plan on the user.
  const isUuidV4 =
    typeof id === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id);

  if (isUuidV4 && env.SUPABASE_URL && env.SUPABASE_SERVICE_KEY) {
    const metadata = { plan: "pro" };
    if (billingInterval) {
      metadata.billing_interval = billingInterval;
    }

    await fetch(`${env.SUPABASE_URL}/auth/v1/admin/users/${id}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
        "apikey": env.SUPABASE_SERVICE_KEY,
      },
      body: JSON.stringify({ user_metadata: metadata }),
    });
  }
}

// Verify Standard Webhooks signature (used by Dodo Payments).
async function verifyStandardWebhookSignature(rawBody, request, secret) {
  if (!secret) return true; // nothing to verify against

  const id   = request.headers.get("webhook-id");
  const ts   = request.headers.get("webhook-timestamp");
  const sigs = request.headers.get("webhook-signature");

  if (!id || !ts || !sigs) return false;

  // Standard Webhooks secret is "whsec_<base64>" or raw base64 — decode to bytes.
  const secretBase64 = secret.startsWith("whsec_") ? secret.slice(6) : secret;
  let secretBytes;
  try {
    secretBytes = Uint8Array.from(atob(secretBase64), c => c.charCodeAt(0));
  } catch {
    return false;
  }

  const encoder = new TextEncoder();
  const payload = encoder.encode(`${id}.${ts}.${rawBody}`);

  const key = await crypto.subtle.importKey(
    "raw",
    secretBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );

  const signature = await crypto.subtle.sign("HMAC", key, payload);
  const computedBase64 = btoa(String.fromCharCode(...new Uint8Array(signature)));
  const computedSig = `v1,${computedBase64}`;

  // Header may contain multiple signatures separated by spaces: "v1,sig1 v1,sig2"
  return sigs.split(" ").some(s => s.trim() === computedSig);
}

// ── Identity resolution ───────────────────────────────────────────────────────
// Returns { id, isPro } — id is either supabase user_id or device_id fallback.

async function resolveIdentity(request, env) {
  const authHeader = request.headers.get("Authorization") || "";

  if (authHeader.startsWith("Bearer ") && env.SUPABASE_URL && env.SUPABASE_ANON_KEY) {
    const token = authHeader.slice(7);
    const userId = await validateSupabaseToken(token, env);

    if (userId) {
      const isPro = (await env.SUBSCRIPTIONS.get(userId)) === "pro";
      return { id: userId, isPro };
    }
  }

  // Fallback: anonymous device-ID
  const deviceId = request.headers.get("X-Device-ID") || "unknown";
  const isPro = (await env.SUBSCRIPTIONS.get(deviceId)) === "pro";
  return { id: deviceId, isPro };
}

// ── Supabase token validation ─────────────────────────────────────────────────
// Calls /auth/v1/user to validate the JWT. Result cached in KV for 5 minutes
// so each unique token only triggers one Supabase round-trip per 5-min window.

async function validateSupabaseToken(token, env) {
  // Use a short prefix of the token as the cache key (tokens are long; first 40 chars are unique enough)
  const cacheKey = `jwt:${token.slice(-40)}`;
  const cached = await env.USAGE.get(cacheKey);
  if (cached) return cached === "invalid" ? null : cached;

  try {
    const res = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
      headers: {
        "Authorization": `Bearer ${token}`,
        "apikey": env.SUPABASE_ANON_KEY,
      },
    });

    if (!res.ok) {
      await env.USAGE.put(cacheKey, "invalid", { expirationTtl: 300 });
      return null;
    }

    const user = await res.json();
    const userId = user.id;
    await env.USAGE.put(cacheKey, userId, { expirationTtl: 300 }); // 5-min cache
    return userId;
  } catch {
    return null;
  }
}

// ── KV helpers ────────────────────────────────────────────────────────────────

function todayKey(id) {
  const date = new Date().toISOString().slice(0, 10); // YYYY-MM-DD UTC
  return `${id}:${date}`;
}

async function getUsage(id, env) {
  const val = await env.USAGE.get(todayKey(id));
  return parseInt(val ?? "0", 10);
}

async function incrementUsage(id, env) {
  const key = todayKey(id);
  const current = await getUsage(id, env);
  await env.USAGE.put(key, String(current + 1), { expirationTtl: 172800 }); // 2-day TTL
}

// ── Billing: session status poll ──────────────────────────────────────────────
// App polls this after opening checkout to detect payment + retrieve magic link.

async function handleSessionStatus(request, env) {
  const deviceId = request.headers.get("X-Device-ID") || "unknown";
  const raw = await env.SUBSCRIPTIONS.get(`checkout:${deviceId}`);
  if (!raw) return resp({ paid: false });
  try {
    return resp(JSON.parse(raw));
  } catch {
    return resp({ paid: false });
  }
}

// ── Supabase user find-or-create ──────────────────────────────────────────────
// Returns the Supabase user_id for the given email, creating the account if needed.

async function findOrCreateSupabaseUser(email, env) {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_KEY) return null;
  const base = env.SUPABASE_URL;
  const key  = env.SUPABASE_SERVICE_KEY;
  const headers = {
    "Content-Type":  "application/json",
    "Authorization": `Bearer ${key}`,
    "apikey":        key,
  };

  // Try creating first — returns the new user on success.
  const cr = await fetch(`${base}/auth/v1/admin/users`, {
    method:  "POST",
    headers,
    body: JSON.stringify({ email, email_confirm: true }),
  });
  if (cr.ok) return (await cr.json()).id ?? null;

  // User already exists (422) — search paginated list for a matching email.
  for (let page = 1; page <= 20; page++) {
    const lr = await fetch(
      `${base}/auth/v1/admin/users?page=${page}&per_page=1000`,
      { headers },
    );
    if (!lr.ok) break;
    const { users = [] } = await lr.json();
    const match = users.find(u => u.email?.toLowerCase() === email.toLowerCase());
    if (match) return match.id;
    if (users.length < 1000) break;
  }
  return null;
}

// ── Magic link generation ─────────────────────────────────────────────────────
// Generates a Supabase magic link that redirects to the app's local OAuth callback.

async function generateMagicLink(email, env) {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_KEY) return null;
  const res = await fetch(`${env.SUPABASE_URL}/auth/v1/admin/generate_link`, {
    method:  "POST",
    headers: {
      "Content-Type":  "application/json",
      "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
      "apikey":        env.SUPABASE_SERVICE_KEY,
    },
    body: JSON.stringify({
      type:    "magiclink",
      email,
      options: { redirect_to: "http://localhost:5579/magic-callback" },
    }),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.action_link ?? null;
}

// ── Response helper ───────────────────────────────────────────────────────────

function resp(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
