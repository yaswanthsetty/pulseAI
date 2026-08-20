"use client";

import { useState, useCallback } from "react";
import { useMutation } from "@tanstack/react-query";
import { search, type SearchResult } from "@/lib/api";
import { Shell } from "@/components/layout/Shell";

type SearchMode = "semantic" | "keyword" | "hybrid";

/* Hoist formatter outside component (js-cache-function-results) */
const DATE_FORMATTER = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [results, setResults] = useState<SearchResult[]>([]);

  const mutation = useMutation({
    mutationFn: () => search({ query, mode }),
    onSuccess: (data) => { const r = data as { results: SearchResult[] }; setResults(r.results || []); },
  });

  /* Wrap in useCallback (rerender-functional-setstate) */
  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    mutation.mutate();
  }, [query, mutation]);

  return (
    <Shell>
      <div className="max-w-[780px] mx-auto px-4 py-8">
        <div className="mb-8">
          <h1 className="font-display font-semibold text-2xl text-foreground mb-2">Search</h1>
          <p className="text-sm text-muted">Find articles across the knowledge base using semantic, keyword, or hybrid search.</p>
        </div>

        <form onSubmit={handleSubmit} className="mb-8">
          <div className="bg-card rounded-2xl border border-border/60 p-4">
            <div className="flex gap-3 mb-3">
              <input type="text" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search articles, topics, events..." aria-label="Search query" className="flex-1 bg-input rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-muted outline-none focus:border-primary/40 border border-transparent transition-colors" />
              <button type="submit" disabled={!query.trim() || mutation.isPending} className="px-6 py-3 bg-primary text-white rounded-xl text-sm font-medium hover:bg-primary-hover disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-150">
                {mutation.isPending ? "Searching\u2026" : "Search"}
              </button>
            </div>
            <div className="flex gap-2">
              {(["semantic", "keyword", "hybrid"] as SearchMode[]).map((m) => (
                <button key={m} type="button" onClick={() => setMode(m)} aria-label={`${m} mode`} className={"px-3 py-1.5 rounded-lg text-xs font-medium transition-colors " + (mode === m ? "bg-primary/10 text-primary" : "text-muted hover:text-foreground hover:bg-secondary")}>
                  {m.charAt(0).toUpperCase() + m.slice(1)}
                </button>
              ))}
            </div>
          </div>
        </form>

        <div className="space-y-3">
          {results.map((r, i) => (
            <div key={i} className="bg-card border border-border/40 rounded-xl p-4 hover:bg-card-hover transition-colors chat-message-enter">
              <div className="flex items-start justify-between gap-4 mb-2">
                <h3 className="font-medium text-sm text-foreground leading-snug">{r.title}</h3>
                <span className="font-mono text-[11px] text-primary bg-primary/10 px-2 py-0.5 rounded-lg whitespace-nowrap">{(r.score || 0).toFixed(3)}</span>
              </div>
              <p className="text-xs text-muted leading-relaxed line-clamp-2 mb-2">{r.snippet || r.description || ""}</p>
              <div className="flex items-center gap-3 text-[11px] text-muted">
                {r.source ? <span>{r.source}</span> : null}
                {r.published_at ? <span>{DATE_FORMATTER.format(new Date(r.published_at))}</span> : null}
                {r.category ? <span className="bg-secondary px-2 py-0.5 rounded-md">{r.category}</span> : null}
              </div>
            </div>
          ))}

          {results.length === 0 && !mutation.isPending && mutation.isSuccess ? (
            <div className="text-center py-16">
              <p className="text-sm text-muted">No results found for &ldquo;{query}&rdquo;</p>
            </div>
          ) : null}

          {mutation.isError ? (
            <div className="text-center py-16">
              <p className="text-sm text-destructive">Search failed. Please try again.</p>
            </div>
          ) : null}
        </div>
      </div>
    </Shell>
  );
}
