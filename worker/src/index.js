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
const XAI_BASE  = "https://api.x.ai/v1";
const GROQ_BASE = "https://api.groq.com/openai/v1";

// ── Entry point ───────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    // Every request must carry the shared app secret
    if (request.headers.get("X-Omni-Secret") !== env.OMNI_SECRET) {
      return resp({ error: "Unauthorized" }, 401);
    }

    const url    = new URL(request.url);
    const path   = url.pathname;
    const method = request.method;

    if (path === "/v1/chat/completions" && method === "POST")
      return handleChat(request, env);

    if (path === "/v1/search" && method === "POST")
      return handleSearch(request, env);

    if (path === "/v1/status" && method === "GET")
      return handleStatus(request, env);

    if (path === "/v1/webhook/payment" && method === "POST")
      return handlePaymentWebhook(request, env);

    return resp({ error: "Not found" }, 404);
  },
};

// ── Chat proxy ────────────────────────────────────────────────────────────────

async function handleChat(request, env) {
  const body     = await request.json();
  const model    = body.model || "";
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
    method:  "POST",
    headers: {
      "Authorization": `Bearer ${apiKey}`,
      "Content-Type":  "application/json",
    },
    body: JSON.stringify(body),
  });

  return new Response(upstream.body, {
    status:  upstream.status,
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
  const body     = await request.json();
  const endpoint = body._endpoint || "/search";
  const fast     = !!body._fast;
  delete body._endpoint;
  delete body._fast;

  const apiKey = fast ? env.SERPER_FAST_API_KEY : env.SERPER_MAIN_API_KEY;

  const upstream = await fetch(`https://google.serper.dev${endpoint}`, {
    method:  "POST",
    headers: { "X-API-KEY": apiKey, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  return new Response(upstream.body, {
    status:  upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}

// ── Status ────────────────────────────────────────────────────────────────────

async function handleStatus(request, env) {
  const { id, isPro } = await resolveIdentity(request, env);
  const usage = isPro ? 0 : await getUsage(id, env);

  return resp({
    plan:        isPro ? "pro" : "free",
    daily_usage: usage,
    daily_limit: FREE_DAILY_LIMIT,
  });
}

// ── Payment webhook ───────────────────────────────────────────────────────────
// Expected payload: { event: "payment.completed", user_id: "<supabase_user_uuid>" }

async function handlePaymentWebhook(request, env) {
  const body = await request.json();

  if (body.event === "payment.completed" && body.user_id) {
    const userId = body.user_id;

    // Mark as pro in our KV (fast lookup for rate limiting)
    await env.SUBSCRIPTIONS.put(userId, "pro");

    // Also update Supabase user_metadata so the JWT carries the plan on next sign-in
    if (env.SUPABASE_URL && env.SUPABASE_SERVICE_KEY) {
      await fetch(`${env.SUPABASE_URL}/auth/v1/admin/users/${userId}`, {
        method: "PUT",
        headers: {
          "Content-Type":  "application/json",
          "Authorization": `Bearer ${env.SUPABASE_SERVICE_KEY}`,
          "apikey":        env.SUPABASE_SERVICE_KEY,
        },
        body: JSON.stringify({ user_metadata: { plan: "pro" } }),
      });
    }
  }

  return resp({ received: true });
}

// ── Identity resolution ───────────────────────────────────────────────────────
// Returns { id, isPro } — id is either supabase user_id or device_id fallback.

async function resolveIdentity(request, env) {
  const authHeader = request.headers.get("Authorization") || "";

  if (authHeader.startsWith("Bearer ") && env.SUPABASE_URL && env.SUPABASE_ANON_KEY) {
    const token  = authHeader.slice(7);
    const userId = await validateSupabaseToken(token, env);

    if (userId) {
      const isPro = (await env.SUBSCRIPTIONS.get(userId)) === "pro";
      return { id: userId, isPro };
    }
  }

  // Fallback: anonymous device-ID
  const deviceId = request.headers.get("X-Device-ID") || "unknown";
  const isPro    = (await env.SUBSCRIPTIONS.get(deviceId)) === "pro";
  return { id: deviceId, isPro };
}

// ── Supabase token validation ─────────────────────────────────────────────────
// Calls /auth/v1/user to validate the JWT. Result cached in KV for 5 minutes
// so each unique token only triggers one Supabase round-trip per 5-min window.

async function validateSupabaseToken(token, env) {
  // Use a short prefix of the token as the cache key (tokens are long; first 40 chars are unique enough)
  const cacheKey = `jwt:${token.slice(-40)}`;
  const cached   = await env.USAGE.get(cacheKey);
  if (cached) return cached === "invalid" ? null : cached;

  try {
    const res = await fetch(`${env.SUPABASE_URL}/auth/v1/user`, {
      headers: {
        "Authorization": `Bearer ${token}`,
        "apikey":        env.SUPABASE_ANON_KEY,
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
  const key     = todayKey(id);
  const current = await getUsage(id, env);
  await env.USAGE.put(key, String(current + 1), { expirationTtl: 172800 }); // 2-day TTL
}

// ── Response helper ───────────────────────────────────────────────────────────

function resp(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
