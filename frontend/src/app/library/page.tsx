"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addBookmark,
  createNotificationRule,
  createSavedSearch,
  deleteNotificationRule,
  deleteSavedSearch,
  fetchBookmarks,
  fetchNotificationRules,
  fetchNotifications,
  fetchSavedSearches,
  markNotificationsRead,
  removeBookmark,
  type BookmarkItem,
} from "@/lib/api";
import { Shell } from "@/components/layout/Shell";
import { useToast } from "@/components/ui/Toast";

const DATE_FORMATTER = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
});

type Tab = "searches" | "bookmarks" | "rules" | "inbox";

const TABS: { id: Tab; label: string }[] = [
  { id: "searches", label: "Saved searches" },
  { id: "bookmarks", label: "Bookmarks" },
  { id: "rules", label: "Alert rules" },
  { id: "inbox", label: "Notifications" },
];

export default function LibraryPage() {
  const [tab, setTab] = useState<Tab>("searches");

  return (
    <Shell>
      <div className="max-w-4xl mx-auto px-6 py-8">
        <h1 className="text-xl font-display font-semibold text-foreground mb-2">Library</h1>
        <p className="text-sm text-muted mb-6">
          Saved searches, bookmarked articles, and event alerts.
        </p>

        <div className="flex gap-1 mb-6 bg-card border border-border/40 rounded-xl p-1 w-fit" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={`px-4 py-2 rounded-lg text-sm transition-colors ${
                tab === t.id
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted hover:text-foreground"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === "searches" && <SavedSearchesTab />}
        {tab === "bookmarks" && <BookmarksTab />}
        {tab === "rules" && <RulesTab />}
        {tab === "inbox" && <InboxTab />}
      </div>
    </Shell>
  );
}

/* ── Saved searches ──────────────────────────────────────────────────── */

function SavedSearchesTab() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");

  const { data: items, isLoading } = useQuery({
    queryKey: ["saved-searches"],
    queryFn: fetchSavedSearches,
  });

  const create = useMutation({
    mutationFn: () => createSavedSearch(query.trim()),
    onSuccess: () => {
      toast("Search saved", "success");
      setQuery("");
      queryClient.invalidateQueries({ queryKey: ["saved-searches"] });
    },
    onError: (err: Error) => toast(err.message, "error"),
  });

  const remove = useMutation({
    mutationFn: deleteSavedSearch,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["saved-searches"] }),
    onError: (err: Error) => toast(err.message, "error"),
  });

  return (
    <div className="space-y-4">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) create.mutate();
        }}
        className="flex gap-2"
      >
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Save a search query…"
          aria-label="Search query to save"
          className="flex-1 bg-secondary/50 rounded-xl px-4 py-2.5 text-sm text-foreground placeholder:text-muted outline-none focus:ring-1 focus:ring-primary/40 border border-transparent focus:border-primary/30"
        />
        <button
          type="submit"
          disabled={!query.trim() || create.isPending}
          className="px-5 py-2.5 bg-primary text-white rounded-xl text-sm font-medium hover:bg-primary-hover disabled:opacity-30 disabled:cursor-not-allowed"
        >
          Save
        </button>
      </form>

      {isLoading ? (
        <div className="text-sm text-muted animate-pulse">Loading…</div>
      ) : items && items.length > 0 ? (
        <ul className="space-y-2">
          {items.map((s) => (
            <li
              key={s.id}
              className="flex items-center gap-3 bg-card border border-border/40 rounded-xl px-4 py-3"
            >
              <a
                href={`/search?q=${encodeURIComponent(s.query)}`}
                className="flex-1 text-sm text-foreground hover:text-primary truncate"
              >
                {s.query}
              </a>
              <span className="text-[11px] font-mono text-muted shrink-0">
                {DATE_FORMATTER.format(new Date(s.created_at))}
              </span>
              <button
                onClick={() => remove.mutate(s.id)}
                aria-label={`Delete saved search "${s.query}"`}
                className="text-muted hover:text-destructive shrink-0"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted py-6 text-center">
          No saved searches yet. Save a query to re-run it with one click.
        </p>
      )}
    </div>
  );
}

/* ── Bookmarks ───────────────────────────────────────────────────────── */

function BookmarksTab() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data: items, isLoading } = useQuery({
    queryKey: ["bookmarks"],
    queryFn: fetchBookmarks,
  });

  const remove = useMutation({
    mutationFn: removeBookmark,
    onSuccess: () => {
      toast("Bookmark removed", "success");
      queryClient.invalidateQueries({ queryKey: ["bookmarks"] });
    },
    onError: (err: Error) => toast(err.message, "error"),
  });

  if (isLoading) return <div className="text-sm text-muted animate-pulse">Loading…</div>;
  if (!items || items.length === 0)
    return (
      <p className="text-sm text-muted py-6 text-center">
        No bookmarks yet. Bookmark articles from search results or the article view.
      </p>
    );

  return (
    <ul className="space-y-2">
      {items.map((b: BookmarkItem) => (
        <li key={b.article_id} className="bg-card border border-border/40 rounded-xl p-4">
          <div className="flex items-start justify-between gap-3">
            <a
              href={b.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium text-foreground hover:text-primary"
            >
              {b.title}
            </a>
            <button
              onClick={() => remove.mutate(b.article_id)}
              aria-label={`Remove bookmark "${b.title}"`}
              className="text-muted hover:text-destructive shrink-0"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          {b.description && (
            <p className="text-xs text-muted mt-1 line-clamp-2">{b.description}</p>
          )}
          <div className="text-[11px] font-mono text-muted mt-2">
            {b.source}
            {b.published_at && ` · ${DATE_FORMATTER.format(new Date(b.published_at))}`}
          </div>
        </li>
      ))}
    </ul>
  );
}

/* ── Alert rules ─────────────────────────────────────────────────────── */

function RulesTab() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [keyword, setKeyword] = useState("");
  const [channel, setChannel] = useState("in_app");

  const { data: items, isLoading } = useQuery({
    queryKey: ["notification-rules"],
    queryFn: fetchNotificationRules,
  });

  const create = useMutation({
    mutationFn: () =>
      createNotificationRule(keyword.trim() || null, null, channel),
    onSuccess: () => {
      toast("Alert rule created", "success");
      setKeyword("");
      queryClient.invalidateQueries({ queryKey: ["notification-rules"] });
    },
    onError: (err: Error) => toast(err.message, "error"),
  });

  const remove = useMutation({
    mutationFn: deleteNotificationRule,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notification-rules"] }),
    onError: (err: Error) => toast(err.message, "error"),
  });

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">
        Get notified when a new event matches your keyword. In-app alerts appear in the
        Notifications tab; email/webhook need SMTP or a webhook URL configured on the server.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (keyword.trim()) create.mutate();
        }}
        className="flex gap-2"
      >
        <input
          type="text"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="Keyword or topic (e.g. quantum computing)"
          aria-label="Alert keyword"
          className="flex-1 bg-secondary/50 rounded-xl px-4 py-2.5 text-sm text-foreground placeholder:text-muted outline-none focus:ring-1 focus:ring-primary/40 border border-transparent focus:border-primary/30"
        />
        <select
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
          aria-label="Notification channel"
          className="bg-secondary/50 rounded-xl px-3 py-2.5 text-sm text-foreground outline-none focus:border-primary/30 border border-transparent"
        >
          <option value="in_app">In-app</option>
          <option value="email">Email</option>
          <option value="webhook">Webhook</option>
        </select>
        <button
          type="submit"
          disabled={!keyword.trim() || create.isPending}
          className="px-5 py-2.5 bg-primary text-white rounded-xl text-sm font-medium hover:bg-primary-hover disabled:opacity-30 disabled:cursor-not-allowed"
        >
          Add
        </button>
      </form>

      {isLoading ? (
        <div className="text-sm text-muted animate-pulse">Loading…</div>
      ) : items && items.length > 0 ? (
        <ul className="space-y-2">
          {items.map((r) => (
            <li
              key={r.id}
              className="flex items-center gap-3 bg-card border border-border/40 rounded-xl px-4 py-3"
            >
              <span className="flex-1 text-sm text-foreground truncate">
                {r.keyword_or_topic || r.category_code || "(any)"}
              </span>
              <span className="text-[11px] font-mono px-2 py-0.5 rounded-lg bg-secondary text-muted shrink-0">
                {r.channel}
              </span>
              <button
                onClick={() => remove.mutate(r.id)}
                aria-label={`Delete alert rule "${r.keyword_or_topic ?? r.channel}"`}
                className="text-muted hover:text-destructive shrink-0"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted py-6 text-center">
          No alert rules yet. Create one to get notified about matching events.
        </p>
      )}
    </div>
  );
}

/* ── Notifications inbox ─────────────────────────────────────────────── */

function InboxTab() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["notifications"],
    queryFn: fetchNotifications,
    refetchInterval: 30_000,
  });

  const markRead = useMutation({
    mutationFn: markNotificationsRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
    onError: (err: Error) => toast(err.message, "error"),
  });

  if (isLoading) return <div className="text-sm text-muted animate-pulse">Loading…</div>;
  if (!data || data.items.length === 0)
    return (
      <p className="text-sm text-muted py-6 text-center">
        No notifications yet. Matching events will appear here.
      </p>
    );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">
          {data.unread > 0 ? `${data.unread} unread` : "All read"}
        </span>
        {data.unread > 0 && (
          <button
            onClick={() => markRead.mutate()}
            disabled={markRead.isPending}
            className="text-xs text-primary hover:text-primary-hover"
          >
            Mark all read
          </button>
        )}
      </div>
      <ul className="space-y-2">
        {data.items.map((n) => (
          <li
            key={n.id}
            className={`bg-card border rounded-xl px-4 py-3 ${
              n.status === "failed" ? "border-destructive/30" : "border-border/40"
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-mono px-2 py-0.5 rounded-lg bg-secondary text-muted">
                {n.channel}
              </span>
              <span
                className={`text-[11px] font-mono px-2 py-0.5 rounded-lg ${
                  n.status === "sent"
                    ? "bg-success/10 text-success"
                    : "bg-destructive/10 text-destructive"
                }`}
              >
                {n.status}
              </span>
              <span className="text-[11px] font-mono text-muted ml-auto shrink-0">
                {DATE_FORMATTER.format(new Date(n.created_at))}
              </span>
            </div>
            {n.detail && <p className="text-sm text-foreground mt-2">{n.detail}</p>}
            {n.event_id && (
              <a
                href={`/events?focus=${n.event_id}`}
                className="text-xs text-primary hover:text-primary-hover mt-1 inline-block"
              >
                View event →
              </a>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
