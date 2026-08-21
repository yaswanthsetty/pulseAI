"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchApiKeys, createApiKey, revokeApiKey, type ApiKeyInfo } from "@/lib/api";
import { Shell } from "@/components/layout/Shell";
import { AuthGuard } from "@/components/AuthGuard";
import { useToast } from "@/components/ui/Toast";

const FULL_FORMATTER = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });

export default function SettingsPage() {
  return (
    <AuthGuard>
      <Shell>
        <SettingsContent />
      </Shell>
    </AuthGuard>
  );
}

function SettingsContent() {
  const [label, setLabel] = useState("");
  const [newKey, setNewKey] = useState<string | null>(null);
  const { toast } = useToast();
  const qc = useQueryClient();

  const { data: keys, isLoading } = useQuery({
    queryKey: ["api-keys"],
    queryFn: fetchApiKeys,
  });

  const createMutation = useMutation({
    mutationFn: () => createApiKey(label, ["read"]),
    onSuccess: (data) => { setNewKey(data.key); setLabel(""); toast("API key created", "success"); qc.invalidateQueries({ queryKey: ["api-keys"] }); },
    onError: (err: Error) => toast(err.message, "error"),
  });

  const revokeMutation = useMutation({
    mutationFn: revokeApiKey,
    onSuccess: () => { toast("API key revoked", "success"); qc.invalidateQueries({ queryKey: ["api-keys"] }); },
    onError: (err: Error) => toast(err.message, "error"),
  });

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <h1 className="text-xl font-display font-semibold text-foreground mb-2">Settings</h1>
      <p className="text-sm text-muted mb-8">Manage your API keys.</p>

      {/* Create new key */}
      <div className="bg-card border border-border/40 rounded-xl p-5 mb-6">
        <h2 className="text-sm font-display font-semibold text-foreground mb-3">Create API Key</h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Key label (e.g., dev, production)"
            aria-label="API key label"
            className="flex-1 bg-secondary/50 rounded-xl px-4 py-2.5 text-sm text-foreground placeholder:text-muted outline-none focus:ring-1 focus:ring-primary/40 border border-transparent focus:border-primary/30 transition-colors"
          />
          <button
            onClick={() => createMutation.mutate()}
            disabled={!label.trim() || createMutation.isPending}
            className="px-4 py-2.5 bg-primary text-white rounded-xl text-sm font-medium hover:bg-primary-hover disabled:opacity-30 disabled:cursor-not-allowed transition-all"
          >
            {createMutation.isPending ? "Creating…" : "Create"}
          </button>
        </div>
      </div>

      {/* Show new key */}
      {newKey && (
        <div className="bg-success/10 border border-success/30 rounded-xl p-4 mb-6">
          <p className="text-xs text-success font-medium mb-2">API Key (copy it now, it won’t be shown again):</p>
          <code className="block bg-background rounded-lg px-3 py-2 text-sm font-mono text-foreground break-all">{newKey}</code>
          <button onClick={() => { navigator.clipboard.writeText(newKey); toast("Copied to clipboard", "success"); }} className="mt-2 text-xs text-primary hover:text-primary-hover transition-colors">
            Copy to clipboard
          </button>
        </div>
      )}

      {/* Existing keys */}
      <div>
        <h2 className="text-sm font-display font-semibold text-foreground mb-3">Existing Keys</h2>
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2].map((i) => <div key={i} className="h-14 bg-card rounded-xl animate-pulse" />)}
          </div>
        ) : keys && keys.length > 0 ? (
          <div className="space-y-2">
            {keys.map((k) => (
              <div key={k.id} className="bg-card border border-border/40 rounded-xl p-4 flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">{k.label || "Unnamed key"}</p>
                  <p className="text-[11px] font-mono text-muted">
                    Scopes: {(k.scopes || []).join(", ")} | Created {FULL_FORMATTER.format(new Date(k.created_at))}
                  </p>
                </div>
                <button
                  onClick={() => revokeMutation.mutate(k.id)}
                  className="px-3 py-1.5 text-xs text-destructive hover:bg-destructive/10 rounded-lg transition-colors flex-shrink-0"
                >
                  Revoke
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted text-center py-6">No API keys yet</p>
        )}
      </div>
    </div>
  );
}
