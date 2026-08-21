"use client";

import { useState, useCallback } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { generateReport, fetchReports, getReport, type Report } from "@/lib/api";
import { Shell } from "@/components/layout/Shell";
import { useToast } from "@/components/ui/Toast";

const FULL_FORMATTER = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });

export default function ReportsPage() {
  const [topic, setTopic] = useState("");
  const [timeframe, setTimeframe] = useState("7d");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { toast } = useToast();

  const { data: reports, isLoading } = useQuery({
    queryKey: ["reports"],
    queryFn: fetchReports,
  });

  const mutation = useMutation({
    mutationFn: () => generateReport(topic, timeframe),
    onSuccess: () => { toast("Report generated", "success"); setTopic(""); },
    onError: (err: Error) => toast(err.message, "error"),
  });

  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    if (topic.trim()) mutation.mutate();
  }, [topic, mutation]);

  return (
    <Shell>
      <div className="max-w-4xl mx-auto px-6 py-8">
        <h1 className="text-xl font-display font-semibold text-foreground mb-2">Reports</h1>
        <p className="text-sm text-muted mb-6">Generate executive intelligence reports with source analysis.</p>

        {/* Generate form */}
        <form onSubmit={handleSubmit} className="mb-8">
          <div className="bg-card border border-border/40 rounded-2xl p-4">
            <div className="flex gap-3">
              <input
                type="text"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="Report topic (e.g., AI regulation in EU)"
                aria-label="Report topic"
                className="flex-1 bg-secondary/50 rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-muted outline-none focus:ring-1 focus:ring-primary/40 border border-transparent focus:border-primary/30 transition-colors"
              />
              <select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
                aria-label="Timeframe"
                className="bg-secondary/50 rounded-xl px-3 py-3 text-sm text-foreground border border-transparent outline-none focus:border-primary/30 transition-colors"
              >
                <option value="24h">Last 24h</option>
                <option value="7d">Last 7 days</option>
                <option value="30d">Last 30 days</option>
                <option value="90d">Last 90 days</option>
              </select>
              <button
                type="submit"
                disabled={!topic.trim() || mutation.isPending}
                className="px-6 py-3 bg-primary text-white rounded-xl text-sm font-medium hover:bg-primary-hover disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-150"
              >
                {mutation.isPending ? "Generating…" : "Generate"}
              </button>
            </div>
          </div>
        </form>

        {/* Report detail or list */}
        {selectedId ? (
          <ReportDetail id={selectedId} onBack={() => setSelectedId(null)} />
        ) : (
          <div className="space-y-3">
            {isLoading ? (
              [1, 2, 3].map((i) => <div key={i} className="h-24 bg-card rounded-xl animate-pulse" />)
            ) : reports && reports.length > 0 ? (
              reports.map((r) => (
                <button
                  key={r.id}
                  onClick={() => setSelectedId(r.id)}
                  className="w-full text-left bg-card border border-border/40 rounded-xl p-5 hover:border-primary/20 transition-colors"
                >
                  <div className="flex items-start justify-between gap-4 mb-2">
                    <h3 className="font-medium text-sm text-foreground">{r.topic}</h3>
                    <span className={"text-[11px] font-mono px-2 py-0.5 rounded-lg " + (r.status === "completed" ? "bg-success/10 text-success" : r.status === "failed" ? "bg-destructive/10 text-destructive" : "bg-secondary text-muted")}>
                      {r.status}
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-muted">
                    {FULL_FORMATTER.format(new Date(r.created_at))}
                  </div>
                </button>
              ))
            ) : (
              <p className="text-sm text-muted text-center py-8">No reports yet. Enter a topic above to generate one.</p>
            )}
          </div>
        )}
      </div>
    </Shell>
  );
}

function ReportDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const { data: report, isLoading } = useQuery({
    queryKey: ["report", id],
    queryFn: () => getReport(id),
  });

  if (isLoading) return <div className="h-40 bg-card rounded-xl animate-pulse" />;
  if (!report) return <p className="text-sm text-muted">Report not found</p>;

  const content = report.content
    ? (typeof report.content === "object" ? JSON.stringify(report.content, null, 2) : String(report.content))
    : "No content available";

  return (
    <div className="bg-card border border-border/40 rounded-xl p-6">
      <button onClick={onBack} className="mb-4 text-sm text-primary hover:text-primary-hover flex items-center gap-1">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
        </svg>
        Back to reports
      </button>
      <div className="flex items-center gap-3 mb-3">
        <h2 className="text-lg font-display font-semibold text-foreground">{report.topic}</h2>
        <span className={"text-[11px] font-mono px-2 py-0.5 rounded-lg " + (report.status === "completed" ? "bg-success/10 text-success" : "bg-secondary text-muted")}>
          {report.status}
        </span>
      </div>
      {report.timeframe && <p className="text-xs text-muted mb-2">Timeframe: {report.timeframe}</p>}
      <div className="text-[11px] font-mono text-muted mb-4">
        {FULL_FORMATTER.format(new Date(report.created_at))}
        {report.evidence_agreement && typeof report.evidence_agreement === "number" && (
          <span className="ml-2">Agreement: {Math.round(report.evidence_agreement * 100)}%</span>
        )}
      </div>
      <div className="bg-secondary/30 rounded-xl p-4 text-sm text-foreground/80 leading-relaxed whitespace-pre-wrap max-h-96 overflow-y-auto">
        {content}
      </div>
    </div>
  );
}
