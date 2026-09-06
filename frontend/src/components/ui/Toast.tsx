"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

type ToastVariant = "success" | "error" | "info";

interface Toast {
  id: number;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (message: string, variant?: ToastVariant) => void;
  dismissToast: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue>({
  toast: () => {},
  dismissToast: () => {},
});

const TOAST_STYLES: Record<ToastVariant, string> = {
  success: "bg-success/15 text-success border-success/30",
  error: "bg-destructive/15 text-destructive border-destructive/30",
  info: "bg-primary/15 text-primary border-primary/30",
};

const AUTO_DISMISS_MS = 4000;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);
  const timersRef = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismissToast = useCallback((id: number) => {
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, variant: ToastVariant = "info") => {
      const id = ++idRef.current;
      setToasts((prev) => [...prev.slice(-4), { id, message, variant }]);
      timersRef.current.set(
        id,
        setTimeout(() => dismissToast(id), AUTO_DISMISS_MS)
      );
    },
    [dismissToast]
  );

  // Clear any pending timers on unmount
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach(clearTimeout);
      timers.clear();
    };
  }, []);

  return (
    <ToastContext.Provider value={{ toast, dismissToast }}>
      {children}
      <div
        className="fixed bottom-4 right-4 z-100 flex flex-col gap-2 max-w-[calc(100vw-2rem)]"
        role="region"
        aria-label="Notifications"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              "flex items-center gap-3 pl-4 pr-2 py-2.5 rounded-xl border text-sm font-medium shadow-lg animate-slide-up",
              TOAST_STYLES[t.variant]
            )}
          >
            <p className="flex-1">{t.message}</p>
            <button
              onClick={() => dismissToast(t.id)}
              aria-label="Dismiss notification"
              className="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg hover:bg-black/20 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

