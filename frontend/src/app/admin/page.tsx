"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchUsers, updateUserRole, type User } from "@/lib/api";
import { Shell } from "@/components/layout/Shell";
import { useToast } from "@/components/ui/Toast";
import { AuthGuard } from "@/components/AuthGuard";

const ROLES = ["user", "analyst", "admin"];
const FULL_FORMATTER = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });

export default function AdminPage() {
  return (
    <AuthGuard>
      <Shell>
        <AdminContent />
      </Shell>
    </AuthGuard>
  );
}

function AdminContent() {
  const [page, setPage] = useState(1);
  const { toast } = useToast();
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["admin-users", page],
    queryFn: () => fetchUsers({ page, page_size: 20 }),
  });

  const roleMutation = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) => updateUserRole(userId, role),
    onSuccess: () => { toast("Role updated", "success"); qc.invalidateQueries({ queryKey: ["admin-users"] }); },
    onError: (err: Error) => toast(err.message, "error"),
  });

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <h1 className="text-xl font-display font-semibold text-foreground mb-2">Admin Panel</h1>
      <p className="text-sm text-muted mb-6">Manage users and their roles.</p>

      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => <div key={i} className="h-16 bg-card rounded-xl animate-pulse" />)}
        </div>
      ) : data && data.items.length > 0 ? (
        <div className="space-y-2">
          {data.items.map((user) => (
            <div key={user.id} className="bg-card border border-border/40 rounded-xl p-4 flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground truncate">{user.display_name || user.email}</p>
                <p className="text-[11px] font-mono text-muted">{user.email}</p>
              </div>
              <div className="flex items-center gap-3 flex-shrink-0">
                <span className="text-[11px] font-mono text-muted">{FULL_FORMATTER.format(new Date(user.created_at))}</span>
                <select
                  value={user.role}
                  onChange={(e) => roleMutation.mutate({ userId: user.id, role: e.target.value })}
                  aria-label={`Role for ${user.email}`}
                  className="bg-secondary/50 rounded-lg px-2 py-1.5 text-xs font-mono text-foreground border border-border/40 outline-none focus:border-primary/40 transition-colors"
                >
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted text-center py-8">No users found</p>
      )}

      {/* Pagination */}
      {data && data.total > 20 && (
        <div className="flex items-center justify-center gap-4 mt-6">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 text-xs text-muted hover:text-foreground disabled:opacity-30 transition-colors"
          >
            Previous
          </button>
          <span className="text-xs font-mono text-muted">Page {page}</span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={data.items.length < 20}
            className="px-3 py-1.5 text-xs text-muted hover:text-foreground disabled:opacity-30 transition-colors"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
