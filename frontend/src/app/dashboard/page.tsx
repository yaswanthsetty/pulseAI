"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { fetchStats, fetchTrending, type TrendingEvent } from "@/lib/api";
import { AuthGuard } from "@/components/AuthGuard";

const DIRECTION_STYLES: Record<string, { label: string; className: string }> = {
  rising: { label: "▲ Rising", className: "text-success" },
  steady: { label: "◆ Steady", className: "text-signal" },
  cooling: { label: "▼ Cooling", className: "text-muted" },
  quiet: { label: "· Quiet", className: "text-muted" },
};

function StatCard({
  label,
  value,
  sub,
  href,
}: {
  label: string;
  value: number | string;
  sub?: string;
  href?: string;
}) {
  const inner = (
    <div className="bg-card border border-border/50 rounded-xl p-5 hover:border-primary/40 transition-colors h-full">
      <div className="text-xs uppercase tracking-wider text-muted">{label}</div>
      <div className="text-3xl font-display font-bold mt-2 text-foreground tabular-nums">
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </div>
  );
  return href ? (
    <Link href={href} className="block focus-visible:outline-ring rounded-xl">
      {inner}
    </Link>
  ) : (
    inner
  );
}

/** Simple CSS bar chart of the 14-day ingestion volume (no chart lib needed). */
function VolumeChart({ data }: { data: { date: string; count: number }[] }) {
  const max = Math.max(...data.map((d) => d.count), 1);
  return (
    <div className="flex items-end gap-1 h-28" aria-hidden="true">
      {data.map((d) => (
        <div key={d.date} className="flex-1 flex flex-col items-center gap-1 group">
          <div
            className="w-full rounded-t bg-primary/70 group-hover:bg-primary transition-colors min-h-[2px]"
            style={{ height: `${Math.max((d.count / max) * 100, 2)}%` }}
            title={`${d.date}: ${d.count}`}
          />
          <div className="text-[9px] text-muted rotate-0">{d.date.slice(8)}</div>
        </div>
      ))}
    </div>
  );
}

function TrendingRow({ item, rank }: { item: TrendingEvent; rank: number }) {
  const dir = DIRECTION_STYLES[item.direction] ?? DIRECTION_STYLES.steady;
  return (
    <Link
      href={`/events?focus=${item.event_id}`}
      className="flex items-center gap-4 py-3 border-b border-border/40 last:border-0 hover:bg-card-hover/50 px-2 -mx-2 rounded transition-colors"
    >
      <span className="font-mono text-xs text-muted w-6 text-right">{rank}</span>
      <div className="flex-1 min-w-0">
        <div className="truncate text-sm font-medium text-foreground">{item.title}</div>
        <div className="text-xs text-muted mt-0.5">
          {item.article_count} articles · {item.recent_24h} in last 24h
        </div>
      </div>
      <span className={`text-xs font-mono shrink-0 ${dir.className}`}>{dir.label}</span>
    </Link>
  );
}

export default function DashboardPage() {
  const stats = useQuery({ queryKey: ["stats"], queryFn: fetchStats, refetchInterval: 60_000 });
  const trending = useQuery({
    queryKey: ["trending"],
    queryFn: () => fetchTrending(8),
    refetchInterval: 60_000,
  });

  return (
    <AuthGuard>
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto p-6 space-y-6">
          <header>
            <h1 className="font-display text-2xl font-bold text-foreground">Dashboard</h1>
            <p className="text-sm text-muted mt-1">
              Live pipeline health and what the news stream is doing right now.
            </p>
          </header>

          {stats.isLoading ? (
            <div className="text-sm text-muted animate-pulse">Loading stats…</div>
          ) : stats.isError ? (
            <div className="text-sm text-destructive">
              Couldn&apos;t load stats. Is the backend running?
            </div>
          ) : stats.data ? (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <StatCard
                  label="Articles (24h)"
                  value={stats.data.articles_last_24h}
                  sub={`${stats.data.articles_total.toLocaleString()} total`}
                />
                <StatCard
                  label="Open events"
                  value={stats.data.events_open}
                  sub={`${stats.data.events_total} all-time`}
                  href="/events"
                />
                <StatCard label="Sources" value={stats.data.sources_total} />
                <StatCard label="Users" value={stats.data.users_total} />
              </div>

              <section className="bg-card border border-border/50 rounded-xl p-5">
                <h2 className="text-sm font-semibold text-foreground mb-4">
                  Ingestion volume — last 14 days
                </h2>
                <VolumeChart data={stats.data.articles_per_day} />
              </section>

              <div className="grid md:grid-cols-2 gap-4">
                <section className="bg-card border border-border/50 rounded-xl p-5">
                  <h2 className="text-sm font-semibold text-foreground mb-2">Top categories</h2>
                  <ul className="space-y-2 mt-3">
                    {stats.data.top_categories.map((c) => {
                      const max = stats.data!.top_categories[0]?.count || 1;
                      return (
                        <li key={c.category} className="flex items-center gap-3">
                          <span className="text-xs text-muted w-24 truncate">{c.category}</span>
                          <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
                            <div
                              className="h-full bg-primary/70 rounded-full"
                              style={{ width: `${(c.count / max) * 100}%` }}
                            />
                          </div>
                          <span className="text-xs font-mono text-muted tabular-nums w-10 text-right">
                            {c.count}
                          </span>
                        </li>
                      );
                    })}
                    {stats.data.top_categories.length === 0 && (
                      <li className="text-xs text-muted">No categorized articles yet.</li>
                    )}
                  </ul>
                </section>

                <section className="bg-card border border-border/50 rounded-xl p-5">
                  <h2 className="text-sm font-semibold text-foreground mb-2">
                    Most active sources (14d)
                  </h2>
                  <ul className="space-y-2 mt-3">
                    {stats.data.top_sources.map((s) => {
                      const max = stats.data!.top_sources[0]?.count || 1;
                      return (
                        <li key={s.source} className="flex items-center gap-3">
                          <span className="text-xs text-muted w-28 truncate">{s.source}</span>
                          <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
                            <div
                              className="h-full bg-ice/70 rounded-full"
                              style={{ width: `${(s.count / max) * 100}%` }}
                            />
                          </div>
                          <span className="text-xs font-mono text-muted tabular-nums w-10 text-right">
                            {s.count}
                          </span>
                        </li>
                      );
                    })}
                    {stats.data.top_sources.length === 0 && (
                      <li className="text-xs text-muted">No recent source activity.</li>
                    )}
                  </ul>
                </section>
              </div>
            </>
          ) : null}

          <section className="bg-card border border-border/50 rounded-xl p-5">
            <div className="flex items-baseline justify-between mb-2">
              <h2 className="text-sm font-semibold text-foreground">Trending events</h2>
              <span className="text-xs text-muted">by coverage momentum</span>
            </div>
            {trending.isLoading && (
              <div className="text-sm text-muted animate-pulse py-4">Loading…</div>
            )}
            {trending.data && trending.data.length === 0 && (
              <div className="text-sm text-muted py-4">
                No open events yet — run a cluster backfill or wait for ingestion.
              </div>
            )}
            {trending.data && trending.data.length > 0 && (
              <div>
                {trending.data.map((item, i) => (
                  <TrendingRow key={item.event_id} item={item} rank={i + 1} />
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </AuthGuard>
  );
}
