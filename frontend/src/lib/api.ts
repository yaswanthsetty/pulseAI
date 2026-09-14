const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8090";

/* ── Types ────────────────────────────────────────────── */

export interface SearchResult {
  article_id: string;
  title: string;
  snippet?: string;
  description?: string;
  source?: string;
  source_id: string;
  category?: string;
  score?: number;
  similarity_score: number;
  published_at: string;
}

export interface Event {
  id: string;
  title: string;
  summary: string | null;
  confidence: number;
  status: string;
  article_count: number;
  created_at: string;
  last_updated: string;
}

export interface EventDetail extends Event {
  timeline: {
    article_id: string;
    title: string;
    source_id: string;
    published_at: string | null;
    similarity_at_match: number | null;
    added_at: string;
  }[];
}

export interface EventTimeline {
  id: string;
  title: string;
  status: string;
  total_articles: number;
  first_day: string | null;
  last_day: string | null;
  days: {
    date: string;
    article_count: number;
    headline: string | null;
    keywords: string[];
    titles: string[];
  }[];
}

export interface ChatEvent {
  type: "token" | "thinking" | "evidence" | "done" | "error" | "agreement";
  delta?: string;
  token?: string;
  stage?: string;
  message?: string;
  score?: number;
  sub_questions?: string[];
  agreement?: number;
  evidence?: EvidenceItem[];
  conversation_id?: string;
  error?: string;
}

export interface EvidenceItem {
  citation_id: number;
  article_id: string;
  title: string;
  snippet?: string;
  source?: string;
  source_id: string;
  published_at: string | null;
  score: number;
}

export interface Report {
  id: string;
  topic: string;
  timeframe: string | null;
  status: string;
  content: Record<string, unknown> | string | null;
  evidence_agreement: number | Record<string, unknown> | null;
  created_at: string;
}

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface ApiKeyInfo {
  id: string;
  label: string | null;
  scopes: string[];
  last_used_at: string | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

/* ── Auth helpers ─────────────────────────────────────── */

const TOKEN_KEY = "pulseai_token";

export function setAccessToken(t: string | null) {
  if (typeof window === "undefined") return;
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function logout() {
  setAccessToken(null);
  if (typeof window !== "undefined") window.location.href = "/login";
}

/* ── HTTP helper ──────────────────────────────────────── */

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const h: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as Record<string, string>),
  };
  const token = getAccessToken();
  if (token) h["Authorization"] = "Bearer " + token;
  const res = await fetch(API_URL + path, { ...opts, headers: h });
  if (res.status === 401) {
    logout();
    throw new Error("Session expired. Please log in again.");
  }
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error(b?.detail || b?.error?.message || "Request failed: " + res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

/* ── Auth ─────────────────────────────────────────────── */

export async function login(email: string, password: string) {
  const t = await request<{ access_token: string; user: User }>(
    "/api/v1/auth/login",
    { method: "POST", body: JSON.stringify({ email, password }) }
  );
  setAccessToken(t.access_token);
  return t;
}

export async function register(email: string, password: string, name?: string) {
  return request<User>(
    "/api/v1/auth/register",
    { method: "POST", body: JSON.stringify({ email, password, display_name: name }) }
  );
}

export async function getMe() {
  return request<User>("/api/v1/users/me");
}

/* ── Search ───────────────────────────────────────────── */

export async function search(params: {
  query: string; limit?: number; mode?: string; intent?: string;
}) {
  const raw = await request<{ results: SearchResult[] } | SearchResult[]>(
    "/api/v1/search",
    { method: "POST", body: JSON.stringify(params) }
  );
  const results = Array.isArray(raw) ? raw : (raw as { results: SearchResult[] }).results || [];
  return results;
}

/* ── Events ───────────────────────────────────────────── */

export async function fetchEvents(params?: { limit?: number; q?: string }) {
  const p = new URLSearchParams();
  if (params?.limit) p.set("limit", String(params.limit));
  if (params?.q) p.set("q", params.q);
  const qs = p.toString();
  const data = await request<{ items: Event[]; total: number }>(
    "/api/v1/events" + (qs ? "?" + qs : "")
  );
  return data.items || [];
}

export async function getEventDetail(id: string) {
  return request<EventDetail>("/api/v1/events/" + id);
}

export async function getEventTimeline(id: string) {
  return request<EventTimeline>("/api/v1/events/" + id + "/timeline");
}

/* ── Chat (SSE streaming) ─────────────────────────────── */

export async function chatStream(message: string): Promise<AsyncGenerator<ChatEvent>> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getAccessToken();
  if (token) headers["Authorization"] = "Bearer " + token;

  const res = await fetch(API_URL + "/api/v1/chat", {
    method: "POST",
    headers,
    body: JSON.stringify({ message }),
  });

  if (!res.ok) throw new Error("Chat failed: " + res.status);
  if (!res.body) throw new Error("No response body");

  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";

  return (async function* () {
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n");
        buf = parts.pop() || "";
        for (const part of parts) {
          if (part.startsWith("data: ")) {
            const payload = part.slice(6).trim();
            if (!payload) continue;
            try {
              const parsed = JSON.parse(payload) as ChatEvent;
              yield parsed;
              if (parsed.type === "done") return;
            } catch { /* skip malformed */ }
          }
        }
      }
      if (buf.startsWith("data: ")) {
        const payload = buf.slice(6).trim();
        if (payload) {
          try {
            const parsed = JSON.parse(payload) as ChatEvent;
            yield parsed;
          } catch { /* skip */ }
        }
      }
    } catch (err) {
      yield { type: "error", error: err instanceof Error ? err.message : "Stream failed" };
    }
  })();
}

/* ── Conversations ────────────────────────────────────── */

export async function fetchConversations() {
  const data = await request<{ items: Conversation[] }>("/api/v1/conversations");
  return data.items || [];
}

/* ── Reports ──────────────────────────────────────────── */

export async function generateReport(topic: string, timeframe?: string) {
  return request<Report>("/api/v1/reports/generate", {
    method: "POST",
    body: JSON.stringify({ topic, timeframe }),
  });
}

export async function fetchReports() {
  const data = await request<{ items: Report[] }>("/api/v1/reports");
  return data.items || [];
}

export async function getReport(id: string) {
  return request<Report>("/api/v1/reports/" + id);
}

export async function exportReportCsv(id: string, topic: string) {
  const token = getAccessToken();
  const res = await fetch(`${API_URL}/api/v1/reports/${id}/export`, {
    headers: token ? { Authorization: "Bearer " + token } : undefined,
  });
  if (!res.ok) throw new Error("Export failed: " + res.status);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `pulseai-report-${topic.slice(0, 40).replace(/[^a-z0-9]+/gi, "-").toLowerCase() || id}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ── Admin ────────────────────────────────────────────── */

export async function fetchUsers(params?: { page?: number; page_size?: number }) {
  const p = new URLSearchParams();
  if (params?.page) p.set("page", String(params.page));
  if (params?.page_size) p.set("page_size", String(params.page_size));
  const qs = p.toString();
  return request<{ items: User[]; total: number }>(
    "/api/v1/users" + (qs ? "?" + qs : "")
  );
}

export async function updateUserRole(userId: string, role: string) {
  return request<User>("/api/v1/users/" + userId + "/role", {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}

/* ── API Keys ─────────────────────────────────────────── */

export async function fetchApiKeys() {
  return request<ApiKeyInfo[]>("/api/v1/api-keys");
}

export async function createApiKey(label: string, scopes: string[]) {
  return request<ApiKeyInfo & { key: string }>("/api/v1/api-keys", {
    method: "POST",
    body: JSON.stringify({ label, scopes }),
  });
}

export async function revokeApiKey(keyId: string) {
  return request<void>("/api/v1/api-keys/" + keyId, { method: "DELETE" });
}

/* ── Usage ────────────────────────────────────────────── */

export async function fetchUsage() {
  return request<{ breakdown: Record<string, unknown>[]; total_tokens: number }>(
    "/api/v1/usage"
  );
}

/* ── Insights (dashboard / trends / comparison) ────────── */

export interface Stats {
  articles_total: number;
  articles_last_24h: number;
  events_total: number;
  events_open: number;
  sources_total: number;
  users_total: number;
  articles_per_day: { date: string; count: number }[];
  top_categories: { category: string; count: number }[];
  top_sources: { source: string; count: number }[];
}

export interface TrendingEvent {
  event_id: string;
  title: string;
  summary: string | null;
  direction: "rising" | "steady" | "cooling" | "quiet";
  momentum: number;
  recent_24h: number;
  previous_24h: number;
  total_articles: number;
  article_count: number;
  confidence: number;
}

export interface SourceCoverage {
  source: string;
  credibility: number | null;
  article_count: number;
  latest: { article_id: string; title: string; published_at: string | null } | null;
  top_keywords: string[];
}

export async function fetchStats() {
  return request<Stats>("/api/v1/insights/stats");
}

export async function fetchTrending(limit = 10) {
  const data = await request<{ items: TrendingEvent[] }>(
    "/api/v1/insights/trending?limit=" + limit
  );
  return data.items || [];
}

export async function compareSources(query: string, a: string, b: string) {
  const p = new URLSearchParams({ q: query, a, b });
  return request<{ query: string; source_a: SourceCoverage; source_b: SourceCoverage }>(
    "/api/v1/insights/compare?" + p.toString()
  );
}

export interface ArticleDetail {
  id: string;
  title: string;
  author: string | null;
  description: string | null;
  content_preview: string | null;
  content: string | null;
  url: string;
  image_url: string | null;
  language_code: string | null;
  category_code: string | null;
  published_at: string;
  source: string | null;
  credibility: number | null;
  event: { id: string; title: string; status: string } | null;
  bookmarked: boolean;
}

export async function getArticle(id: string) {
  return request<ArticleDetail>("/api/v1/insights/articles/" + id);
}

/* ── Library (saved searches / bookmarks / notifications) ─ */

export interface SavedSearch {
  id: string;
  query: string;
  filters: Record<string, unknown> | null;
  created_at: string;
}

export interface BookmarkItem {
  article_id: string;
  title: string;
  url: string;
  description: string | null;
  author: string | null;
  published_at: string | null;
  category: string | null;
  source: string | null;
  created_at: string;
}

export interface NotificationRule {
  id: string;
  keyword_or_topic: string | null;
  category_code: string | null;
  channel: "email" | "in_app" | "webhook";
  is_active: boolean;
  created_at: string;
}

export interface NotificationDelivery {
  id: string;
  event_id: string | null;
  channel: string;
  status: string;
  detail: string | null;
  created_at: string;
}

export async function fetchSavedSearches() {
  const data = await request<{ items: SavedSearch[] }>("/api/v1/library/searches");
  return data.items || [];
}

export async function createSavedSearch(query: string, filters?: Record<string, unknown>) {
  return request<SavedSearch>("/api/v1/library/searches", {
    method: "POST",
    body: JSON.stringify({ query, filters }),
  });
}

export async function deleteSavedSearch(id: string) {
  return request<void>("/api/v1/library/searches/" + id, { method: "DELETE" });
}

export async function fetchBookmarks() {
  const data = await request<{ items: BookmarkItem[] }>("/api/v1/library/bookmarks");
  return data.items || [];
}

export async function addBookmark(articleId: string) {
  return request<void>("/api/v1/library/bookmarks/" + articleId, { method: "PUT" });
}

export async function removeBookmark(articleId: string) {
  return request<void>("/api/v1/library/bookmarks/" + articleId, { method: "DELETE" });
}

export async function fetchNotificationRules() {
  const data = await request<{ items: NotificationRule[] }>(
    "/api/v1/library/notification-rules"
  );
  return data.items || [];
}

export async function createNotificationRule(
  keyword: string | null,
  category: string | null,
  channel: string
) {
  return request<NotificationRule>("/api/v1/library/notification-rules", {
    method: "POST",
    body: JSON.stringify({
      keyword_or_topic: keyword,
      category_code: category,
      channel,
    }),
  });
}

export async function deleteNotificationRule(id: string) {
  return request<void>("/api/v1/library/notification-rules/" + id, { method: "DELETE" });
}

export async function fetchNotifications() {
  return request<{ items: NotificationDelivery[]; unread: number }>(
    "/api/v1/library/notifications"
  );
}

export async function markNotificationsRead() {
  return request<{ updated: number }>("/api/v1/library/notifications/read-all", {
    method: "POST",
  });
}
