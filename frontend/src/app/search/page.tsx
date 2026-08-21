"use client";

import { useState, useCallback } from "react";
import { useMutation } from "@tanstack/react-query";
import { search, type SearchResult } from "@/lib/api";
import { Shell } from "@/components/layout/Shell";

const MODES = [
  { value: "semantic", label: "Semantic" },
  { value: "keyword", label: "Keyword" },
  { value: "hybrid", label: "Hybrid" },
] as const;

const INTENTS = [
  { value: null, label: "Auto" },
  { value: "recency", label: "Recency" },
  { value: "default", label: "Relevance" },
  { value: "historical", label: "Historical" },
] as const;

const DATE_FORMATTER = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<string>("hybrid");
  const [intent, setIntent] = useState<string | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [hasSearched, setHasSearched] = useState(false);

  const mutation = useMutation({
    mutationFn: () => search({ query, mode, intent: intent || undefined, limit: 10 }),
    onSuccess: (data) => { setResults(Array.isArray(data) ? data : []); setHasSearched(true); },
  });

  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) mutation.mutate();
  }, [query, mutation]);

  return (
    <Shell>
      <div className="max-w-3xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-2xl font-display font-semibold text-foreground mb-2">Search</h1>
          <p className="text-sm text-muted">Semantic, keyword, or hybrid search across all ingested articles.</p>
        </div>

        <form onSubmit={handleSubmit} className="mb-6">
          <div className="flex gap-2">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search articles…"
              aria-label="Search query"
              className="flex-1 bg-card border border-border/40 rounded-xl px-4 py-3 text-foreground placeholder:text-muted outline-none focus:border-primary/40 focus:ring-1 focus:ring-primary/20 font-body text-sm transition-colors"
            />
            <button
              type="submit"
              disabled={!query.trim() || mutation.isPending}
              className="px-6 py-3 bg-primary text-white font-display font-medium text-sm rounded-xl hover:bg-primary-hover disabled:opacity-30 disabled:cursor-not-allowed transition-all"
            >
              {mutation.isPending ? "Searching…" : "Search"}
            </button>
          </div>
        </form>

        <div className="flex flex-wrap items-center gap-6 mb-6">
          <div className="flex items-center gap-1">
            <span className="text-[11px] font-mono text-muted uppercase tracking-wider mr-2">Mode</span>
            {MODES.map((m) => (
              <button
                key={m.value}
                onClick={() => setMode(m.value)}
                aria-label={`${m.label} mode`}
                className={`px-3 py-1 text-xs font-mono rounded-lg transition-colors ${mode === m.value ? "bg-primary/10 text-primary border border-primary/20" : "text-muted hover:text-foreground border border-transparent"}`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1">
            <span className="text-[11px] font-mono text-muted uppercase tracking-wider mr-2">Intent</span>
            {INTENTS.map((i) => (
              <button
                key={i.value ?? "auto"}
                onClick={() => setIntent(i.value)}
                className={`px-3 py-1 text-xs font-mono rounded-lg transition-colors ${intent === i.value ? "bg-primary/10 text-primary border border-primary/20" : "text-muted hover:text-foreground border border-transparent"}`}
              >
                {i.label}
              </button>
            ))}
          </div>
        </div>

        {mutation.isError && (
          <div className="mb-6 p-4 bg-destructive/10 border border-destructive/30 rounded-xl text-sm text-destructive">
            {mutation.error.message}
          </div>
        )}

        {hasSearched && !mutation.isPending && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <span className="text-xs font-mono text-muted">{results.length} result{results.length !== 1 ? "s" : ""}</span>
            </div>
            {results.length === 0 ? (
              <div className="text-center py-12">
                <p className="text-muted text-sm">No results found</p>
                <p className="text-muted text-xs mt-1 font-mono">Try a different query or mode</p>
              </div>
            ) : (
              <div className="space-y-2">
                {results.map((result, idx) => (
                  <div key={result.article_id} className="group p-4 bg-card border border-border/40 rounded-xl hover:border-primary/20 transition-colors">
                    <div className="flex items-start gap-3">
                      <span className="flex-shrink-0 w-6 h-6 flex items-center justify-center rounded-lg bg-secondary text-[11px] font-mono text-muted group-hover:bg-primary/10 group-hover:text-primary transition-colors">
                        {idx + 1}
                      </span>
                      <div className="flex-1 min-w-0">
                        <h3 className="text-sm font-display font-medium text-foreground group-hover:text-primary transition-colors leading-snug">{result.title}</h3>
                        {result.snippet && <p className="mt-1 text-xs text-muted leading-relaxed line-clamp-2">{result.snippet}</p>}
                        <div className="mt-1.5 flex items-center gap-3 text-[11px] font-mono text-muted">
                          {result.published_at && <span>{DATE_FORMATTER.format(new Date(result.published_at))}</span>}
                          <span className="text-primary">{result.similarity_score.toFixed(3)}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {!hasSearched && !mutation.isPending && (
          <div className="text-center py-16">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-card border border-border/40 mb-4">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-muted">
                <circle cx="11" cy="11" r="8" />
                <path d="m21 21-4.35-4.35" />
              </svg>
            </div>
            <p className="text-sm text-muted">Type a query to search articles</p>
            <p className="text-xs text-muted/60 mt-1 font-mono">Try: "AI funding trends" or "climate change policy"</p>
          </div>
        )}
      </div>
    </Shell>
  );
}
