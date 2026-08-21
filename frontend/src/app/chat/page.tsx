"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { chatStream, fetchConversations, type ChatEvent, type EvidenceItem, type Conversation } from "@/lib/api";
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

function EvidencePanel({ evidence }: { evidence: EvidenceItem[] }) {
  return (
    <div className="mt-3 p-3 bg-secondary/30 border border-border/40 rounded-xl">
      <div className="text-[10px] font-mono text-muted uppercase tracking-wider mb-2">Sources</div>
      <div className="space-y-1.5">
        {evidence.map((item) => (
          <div key={item.citation_id} className="flex items-start gap-2 text-xs">
            <span className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded-lg bg-primary/10 text-[10px] font-mono text-primary">
              {item.citation_id}
            </span>
            <div className="min-w-0">
              <p className="text-foreground leading-snug line-clamp-1">{item.title}</p>
              <p className="text-[10px] font-mono text-muted mt-0.5">{item.score.toFixed(3)}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ThinkingIndicator({ stages }: { stages: string[] }) {
  const stageLabels: Record<string, string> = { planner: "Planning", reasoner: "Reasoning", synthesizer: "Synthesizing" };
  return (
    <div className="flex items-center gap-2 py-2">
      <div className="flex gap-1">
        <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
        <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse [animation-delay:150ms]" />
        <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse [animation-delay:300ms]" />
      </div>
      <span className="text-xs font-mono text-muted">
        {stages.length > 0 ? stageLabels[stages[stages.length - 1]] || stages[stages.length - 1] : "Thinking…"}
      </span>
    </div>
  );
}

const ChatMessage = ({ message }: { message: Message }) => {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-4 chat-message-enter`}>
      <div className={`max-w-2xl ${isUser ? "bg-primary/10 border border-primary/20 rounded-2xl rounded-br-sm" : "bg-card border border-border/40 rounded-2xl rounded-bl-sm"} px-4 py-3`}>
        {message.thinking && message.thinking.length > 0 && <ThinkingIndicator stages={message.thinking} />}
        <div className="text-sm text-foreground leading-relaxed whitespace-pre-wrap">
          {message.content}
          {message.isStreaming && <span className="inline-block w-1.5 h-4 bg-primary/60 animate-pulse ml-0.5 -mb-0.5" />}
        </div>
        {message.evidence && message.evidence.length > 0 && (
          <>
            <div className="mt-2 flex items-center gap-2">
              <span className="text-[10px] font-mono text-muted">{message.evidence.length} sources</span>
              {message.agreement !== undefined && (
                <>
                  <span className="text-border">|</span>
                  <span className="text-[10px] font-mono text-muted">
                    agreement <span className={message.agreement >= 0.6 ? "text-success" : message.agreement >= 0.3 ? "text-signal" : "text-destructive"}>{(message.agreement * 100).toFixed(0)}%</span>
                  </span>
                </>
              )}
            </div>
            <EvidencePanel evidence={message.evidence} />
          </>
        )}
      </div>
    </div>
  );
};

const SUGGESTION_QUERIES = ["What are the latest AI developments?", "How is climate policy evolving?", "Summarize recent tech funding trends"];

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }, [messages.length]);

  useEffect(() => {
    if (getAccessToken()) {
      fetchConversations().then(setConversations).catch(() => {});
    }
  }, []);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming || !getAccessToken()) return;
    setInput("");
    const userMsg: Message = { id: crypto.randomUUID(), role: "user", content: text };
    const assistantMsg: Message = { id: crypto.randomUUID(), role: "assistant", content: "", isStreaming: true, thinking: [] };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);
    try {
      const stream = await chatStream(text);
      for await (const event of stream) {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || last.id !== assistantMsg.id) return prev;
          const updated = { ...last };
          switch (event.type) {
            case "token": updated.content += event.token || ""; break;
            case "thinking": if (event.stage) updated.thinking = [...(updated.thinking || []), event.stage]; break;
            case "evidence": updated.content = event.message || updated.content; updated.evidence = event.evidence; updated.agreement = event.agreement; updated.isStreaming = false; break;
            case "error": updated.content = event.error ? `Error: ${event.error}` : "An error occurred"; updated.isStreaming = false; break;
          }
          return [...prev.slice(0, -1), updated];
        });
      }
    } catch (err) {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (!last || last.id !== assistantMsg.id) return prev;
        return [...prev.slice(0, -1), { ...last, content: `Error: ${err instanceof Error ? err.message : "Unknown error"}`, isStreaming: false }];
      });
    } finally {
      setIsStreaming(false);
      inputRef.current?.focus();
    }
  }, [input, isStreaming]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  }, [handleSend]);

  return (
    <Shell>
      <div className="flex h-full">
        {/* Conversation history sidebar */}
        <div className="hidden md:flex w-56 flex-shrink-0 border-r border-border/40 flex-col">
          <div className="px-3 py-3 border-b border-border/40">
            <span className="text-[11px] font-mono text-muted uppercase tracking-wider">History</span>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
            {conversations.length > 0 ? conversations.map((c) => (
              <div key={c.id} className="px-3 py-2 rounded-lg text-xs text-muted truncate hover:bg-card-hover cursor-pointer transition-colors">
                {c.title || "Untitled conversation"}
              </div>
            )) : (
              <p className="text-[11px] text-muted text-center py-4">No conversations yet</p>
            )}
          </div>
        </div>

        {/* Chat area */}
        <div className="flex-1 flex flex-col min-w-0">
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
            {messages.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center">
                  <h1 className="text-xl font-display font-semibold text-foreground mb-2">Chat</h1>
                  <p className="text-sm text-muted mb-6">Ask questions about your news corpus</p>
                  <div className="flex flex-wrap gap-2 justify-center max-w-md">
                    {SUGGESTION_QUERIES.map((q) => (
                      <button key={q} onClick={() => { setInput(q); inputRef.current?.focus(); }} className="px-3 py-2 bg-card border border-border/40 rounded-xl text-xs text-muted hover:text-foreground hover:border-primary/20 transition-colors">
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <div className="max-w-3xl mx-auto">
                {messages.map((msg) => <ChatMessage key={msg.id} message={msg} />)}
              </div>
            )}
          </div>

          {/* Input */}
          <div className="border-t border-border/40 bg-sidebar px-6 py-4">
            <form onSubmit={(e) => { e.preventDefault(); handleSend(); }} className="max-w-3xl mx-auto flex gap-2">
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={getAccessToken() ? "Ask a question…" : "Log in to use chat"}
                disabled={!getAccessToken() || isStreaming}
                aria-label="Chat message"
                className="flex-1 bg-card border border-border/40 rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-muted outline-none focus:border-primary/40 focus:ring-1 focus:ring-primary/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              />
              <button
                type="submit"
                disabled={!input.trim() || isStreaming || !getAccessToken()}
                className="px-6 py-3 bg-primary text-white rounded-xl text-sm font-medium hover:bg-primary-hover disabled:opacity-30 disabled:cursor-not-allowed transition-all"
              >
                {isStreaming ? "…" : "Send"}
              </button>
            </form>
          </div>
        </div>
      </div>
    </Shell>
  );
}
