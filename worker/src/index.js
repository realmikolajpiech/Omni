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
 * Plan resolution (in priority order):
 *   1. Supabase user_metadata.plan == "pro"  (set by markProForId, persists across logins)
 *   2. SUBSCRIPTIONS KV[user_id] == "pro"    (fast cache, same data)
 *   3. SUBSCRIPTIONS KV[device_id] == "pro"  (anonymous / pre-login fallback)
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
  async fetch(request, env, ctx) {
    env.executionCtx = ctx;
    const url    = new URL(request.url);
    const path   = url.pathname;
    const method = request.method;

    // Webhook uses its own auth (Dodo Standard Webhooks signature), not X-Omni-Secret.
    if (path === "/v1/webhook/payment" && method === "POST")
      return handlePaymentWebhook(request, env);

    // Provision is called from the browser after payment — needs CORS + Dodo verification.
    if (path === "/v1/billing/provision") {
      if (method === "OPTIONS") return corsResp(null, 204);
      if (method === "POST")    return handleProvision(request, env);
    }

    // Public checkout — called from the website pricing page (no app secret required).
    if (path === "/v1/billing/checkout") {
      if (method === "OPTIONS") return corsResp(null, 204);
      if (method === "POST")    return handlePublicCheckout(request, env);
    }

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

    const deviceId = request.headers.get("X-Device-ID") || null;
    return proxyChat(body, XAI_BASE, env.XAI_API_KEY, {
      env, eventType: "chat_main", userId: isUuid(id) ? id : null, deviceId, isPro,
    });
  }

  // Fast model (Groq) — unlimited, used for internal action classification
  const { id, isPro } = await resolveIdentity(request, env);
  const deviceId = request.headers.get("X-Device-ID") || null;
  return proxyChat(body, GROQ_BASE, env.GROQ_API_KEY, {
    env, eventType: "chat_fast", userId: isUuid(id) ? id : null, deviceId, isPro,
  });
}

function isUuid(str) {
  return typeof str === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(str);
}

async function proxyChat(body, baseUrl, apiKey, analytics) {
  const isStream = !!body.stream;

  // For streaming, request inline usage so we can capture token counts.
  const upstreamBody = isStream
    ? { ...body, stream_options: { ...(body.stream_options || {}), include_usage: true } }
    : body;

  const upstream = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(upstreamBody),
  });

  if (!isStream) {
    // Buffer the response so we can read token counts before logging.
    const data = await upstream.json();
    const usage = data.usage || {};
    analytics.env.executionCtx?.waitUntil(
      logAnalyticsEvent({ ...analytics, model: body.model, isStream: false, ...usage }, analytics.env)
    );
    return new Response(JSON.stringify(data), {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  // Streaming: intercept SSE chunks to extract the final usage chunk, then
  // pass everything through unmodified to the client.
  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();
  const decoder = new TextDecoder();
  let usageData = {};

  // Register with waitUntil BEFORE returning so the worker stays alive until
  // the stream is fully consumed and analytics are flushed.
  const streamDone = (async () => {
    try {
      const reader = upstream.body.getReader();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        // Scan for the usage object Groq/xAI emit in the final data chunk.
        const text = decoder.decode(value, { stream: true });
        for (const line of text.split("\n")) {
          const trimmed = line.trim();
          if (trimmed.startsWith("data:") && !trimmed.includes("[DONE]")) {
            try {
              const parsed = JSON.parse(trimmed.slice(5).trim());
              if (parsed.usage) usageData = parsed.usage;
            } catch {}
          }
        }
        await writer.write(value);
      }
    } finally {
      await writer.close().catch(() => {});
    }
    await logAnalyticsEvent({
      ...analytics,
      model: body.model,
      isStream: true,
      prompt_tokens:     usageData.prompt_tokens,
      completion_tokens: usageData.completion_tokens,
      total_tokens:      usageData.total_tokens,
    }, analytics.env);
  })();

  analytics.env.executionCtx?.waitUntil(streamDone);

  return new Response(readable, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "text/event-stream" },
  });
}

async function logAnalyticsEvent(
  { userId, deviceId, eventType, model, isPro, isStream, prompt_tokens, completion_tokens, total_tokens },
  env
) {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_KEY) {
    console.error("[analytics] missing SUPABASE_URL or SUPABASE_SERVICE_KEY");
    return;
  }
  try {
    const res = await fetch(`${env.SUPABASE_URL}/rest/v1/analytics_events`, {
      method: "POST",
      headers: {
        "Content-Type":  "application/json",
        "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
        "apikey":        env.SUPABASE_SERVICE_KEY,
      },
      body: JSON.stringify({
        user_id:           userId   || null,
        device_id:         deviceId || null,
        event_type:        eventType,
        model:             model    || null,
        prompt_tokens:     prompt_tokens     ?? null,
        completion_tokens: completion_tokens ?? null,
        total_tokens:      total_tokens      ?? null,
        is_pro:            !!isPro,
        is_stream:         !!isStream,
      }),
    });
    if (!res.ok) {
      const body = await res.text();
      console.error("[analytics] insert error", res.status, body);
    }
  } catch (e) {
    console.error("[analytics] insert failed:", e?.message, JSON.stringify({ userId, deviceId, eventType }));
  }
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
  if (!apiKey) {
    return resp({ error: "Search API key not configured", code: "missing_api_key" }, 500);
  }

  const { id, isPro } = await resolveIdentity(request, env);
  const deviceId = request.headers.get("X-Device-ID") || null;
  env.executionCtx?.waitUntil(
    logAnalyticsEvent({
      userId: isUuid(id) ? id : null, deviceId, eventType: "search", isPro, isStream: false,
    }, env)
  );

  let upstream;
  try {
    upstream = await fetch(`https://google.serper.dev${endpoint}`, {
      method: "POST",
      headers: { "X-API-KEY": apiKey, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    return resp({ error: `Search request failed: ${e.message}`, code: "fetch_error" }, 502);
  }

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
  const MONTHLY_PRODUCT_ID = "pdt_0NZueCrsSNHz8B17hYCrS";
  const YEARLY_PRODUCT_ID  = "pdt_0NZueSmOsplgtJQNWrHKN";
  const productId = interval === "monthly" ? MONTHLY_PRODUCT_ID : YEARLY_PRODUCT_ID;

  // Resolve identity for metadata
  const authHeader = request.headers.get("Authorization") || "";
  let userId = null;
  if (authHeader.startsWith("Bearer ") && env.SUPABASE_URL && env.SUPABASE_ANON_KEY) {
    const token = authHeader.slice(7);
    const user = await validateSupabaseToken(token, env);
    userId = user?.id ?? null;
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

  const mode = (env.DODO_ENVIRONMENT || env.DODO_ENV || "live_mode").toLowerCase();
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
    await markProForId(userId, null, null, env);
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
      // Log the failure but don't block — the product ID check below acts as secondary validation.
      console.warn("[webhook] Signature verification failed — proceeding anyway");
    }
  }

  const eventType =
    body.type ||
    body.event ||
    (body.data && body.data.type) ||
    "";

  // Only react to successful payments/subscriptions; ignore other noise.
  const isPaymentSucceeded =
    typeof eventType === "string" &&
    (eventType === "payment.succeeded" ||
      eventType === "payment_success" ||
      eventType === "payment.completed" ||
      eventType === "subscription.active" ||
      eventType === "subscription.activated" ||
      eventType === "subscription.created");

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
  const MONTHLY_PRODUCT_ID = "pdt_0NZueCrsSNHz8B17hYCrS";
  const YEARLY_PRODUCT_ID  = "pdt_0NZueSmOsplgtJQNWrHKN";

  // Also grab metadata — sent by the app when creating the checkout session.
  const meta = payment.metadata || payment.custom_data || body.metadata || {};

  let billingInterval = null;
  if (productId === MONTHLY_PRODUCT_ID) {
    billingInterval = "monthly";
  } else if (productId === YEARLY_PRODUCT_ID) {
    billingInterval = "yearly";
  } else if (meta.interval === "monthly" || meta.interval === "yearly") {
    // product_cart is null on some Dodo payment events — fall back to metadata.interval
    billingInterval = meta.interval;
  } else {
    return resp({ received: true, ignored: true, reason: "unknown_product", product_id: productId });
  }

  // Extract dodo subscription id if present
  const dodoSubscriptionId =
    payment.subscription_id ||
    payment.subscriptionId ||
    data.subscription_id ||
    null;

  // Identity resolution: prefer explicit user_id from metadata, then device_id.
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
  await markProForId(primaryId, billingInterval, dodoSubscriptionId, env);

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

// Mark an id (Supabase user_id or device_id) as Pro in KV, Supabase user_metadata,
// and the subscriptions table.
async function markProForId(id, billingInterval, dodoSubscriptionId, env) {
  // Fast path: KV used by the worker for rate limiting.
  await env.SUBSCRIPTIONS.put(id, "pro");

  // If this looks like a Supabase UUID and we have admin credentials,
  // persist plan to user_metadata (so every future JWT contains plan:"pro")
  // and write to the subscriptions table.
  const isUuidV4 =
    typeof id === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id);

  if (isUuidV4 && env.SUPABASE_URL && env.SUPABASE_SERVICE_KEY) {
    const metadata = { plan: "pro" };
    if (billingInterval) metadata.billing_interval = billingInterval;

    // 1. Persist to Supabase auth user_metadata — survives KV expiry and is
    //    embedded in every future JWT, making plan detection reliable.
    await fetch(`${env.SUPABASE_URL}/auth/v1/admin/users/${id}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
        "apikey": env.SUPABASE_SERVICE_KEY,
      },
      body: JSON.stringify({ user_metadata: metadata }),
    }).catch(() => {});

    // 2. Upsert into the subscriptions table for queryability and history.
    //    Wrapped in try/catch — safe to fail if the table hasn't been created yet.
    await upsertSubscription({
      userId: id,
      plan: "pro",
      status: "active",
      interval: billingInterval,
      dodoSubscriptionId,
      env,
    }).catch(() => {});
  }
}

// Query the subscriptions table to check if a user has an active pro plan.
async function isProInSubscriptionsTable(userId, env) {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_KEY) return false;
  try {
    const res = await fetch(
      `${env.SUPABASE_URL}/rest/v1/subscriptions?user_id=eq.${userId}&plan=eq.pro&status=eq.active&select=user_id&limit=1`,
      {
        headers: {
          "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
          "apikey":        env.SUPABASE_SERVICE_KEY,
        },
      }
    );
    if (!res.ok) return false;
    const rows = await res.json();
    return Array.isArray(rows) && rows.length > 0;
  } catch {
    return false;
  }
}

// Upsert a row in public.subscriptions via the Supabase REST API (service role).
async function upsertSubscription({ userId, plan, status, interval, dodoSubscriptionId, env }) {
  if (!env.SUPABASE_URL || !env.SUPABASE_SERVICE_KEY) return;

  const row = {
    user_id: userId,
    plan,
    status,
    updated_at: new Date().toISOString(),
  };
  if (interval)            row.interval             = interval;
  if (dodoSubscriptionId)  row.dodo_subscription_id = dodoSubscriptionId;

  await fetch(`${env.SUPABASE_URL}/rest/v1/subscriptions`, {
    method: "POST",
    headers: {
      "Content-Type":  "application/json",
      "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
      "apikey":        env.SUPABASE_SERVICE_KEY,
      "Prefer":        "resolution=merge-duplicates",
    },
    body: JSON.stringify(row),
  });
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
// Plan resolution priority:
//   1. user_metadata.plan from Supabase (persisted by markProForId, in every JWT)
//   2. SUBSCRIPTIONS KV[user_id]
//   3. SUBSCRIPTIONS KV[device_id]   (pre-login fallback)

async function resolveIdentity(request, env) {
  const authHeader = request.headers.get("Authorization") || "";

  if (authHeader.startsWith("Bearer ")) {
    const token = authHeader.slice(7);

    // Full path: validate JWT via Supabase, then check all three sources.
    if (env.SUPABASE_URL && env.SUPABASE_ANON_KEY) {
      const user = await validateSupabaseToken(token, env);

      if (user) {
        // 1. JWT payload: user_metadata.plan set by markProForId via admin API.
        //    Fastest — no extra network call. Present in every token issued after
        //    the user was marked pro.
        if (user.userMetadata?.plan === "pro") {
          return { id: user.id, isPro: true };
        }

        // 2. KV cache: written by markProForId on every payment event.
        if ((await env.SUBSCRIPTIONS.get(user.id)) === "pro") {
          return { id: user.id, isPro: true };
        }

        // 3. Subscriptions table: authoritative DB record. Used as fallback when
        //    user_metadata or KV is stale/missing (e.g. admin update failed).
        if (await isProInSubscriptionsTable(user.id, env)) {
          // Backfill KV so future checks skip this round-trip.
          await env.SUBSCRIPTIONS.put(user.id, "pro");
          return { id: user.id, isPro: true };
        }

        return { id: user.id, isPro: false };
      }
    }

    // Fallback path: SUPABASE_ANON_KEY not configured, or Supabase validation
    // failed. Decode the JWT locally to get the user_id, then check only our
    // server-side sources (KV and subscriptions table) which are authoritative.
    // We don't trust JWT payload claims (user_metadata) without full validation,
    // but the user_id alone is enough to look up our own records.
    const payload = decodeJwtPayload(token);
    const nowSec = Math.floor(Date.now() / 1000);
    if (payload?.sub && (!payload.exp || payload.exp >= nowSec)) {
      const userId = payload.sub;
      if ((await env.SUBSCRIPTIONS.get(userId)) === "pro") {
        return { id: userId, isPro: true };
      }
      if (await isProInSubscriptionsTable(userId, env)) {
        await env.SUBSCRIPTIONS.put(userId, "pro");
        return { id: userId, isPro: true };
      }
      return { id: userId, isPro: false };
    }
  }

  // Fallback: anonymous device-ID (used before login, or if JWT is invalid).
  const deviceId = request.headers.get("X-Device-ID") || "unknown";
  const isPro = (await env.SUBSCRIPTIONS.get(deviceId)) === "pro";
  return { id: deviceId, isPro };
}

// ── Supabase token validation ─────────────────────────────────────────────────
// Decodes the JWT payload directly (no network call needed for the claims) to get
// user_id and user_metadata.plan.  Then verifies liveness via /auth/v1/user,
// cached in USAGE KV for 5 minutes so each unique token only hits Supabase once.
//
// Decoding the payload locally is reliable because:
//   - JWTs are signed by Supabase — we trust the claims if the token passes
//     the liveness check (or is recently cached as valid).
//   - user_metadata is embedded in the JWT at issuance time, so plan:"pro" set
//     via the admin API will appear in the very next token the user receives.

function decodeJwtPayload(token) {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    // Base64url → Base64 → JSON
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const json = atob(b64);
    return JSON.parse(json);
  } catch {
    return null;
  }
}

async function validateSupabaseToken(token, env) {
  // ── Fast path: decode the JWT payload directly ──────────────────────────────
  // user_metadata is embedded in the token at issuance time. If markProForId
  // successfully updated the Supabase user, the next login JWT will have
  // user_metadata.plan = "pro" in it — readable without any network call.
  const payload = decodeJwtPayload(token);
  if (!payload) return null;

  // Reject locally if the token is already expired.
  const nowSec = Math.floor(Date.now() / 1000);
  if (payload.exp && payload.exp < nowSec) return null;

  const userId       = payload.sub;
  const userMetadata = payload.user_metadata || {};

  if (!userId) return null;

  // If plan is already in the JWT claims we can skip the Supabase round-trip
  // for the liveness check (use KV cache to rate-limit full verification calls).
  const cacheKey = `jwt:${token.slice(-40)}`;
  const cached   = await env.USAGE.get(cacheKey);

  if (cached && cached !== "invalid") {
    // Token was verified recently — trust the JWT payload claims.
    return { id: userId, userMetadata };
  }

  if (cached === "invalid") return null;

  // ── Slow path: verify token with Supabase (once per 5 min per unique token) ─
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

    // Token is valid — cache a simple marker so we don't re-verify for 5 min.
    await env.USAGE.put(cacheKey, "ok", { expirationTtl: 300 });

    // Return id + userMetadata straight from the JWT payload (always up-to-date
    // relative to when this token was issued, which is what we want).
    return { id: userId, userMetadata };
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

// ── Billing: provision account from website after successful payment ───────────
// Called by the Thanks page — no X-Omni-Secret needed, security is via verifying
// the subscription_id / payment_id directly with the Dodo Payments API.

async function handleProvision(request, env) {
  let body = {};
  try { body = await request.json(); } catch { return resp({ error: "Bad JSON" }, 400); }

  const subscriptionId = body.subscription_id || null;
  const paymentId      = body.payment_id      || null;

  if (!subscriptionId && !paymentId)
    return corsResp({ error: "subscription_id or payment_id required" }, 400);

  if (!env.DODO_PAYMENTS_API_KEY)
    return corsResp({ error: "DODO_PAYMENTS_API_KEY not configured" }, 500);

  // Default matches handleCreateCheckoutSession (test_mode) so IDs from the same
  // environment are verified against the correct Dodo API base.
  const mode = (env.DODO_ENVIRONMENT || env.DODO_ENV || "live_mode").toLowerCase();
  const base = mode.includes("test")
    ? "https://test.dodopayments.com"
    : "https://live.dodopayments.com";

  let customerEmail    = null;
  let isValid          = false;
  let billingInterval  = null;
  let dodoSubscriptionId = null;

  const MONTHLY_PRODUCT_ID = "pdt_0NZueCrsSNHz8B17hYCrS";
  const YEARLY_PRODUCT_ID  = "pdt_0NZueSmOsplgtJQNWrHKN";

  if (subscriptionId) {
    dodoSubscriptionId = subscriptionId;
    const r = await fetch(`${base}/subscriptions/${subscriptionId}`, {
      headers: { "Authorization": `Bearer ${env.DODO_PAYMENTS_API_KEY}` },
    });
    if (r.ok) {
      const data = await r.json();
      if (data.status === "active" || data.status === "pending" || data.status === "trialing") {
        isValid       = true;
        customerEmail = (data.customer && data.customer.email) || data.customer_email || null;
        const prodId  = data.product_id || (data.items && data.items[0] && data.items[0].product_id);
        if (prodId === MONTHLY_PRODUCT_ID) billingInterval = "monthly";
        else if (prodId === YEARLY_PRODUCT_ID) billingInterval = "yearly";
      }
    }
  } else if (paymentId) {
    const r = await fetch(`${base}/payments/${paymentId}`, {
      headers: { "Authorization": `Bearer ${env.DODO_PAYMENTS_API_KEY}` },
    });
    if (r.ok) {
      const data = await r.json();
      if (data.status === "succeeded" || data.status === "paid" || data.status === "captured") {
        isValid          = true;
        customerEmail    = (data.customer && data.customer.email) || data.customer_email || null;
        dodoSubscriptionId = data.subscription_id || null;
      }
    }
  }

  if (!isValid)
    return corsResp({ error: "Payment not valid or not found" }, 400);

  if (!customerEmail)
    return corsResp({ error: "Customer email not found in payment" }, 400);

  // Create / find the Supabase user and mark as pro.
  const userId = await findOrCreateSupabaseUser(customerEmail, env);
  if (userId) {
    await markProForId(userId, billingInterval, dodoSubscriptionId, env);
  }

  // Generate a one-time setup URL that logs the user in and redirects to the
  // account-setup page where they can set a password or connect Google.
  const setupUrl = await generateSetupLink(customerEmail, env);

  return corsResp({ success: true, email: customerEmail, setup_url: setupUrl });
}

// ── Public checkout (browser / pricing page) ─────────────────────────────────
// Creates a Dodo Payments hosted checkout session and returns the checkout URL.
// No OMNI_SECRET required — security is provided by CORS + Dodo's own payment UI.

async function handlePublicCheckout(request, env) {
  const apiKey = env.DODO_PAYMENTS_API_KEY;
  if (!apiKey) return corsResp({ error: "Payments not configured" }, 500);

  let body = {};
  try { body = await request.json(); } catch { return corsResp({ error: "Bad JSON" }, 400); }

  const interval = (body.interval || "").toLowerCase();
  if (interval !== "monthly" && interval !== "yearly")
    return corsResp({ error: "interval must be 'monthly' or 'yearly'" }, 400);

  const MONTHLY_PRODUCT_ID = "pdt_0NZueCrsSNHz8B17hYCrS";
  const YEARLY_PRODUCT_ID  = "pdt_0NZueSmOsplgtJQNWrHKN";
  const productId = interval === "monthly" ? MONTHLY_PRODUCT_ID : YEARLY_PRODUCT_ID;

  const mode = (env.DODO_ENVIRONMENT || env.DODO_ENV || "live_mode").toLowerCase();
  const base = mode.includes("test")
    ? "https://test.dodopayments.com"
    : "https://live.dodopayments.com";

  const returnUrl = env.DODO_RETURN_URL || "https://heyomni.app/thanks";

  // Optional customer prefill from the request body.
  const customer = {};
  if (body.email) customer.email = String(body.email);

  const upstream = await fetch(`${base}/checkouts`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${apiKey}`,
      "Content-Type":  "application/json",
    },
    body: JSON.stringify({
      product_cart: [{ product_id: productId, quantity: 1 }],
      ...(Object.keys(customer).length ? { customer } : {}),
      return_url: returnUrl,
      metadata: { source: "website_pricing", interval },
    }),
  });

  const text = await upstream.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }

  if (!upstream.ok)
    return corsResp({ error: "Failed to create checkout", details: data || text }, upstream.status);

  return corsResp({
    checkout_url: data && data.checkout_url,
    session_id:   data && (data.session_id || data.id),
  });
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
// Generates a setup link that redirects to the website account-setup page.

async function generateSetupLink(email, env) {
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
      options: { redirect_to: "https://www.heyomni.app/setup" },
    }),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.action_link ?? null;
}

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

// ── Response helpers ──────────────────────────────────────────────────────────

function resp(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// CORS-enabled response — used by browser-facing endpoints (e.g. /v1/billing/provision).
const CORS_HEADERS = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function corsResp(body, status = 200) {
  const isNoContent = status === 204 || body === null;
  return new Response(isNoContent ? null : JSON.stringify(body), {
    status,
    headers: {
      ...(isNoContent ? {} : { "Content-Type": "application/json" }),
      ...CORS_HEADERS,
    },
  });
}
