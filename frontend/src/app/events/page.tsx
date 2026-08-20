"use client";

import { useState, useEffect } from "react";
import { fetchEvents, type Event as ApiEvent } from "@/lib/api";
import { Shell } from "@/components/layout/Shell";

/* Hoist formatter outside component (js-cache-function-results) */
const DATE_FORMATTER = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });

export default function EventsPage() {
  const [events, setEvents] = useState<ApiEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchEvents()
      .then(setEvents)
      .catch((err) => console.error("Failed to load events:", err))
      .finally(() => setLoading(false));
  }, []);

  return (
    <Shell>
      <div className="max-w-[780px] mx-auto px-4 py-8">
        <div className="mb-8">
          <h1 className="font-display font-semibold text-2xl text-foreground mb-2">Events</h1>
          <p className="text-sm text-muted">Browse clustered news events and track how stories evolve over time.</p>
        </div>

        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="bg-card rounded-xl p-4 animate-pulse">
                <div className="h-4 bg-secondary rounded w-3/4 mb-2" />
                <div className="h-3 bg-secondary rounded w-1/2" />
              </div>
            ))}
          </div>
        ) : null}

        <div className="space-y-3">
          {events.map((ev) => (
            <div key={ev.id} className="bg-card border border-border/40 rounded-xl p-5 hover:bg-card-hover transition-colors chat-message-enter">
              <div className="flex items-start justify-between gap-4 mb-3">
                <h3 className="font-medium text-sm text-foreground leading-snug flex-1">{ev.title}</h3>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <span className="text-[11px] font-mono text-muted">{ev.article_count} articles</span>
                  <div className="w-2 h-2 rounded-full" style={{ backgroundColor: ev.confidence > 0.8 ? "var(--color-success)" : ev.confidence > 0.5 ? "var(--color-signal)" : "var(--color-muted)" }} />
                </div>
              </div>
              {ev.summary ? (
                <p className="text-xs text-muted leading-relaxed line-clamp-2 mb-3">{ev.summary}</p>
              ) : null}
              <div className="flex items-center gap-3 text-[11px] text-muted">
                <span>{DATE_FORMATTER.format(new Date(ev.created_at))}</span>
                <span className="bg-secondary px-2 py-0.5 rounded-md">{ev.status}</span>
                {ev.confidence !== undefined ? (
                  <span className="font-mono">{Math.round(ev.confidence * 100)}% confidence</span>
                ) : null}
              </div>
            </div>
          ))}

          {!loading && events.length === 0 ? (
            <div className="text-center py-16">
              <p className="text-sm text-muted">No events yet. Run the clustering job to generate events.</p>
            </div>
          ) : null}
        </div>
      </div>
    </Shell>
  );
}
