"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const ITEMS = [
  { label: "Search", href: "/search", icon: "\u{1F50D}" },
  { label: "Events", href: "/events", icon: "\u{1F4CB}" },
  { label: "Chat", href: "/chat", icon: "\u{1F4AC}" },
  { label: "Reports", href: "/reports", icon: "\u{1F4C4}" },
  { label: "Admin", href: "/admin", icon: "\u{2699}\u{FE0F}" },
  { label: "Settings", href: "/settings", icon: "\u{2699}\u{FE0F}" },
];

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const router = useRouter();

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!open) return null;

  const filtered = ITEMS.filter((i) =>
    i.label.toLowerCase().includes(query.toLowerCase())
  );

  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center pt-[20vh]">
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
      <div className="relative w-full max-w-md bg-card border border-border/60 rounded-2xl shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border/40">
          <svg className="w-4 h-4 text-muted flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
          </svg>
          <input
            autoFocus
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type a command…"
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted outline-none"
            aria-label="Search commands"
          />
          <kbd className="px-1.5 py-0.5 text-[10px] font-mono text-muted bg-secondary rounded">ESC</kbd>
        </div>
        <div className="max-h-64 overflow-y-auto p-1">
          {filtered.length === 0 ? (
            <p className="text-sm text-muted text-center py-6">No matching commands</p>
          ) : (
            filtered.map((item) => (
              <button
                key={item.href}
                onClick={() => { router.push(item.href); setOpen(false); }}
                className="flex items-center gap-3 w-full px-4 py-2.5 text-sm text-foreground hover:bg-primary/10 rounded-xl transition-colors text-left"
              >
                <span className="text-base">{item.icon}</span>
                <span className="font-medium">{item.label}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
