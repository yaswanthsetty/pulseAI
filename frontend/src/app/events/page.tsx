"use client";

import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchEvents, getEventDetail, getEventTimeline, type Event, type EventDetail, type EventTimeline } from "@/lib/api";
import { Shell } from "@/components/layout/Shell";

const TIME_FORMATTER = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const FULL_FORMATTER = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });

export default function EventsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  const { data: events, isLoading } = useQuery({
    queryKey: ["events", searchQuery],
    queryFn: () => fetchEvents({ limit: 50, q: searchQuery || undefined }),
  });

  return (
    <Shell>
      <div className="flex h-full">
        {/* Event list */}
        <div className={`flex-shrink-0 border-r border-border/40 overflow-y-auto ${selectedId ? "hidden lg:block lg:w-96" : "w-full lg:w-96"}`}>
          <div className="px-4 py-4 border-b border-border/40">
            <h1 className="text-lg font-display font-semibold text-foreground mb-3">Events</h1>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search events…"
              aria-label="Search events"
              className="w-full bg-secondary/50 rounded-xl px-3 py-2 text-sm text-foreground placeholder:text-muted outline-none focus:ring-1 focus:ring-primary/40 border border-transparent focus:border-primary/30 transition-colors"
            />
          </div>
          <div className="p-2">
            {isLoading ? (
              <div className="space-y-2">
                {[1, 2, 3, 4].map((i) => (
                  <div key={i} className="h-20 bg-card rounded-xl animate-pulse" />
                ))}
              </div>
            ) : events && events.length > 0 ? (
              events.map((ev) => (
                <button
                  key={ev.id}
                  onClick={() => setSelectedId(ev.id)}
                  className={`w-full text-left p-4 rounded-xl mb-1 transition-colors ${
                    selectedId === ev.id ? "bg-primary/10 border border-primary/20" : "hover:bg-card-hover border border-transparent"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2 mb-1">
                    <h3 className="text-sm font-medium text-foreground leading-snug line-clamp-2 flex-1">{ev.title}</h3>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      <span className="text-[10px] font-mono text-muted">{ev.article_count}</span>
                      <div className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: ev.confidence > 0.8 ? "var(--color-success)" : ev.confidence > 0.5 ? "var(--color-signal)" : "var(--color-muted)" }} />
                    </div>
                  </div>
                  <div className="text-[11px] font-mono text-muted">{TIME_FORMATTER.format(new Date(ev.created_at))}</div>
                </button>
              ))
            ) : (
              <p className="text-sm text-muted text-center py-8">No events found</p>
            )}
          </div>
        </div>

        {/* Event detail */}
        <div className="flex-1 min-w-0 overflow-y-auto">
          {selectedId ? <EventDetailPanel eventId={selectedId} onBack={() => setSelectedId(null)} /> : (
            <div className="flex items-center justify-center h-full">
              <p className="text-sm text-muted">Select an event to view details</p>
            </div>
          )}
        </div>
      </div>
    </Shell>
  );
}

function EventDetailPanel({ eventId, onBack }: { eventId: string; onBack: () => void }) {
  const { data: detail, isLoading: loadingDetail } = useQuery({
    queryKey: ["event-detail", eventId],
    queryFn: () => getEventDetail(eventId),
  });

  const { data: timeline, isLoading: loadingTimeline } = useQuery({
    queryKey: ["event-timeline", eventId],
    queryFn: () => getEventTimeline(eventId),
  });

  if (loadingDetail || loadingTimeline) {
    return (
      <div className="p-6 space-y-4">
        <div className="h-8 bg-card rounded-lg w-3/4 animate-pulse" />
        <div className="h-4 bg-card rounded w-1/2 animate-pulse" />
        <div className="space-y-3 mt-6">
          {[1, 2, 3].map((i) => <div key={i} className="h-16 bg-card rounded-xl animate-pulse" />)}
        </div>
      </div>
    );
  }

  if (!detail) return <p className="p-6 text-sm text-muted">Event not found</p>;

  return (
    <div className="p-6">
      {/* Mobile back button */}
      <button onClick={onBack} className="lg:hidden mb-4 text-sm text-primary hover:text-primary-hover flex items-center gap-1">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
        </svg>
        Back to events
      </button>

      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-2">
          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: detail.confidence > 0.8 ? "var(--color-success)" : detail.confidence > 0.5 ? "var(--color-signal)" : "var(--color-muted)" }} />
          <span className="text-[11px] font-mono text-muted uppercase">{detail.status}</span>
          <span className="text-[11px] font-mono text-muted">{Math.round(detail.confidence * 100)}% confidence</span>
        </div>
        <h1 className="text-xl font-display font-semibold text-foreground mb-2">{detail.title}</h1>
        {detail.summary && <p className="text-sm text-muted leading-relaxed">{detail.summary}</p>}
        <div className="flex items-center gap-4 mt-3 text-[11px] font-mono text-muted">
          <span>{detail.article_count} articles</span>
          <span>Created {FULL_FORMATTER.format(new Date(detail.created_at))}</span>
        </div>
      </div>

      {/* Timeline */}
      {timeline && timeline.days.length > 0 && (
        <div>
          <h2 className="text-sm font-display font-semibold text-foreground mb-4">Timeline</h2>
          <div className="space-y-4">
            {timeline.days.map((day) => (
              <div key={day.date} className="bg-card border border-border/40 rounded-xl p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-mono text-muted">{FULL_FORMATTER.format(new Date(day.date))}</span>
                  <span className="text-[10px] font-mono text-primary">{day.article_count} articles</span>
                </div>
                {day.headline && (
                  <p className="text-sm font-medium text-foreground mb-1.5">{day.headline}</p>
                )}
                <div className="flex flex-wrap gap-1 mb-2">
                  {day.keywords.map((kw) => (
                    <span key={kw} className="px-1.5 py-0.5 text-[10px] font-mono bg-primary/10 text-primary rounded">{kw}</span>
                  ))}
                </div>
                <div className="space-y-1">
                  {day.titles.map((title, i) => (
                    <p key={i} className="text-xs text-muted leading-snug">{title}</p>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Article list */}
      {detail.timeline && detail.timeline.length > 0 && (
        <div className="mt-8">
          <h2 className="text-sm font-display font-semibold text-foreground mb-4">All Articles ({detail.timeline.length})</h2>
          <div className="space-y-2">
            {detail.timeline.map((entry) => (
              <div key={entry.article_id} className="bg-card border border-border/40 rounded-xl p-4">
                <h3 className="text-sm font-medium text-foreground leading-snug">{entry.title}</h3>
                <div className="flex items-center gap-3 mt-1.5 text-[11px] font-mono text-muted">
                  {entry.published_at && <span>{FULL_FORMATTER.format(new Date(entry.published_at))}</span>}
                  {entry.similarity_at_match !== null && (
                    <>
                      <span className="text-border">|</span>
                      <span className="text-primary">{entry.similarity_at_match.toFixed(3)}</span>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
