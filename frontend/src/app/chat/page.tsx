"use client";

import { useState, useRef, useEffect, useCallback, memo } from "react";
import { chatStream, type ChatEvent, type EvidenceItem } from "@/lib/api";
import { Shell } from "@/components/layout/Shell";
import { getAccessToken } from "@/lib/api";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  evidence?: EvidenceItem[];
  agreement?: number;
  thinking?: string[];
  isStreaming?: boolean;
}

const THINKING_LABELS: Record<string, string> = {
  planning: "Planning search strategy",
  searching: "Searching knowledge base",
  reading: "Reading articles",
  reasoning: "Analyzing evidence",
  synthesizing: "Synthesizing answer",
  thinking: "Thinking",
};

/* Hoist static data outside component (rerender-memo-with-default-value) */
const SUGGESTION_QUERIES = ["What's trending in AI?", "Summarize today's tech news", "Compare Apple and Google earnings"];

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  /* Use ref for input to avoid stale closure in handleSubmit (rerender-dependencies) */
  const inputRef_value = useRef(input);
  inputRef_value.current = input;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]); /* Subscribe to length, not full array (rerender-dependencies) */

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    /* Read from ref to avoid stale closure (rerender-dependencies) */
    const text = inputRef_value.current.trim();
    if (!text || isLoading) return;

    setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "user", content: text }]);
    setInput("");
    setIsLoading(true);

    setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: "assistant", content: "", isStreaming: true, thinking: [] }]);

    const token = getAccessToken();
    const eventSource = await chatStream(text, token || undefined);

    eventSource.onmessage = (event) => {
      try {
        const data: ChatEvent = JSON.parse(event.data);
        setMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (!last || last.role !== "assistant") return prev;
          switch (data.type) {
            case "token": last.content += data.delta || ""; break;
            case "thinking": if (data.stage) last.thinking = [...(last.thinking || []), data.stage]; break;
            case "evidence": last.evidence = data.evidence || []; break;
            case "agreement": last.agreement = data.score; break;
            case "done": last.isStreaming = false; break;
            case "error": last.content += data.message || ""; last.isStreaming = false; break;
          }
          return updated;
        });
      } catch { /* skip malformed events */ }
    };

    eventSource.onerror = () => {
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant" && !last.content) last.content = "Connection lost.";
        if (last) last.isStreaming = false;
        return updated;
      });
      setIsLoading(false);
    };

    eventSource.addEventListener("done", () => { setIsLoading(false); eventSource.close(); });
  }, [isLoading]); /* Only depends on isLoading, not input (input read via ref) */

  /* Wrap in useCallback (rerender-functional-setstate) */
  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(e); }
  }, [handleSubmit]);

  return (
    <Shell>
      <div className="flex flex-col h-full max-w-[780px] mx-auto">
        <div className="flex-1 overflow-y-auto px-4 py-6">
          {/* Use ternary, not && (rendering-conditional-render) */}
          {messages.length === 0 ? <EmptyState /> : null}
          <div className="space-y-6">
            {messages.map((msg) => (<MessageBubble key={msg.id} message={msg} />))}
          </div>
          <div ref={messagesEndRef} />
        </div>
        <div className="border-t border-border/50 bg-background px-4 py-4">
          <form onSubmit={handleSubmit} className="relative">
            <div className="flex items-end gap-3 bg-card rounded-2xl border border-border/60 px-4 py-3 focus-within:border-primary/40 transition-colors">
              <textarea ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={handleKeyDown} placeholder="Ask anything about the news..." rows={1} aria-label="Chat message input" className="flex-1 bg-transparent text-foreground placeholder:text-muted text-sm resize-none outline-none max-h-32" style={{ minHeight: "24px" }} />
              <button type="submit" disabled={!input.trim() || isLoading} aria-label="Send message" className="flex-shrink-0 w-9 h-9 rounded-xl bg-primary text-white flex items-center justify-center hover:bg-primary-hover disabled:opacity-30 disabled:cursor-not-allowed transition-all duration-150">
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" /></svg>
              </button>
            </div>
            <p className="text-center text-[11px] text-muted mt-2">Press Enter to send, Shift+Enter for new line</p>
          </form>
        </div>
      </div>
    </Shell>
  );
}

/* Hoist static JSX outside component (rendering-hoist-jsx) */
function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-[60vh] text-center">
      <div className="w-14 h-14 rounded-2xl bg-primary/10 flex items-center justify-center mb-5">
        <svg className="w-7 h-7 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.087.16 2.185.283 3.293.369V21l4.076-4.076a1.526 1.526 0 011.037-.443 48.282 48.282 0 005.68-.494c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0012 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018z" />
        </svg>
      </div>
      <h2 className="font-display font-semibold text-lg text-foreground mb-2">PulseAI Chat</h2>
      <p className="text-sm text-muted max-w-sm leading-relaxed">
        Ask questions about the latest news. I search through articles in real-time and cite my sources.
      </p>
      <div className="flex gap-2 mt-6 flex-wrap justify-center">
        {SUGGESTION_QUERIES.map((q) => (
          <button key={q} className="px-3 py-1.5 text-xs text-muted bg-card hover:bg-card-hover border border-border/50 rounded-lg transition-colors">{q}</button>
        ))}
      </div>
    </div>
  );
}

/* Memoize to prevent re-rendering all messages on each token (rerender-memo) */
const MessageBubble = memo(function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  return (
    <div className={"chat-message-enter flex " + (isUser ? "justify-end" : "justify-start")}>
      <div className={"max-w-[85%] " + (isUser ? "" : "w-full")}>
        {!isUser && (
          <div className="flex items-center gap-2 mb-2">
            <div className="w-6 h-6 rounded-lg bg-primary/10 flex items-center justify-center">
              <span className="text-primary text-[10px] font-bold">P</span>
            </div>
            <span className="text-xs font-medium text-muted">PulseAI</span>
          </div>
        )}
        <div className={isUser ? "bg-primary text-white rounded-2xl rounded-br-md px-4 py-3 text-sm leading-relaxed" : "bg-card border border-border/40 rounded-2xl rounded-bl-md px-4 py-3 text-sm leading-relaxed"}>
          {!isUser && message.thinking && message.thinking.length > 0 && (
            <div className="mb-3 space-y-1">
              {message.thinking.map((stage, i) => (
                <div key={i} className="flex items-center gap-2 text-[11px] text-muted">
                  <div className="w-1.5 h-1.5 rounded-full bg-primary/60 animate-pulse-dot" />
                  {THINKING_LABELS[stage] || stage}
                </div>
              ))}
            </div>
          )}
          <div className={isUser ? "" : (message.isStreaming ? "streaming-cursor" : "")}>
            {message.content || (!isUser && message.isStreaming ? (
              <div className="flex gap-1.5 py-1">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="w-2 h-2 rounded-full bg-muted" style={{animation: "dot-bounce 1.4s ease-in-out infinite", animationDelay: (i * 0.16) + "s"}} />
                ))}
              </div>
            ) : null)}
          </div>
        </div>
        {!isUser && message.evidence && message.evidence.length > 0 && (
          <div className="mt-3 bg-card border border-border/40 rounded-xl px-4 py-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[11px] font-medium text-muted uppercase tracking-wider">Sources</span>
              {message.agreement !== undefined && (
                <span className="text-[11px] font-mono text-muted">{Math.round(message.agreement * 100)}% agreement</span>
              )}
            </div>
            <div className="space-y-2">
              {message.evidence.slice(0, 5).map((ev, i) => (
                <div key={i} className="flex items-start gap-2 text-xs">
                  <span className="font-mono text-primary text-[11px] font-medium mt-0.5">[{i + 1}]</span>
                  <div className="min-w-0">
                    <p className="text-foreground/80 truncate">{ev.title || ev.source || "Article"}</p>
                    {ev.snippet && <p className="text-muted truncate mt-0.5">{ev.snippet}</p>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
});
