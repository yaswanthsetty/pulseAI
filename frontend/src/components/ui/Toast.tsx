"use client";

import { useEffect, useState, useRef, createContext, useContext, useCallback } from "react";

interface Toast {
  id: string;
  message: string;
  type: "success" | "error" | "info";
}

/* Hoist static map outside component (rerender-memo-with-default-value) */
const TOAST_COLORS: Record<Toast["type"], string> = {
  success: "bg-success/10 border-success/30 text-success",
  error: "bg-destructive/10 border-destructive/30 text-destructive",
  info: "bg-card border-border text-foreground",
};

interface ToastContextType {
  toast: (message: string, type?: Toast["type"]) => void;
}

const ToastContext = createContext<ToastContextType>({ toast: () => {} });

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  /* Use useRef for mutable listener map (rerender-use-ref-transient-values) */
  const idCounter = useRef(0);

  const toast = useCallback((message: string, type: Toast["type"] = "info") => {
    idCounter.current += 1;
    const id = String(idCounter.current);
    setToasts((prev) => [...prev, { id, message, type }]);
  }, []);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 4000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div
      className={`pointer-events-auto px-4 py-3 rounded-xl border text-sm shadow-lg animate-slide-up ${TOAST_COLORS[toast.type]}`}
      role="alert"
      aria-live="polite"
    >
      {toast.message}
    </div>
  );
}
