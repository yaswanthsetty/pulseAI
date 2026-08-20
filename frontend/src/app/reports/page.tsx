"use client";

import { useState, useEffect, useCallback } from "react";
import { useMutation } from "@tanstack/react-query";
import { generateReport, fetchReports, type Report } from "@/lib/api";
import { Shell } from "@/components/layout/Shell";

/* Hoist formatter outside component (js-cache-function-results) */
const DATE_FORMATTER = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });

export default function ReportsPage() {
  const [topic, setTopic] = useState("");
  const [timeframe, setTimeframe] = useState("7d");
  const [reports, setReports] = useState<Report[]>([]);

  useEffect(() => {
    fetchReports()
      .then(setReports)
      .catch((err) => console.error("Failed to load reports:", err));
  }, []);

  const mutation = useMutation({
    mutationFn: () => generateReport(topic, timeframe),
    onSuccess: (data) => {
      setReports((prev) => [data, ...prev]);
      setTopic("");
    },
  });

  /* Wrap in useCallback (rerender-functional-setstate) */
  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    if (topic.trim()) mutation.mutate();
  }, [topic, mutation]);

  return (
    <Shell>
      <div className="max-w-[780px] mx-auto px-4 py-8">
        <div className="mb-8">
          <h1 className="font-display font-semibold text-2xl text-foreground mb-2">Reports</h1>
          <p className="text-sm text-muted">Generate executive intelligence reports on any topic with source analysis.</p>
        </div>

        <form onSubmit={handleSubmit} className="mb-8">
          <div className="bg-card rounded-2xl border border-border/60 p-4">
            <div className="flex gap-3 mb-3">
              <input type="text" value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Report topic (e.g., AI regulation in EU)" aria-label="Report topic" className="flex-1 bg-input rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-muted outline-none focus:border-primary/40 border border-transparent transition-colors" />
              <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} aria-label="Timeframe" className="bg-input rounded-xl px-3 py-3 text-sm text-foreground border border-transparent outline-none focus:border-primary/40 transition-colors">
                <option value="24h">Last 24h</option>
                <option value="7d">Last 7 days</option>
                <option value="30d">Last 30 days</option>
                <option value="90d">Last 90 days</option>
              </select>
              <button type="submit" disabled={!topic.trim() || mutation.isPending} className="px-6 py-3 bg-primary text-white rounded-xl text-sm font-medium hover:bg-primary-hover disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-150">
                {mutation.isPending ? "Generating\u2026" : "Generate"}
              </button>
            </div>
          </div>
        </form>

        <div className="space-y-3">
          {reports.map((r) => (
            <div key={r.id} className="bg-card border border-border/40 rounded-xl p-5 chat-message-enter">
              <div className="flex items-start justify-between gap-4 mb-2">
                <h3 className="font-medium text-sm text-foreground">{r.topic}</h3>
                <span className={"text-[11px] font-mono px-2 py-0.5 rounded-lg " + (r.status === "completed" ? "bg-success/10 text-success" : r.status === "failed" ? "bg-destructive/10 text-destructive" : "bg-secondary text-muted")}>
                  {r.status}
                </span>
              </div>
              {r.timeframe ? <p className="text-xs text-muted mb-2">Timeframe: {r.timeframe}</p> : null}
              <div className="flex items-center gap-3 text-[11px] text-muted">
                <span>{DATE_FORMATTER.format(new Date(r.created_at))}</span>
                {r.evidence_agreement ? (
                  <span className="font-mono">{Math.round((r.evidence_agreement as number) * 100)}% agreement</span>
                ) : null}
              </div>
              {r.content ? (
                <div className="mt-3 pt-3 border-t border-border/40 text-xs text-muted leading-relaxed line-clamp-4">
                  {typeof r.content === "object" ? JSON.stringify(r.content).slice(0, 300) : String(r.content).slice(0, 300)}
                </div>
              ) : null}
            </div>
          ))}

          {reports.length === 0 ? (
            <div className="text-center py-16">
              <p className="text-sm text-muted">No reports yet. Enter a topic above to generate one.</p>
            </div>
          ) : null}
        </div>
      </div>
    </Shell>
  );
}
