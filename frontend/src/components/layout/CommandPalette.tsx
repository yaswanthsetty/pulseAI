"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { cn } from "@/lib/utils";

interface CommandItem {
  label: string;
  href: string;
  icon: React.ReactNode;
  keywords?: string;
}

const ICON_PROPS = {
  className: "w-4 h-4 flex-shrink-0",
  fill: "none",
  viewBox: "0 0 24 24",
  stroke: "currentColor",
  strokeWidth: 1.8,
} as const;

const ITEMS: CommandItem[] = [
  {
    label: "Search",
    href: "/search",
    icon: (
      <svg {...ICON_PROPS} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
      </svg>
    ),
    keywords: "find articles semantic keyword hybrid",
  },
  {
    label: "Events",
    href: "/events",
    icon: (
      <svg {...ICON_PROPS} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6.75 3v2.25M17.25 3v2.25M3 18.75V7.5a2.25 2.25 0 0 1 2.25-2.25h13.5A2.25 2.25 0 0 1 21 7.5v11.25m-18 0A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75m-18 0v-7.5A2.25 2.25 0 0 1 5.25 9h13.5A2.25 2.25 0 0 1 21 11.25v7.5" />
      </svg>
    ),
    keywords: "timeline clusters stories tracking",
  },
  {
    label: "Chat",
    href: "/chat",
    icon: (
      <svg {...ICON_PROPS} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 20.25c4.97 0 9-3.694 9-8.25s-4.03-8.25-9-8.25S3 7.444 3 12c0 2.104.859 4.023 2.273 5.48.432.447.74 1.04.586 1.641a4.483 4.483 0 0 1-.923 1.785A5.969 5.969 0 0 0 6 20.25c1.288 0 2.49-.36 3.51-.98" />
      </svg>
    ),
    keywords: "ask questions assistant streaming",
  },
  {
    label: "Reports",
    href: "/reports",
    icon: (
      <svg {...ICON_PROPS} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
      </svg>
    ),
    keywords: "generate intelligence summary briefing",
  },
  {
    label: "Admin",
    href: "/admin",
    icon: (
      <svg {...ICON_PROPS} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z" />
      </svg>
    ),
    keywords: "users roles management panel",
  },
  {
    label: "Settings",
    href: "/settings",
    icon: (
      <svg {...ICON_PROPS} aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.24-.438.613-.43.991a7.723 7.723 0 0 1 0 .255c-.008.379.137.751.43.992l1.004.827c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
      </svg>
    ),
    keywords: "api keys account preferences",
  },
];

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const toggle = useCallback(() => {
    setOpen((o) => {
      if (!o) {
        // Reset palette state each time it opens
        setQuery("");
        setActiveIndex(0);
      }
      return !o;
    });
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        toggle();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle]);

  // Lock scroll while open and focus the input
  useEffect(() => {
    if (!open) return;
    document.body.style.overflow = "hidden";
    const raf = requestAnimationFrame(() => inputRef.current?.focus());
    return () => {
      document.body.style.overflow = "";
      cancelAnimationFrame(raf);
    };
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return ITEMS;
    return ITEMS.filter(
      (i) =>
        i.label.toLowerCase().includes(q) ||
        (i.keywords && i.keywords.includes(q))
    );
  }, [query]);

  // Clamp during render instead of resetting via effect
  const safeIndex = filtered.length === 0 ? 0 : Math.min(activeIndex, filtered.length - 1);

  const close = useCallback(() => setOpen(false), []);

  const navigate = useCallback(
    (href: string) => {
      close();
      router.push(href);
    },
    [close, router]
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) =>
          filtered.length === 0 ? 0 : (Math.min(i, filtered.length - 1) + 1) % filtered.length
        );
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => {
          if (filtered.length === 0) return 0;
          const clamped = Math.min(i, filtered.length - 1);
          return (clamped - 1 + filtered.length) % filtered.length;
        });
      } else if (e.key === "Enter") {
        e.preventDefault();
        const item = filtered[safeIndex];
        if (item) navigate(item.href);
      } else if (e.key === "Tab") {
        // Focus is trapped inside the dialog
        e.preventDefault();
      }
    },
    [close, filtered, safeIndex, navigate]
  );

  // Keep the active option visible while arrowing through the list
  useEffect(() => {
    if (!open || !listRef.current) return;
    listRef.current
      .querySelector(`[data-index="${safeIndex}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [safeIndex, open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-200 flex items-start justify-center pt-[12vh] px-4"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      onKeyDown={onKeyDown}
    >
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm animate-fade-in"
        onClick={close}
        aria-hidden="true"
      />
      <div className="relative w-full max-w-md bg-card border border-border/60 rounded-2xl shadow-2xl overflow-hidden animate-slide-up">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border/40">
          <svg className="w-4 h-4 text-muted shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
          </svg>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type a command…"
            role="combobox"
            aria-expanded={filtered.length > 0}
            aria-controls="command-palette-list"
            aria-activedescendant={filtered[safeIndex] ? `command-option-${safeIndex}` : undefined}
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted outline-none"
            aria-label="Search commands"
          />
          <kbd className="px-1.5 py-0.5 text-[10px] font-mono text-muted bg-secondary rounded">ESC</kbd>
        </div>
        <div ref={listRef} id="command-palette-list" role="listbox" aria-label="Commands" className="max-h-64 overflow-y-auto p-1">
          {filtered.length === 0 ? (
            <p className="text-sm text-muted text-center py-6" role="status">
              No matching commands
            </p>
          ) : (
            filtered.map((item, idx) => (
              <button
                key={item.href}
                id={`command-option-${idx}`}
                data-index={idx}
                role="option"
                aria-selected={idx === safeIndex}
                onClick={() => navigate(item.href)}
                onMouseMove={() => setActiveIndex(idx)}
                className={cn(
                  "flex items-center gap-3 w-full px-4 py-2.5 text-sm rounded-xl transition-colors text-left",
                  idx === safeIndex
                    ? "bg-primary/10 text-primary"
                    : "text-foreground hover:bg-primary/10"
                )}
              >
                {item.icon}
                <span className="font-medium">{item.label}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
