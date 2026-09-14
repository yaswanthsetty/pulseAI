"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { addBookmark, getArticle, removeBookmark } from "@/lib/api";
import { Shell } from "@/components/layout/Shell";
import { useToast } from "@/components/ui/Toast";

const FULL_FORMATTER = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

export default function ArticlePage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const router = useRouter();
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const { data: article, isLoading, isError } = useQuery({
    queryKey: ["article", id],
    queryFn: () => getArticle(id as string),
    enabled: Boolean(id),
  });

  const bookmark = useMutation({
    mutationFn: () =>
      article?.bookmarked ? removeBookmark(article.id) : addBookmark(article!.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["article", id] });
      queryClient.invalidateQueries({ queryKey: ["bookmarks"] });
      toast(article?.bookmarked ? "Bookmark removed" : "Bookmarked", "success");
    },
    onError: (err: Error) => toast(err.message, "error"),
  });

  if (!id || isError)
    return (
      <Shell>
        <div className="max-w-3xl mx-auto px-6 py-16 text-center">
          <p className="text-sm text-muted">Article not found.</p>
          <button
            onClick={() => router.back()}
            className="text-sm text-primary hover:text-primary-hover mt-3"
          >
            Go back
          </button>
        </div>
      </Shell>
    );

  return (
    <Shell>
      <div className="max-w-3xl mx-auto px-6 py-8">
        {isLoading || !article ? (
          <div className="space-y-4">
            <div className="h-8 w-3/4 bg-card rounded-xl animate-pulse" />
            <div className="h-4 w-1/3 bg-card rounded-xl animate-pulse" />
            <div className="h-40 bg-card rounded-xl animate-pulse" />
          </div>
        ) : (
          <article>
            <button
              onClick={() => router.back()}
              className="mb-6 text-sm text-primary hover:text-primary-hover flex items-center gap-1"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
              </svg>
              Back
            </button>

            <div className="flex items-start justify-between gap-4">
              <h1 className="text-2xl font-display font-bold text-foreground leading-tight">
                {article.title}
              </h1>
              <button
                onClick={() => bookmark.mutate()}
                disabled={bookmark.isPending}
                aria-label={article.bookmarked ? "Remove bookmark" : "Bookmark article"}
                aria-pressed={article.bookmarked}
                className={`shrink-0 p-2 rounded-xl border transition-colors ${
                  article.bookmarked
                    ? "border-primary/40 bg-primary/10 text-primary"
                    : "border-border/40 text-muted hover:text-foreground"
                }`}
              >
                <svg className="w-4 h-4" fill={article.bookmarked ? "currentColor" : "none"} viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0111.186 0z"
                  />
                </svg>
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-3 text-xs text-muted">
              {article.source && <span className="font-medium text-foreground/70">{article.source}</span>}
              {article.author && <span>by {article.author}</span>}
              <span>{FULL_FORMATTER.format(new Date(article.published_at))}</span>
              {article.category_code && (
                <span className="font-mono px-2 py-0.5 rounded-lg bg-secondary">{article.category_code}</span>
              )}
              {article.credibility !== null && article.credibility !== undefined && (
                <span title="Source credibility score">
                  credibility {(article.credibility * 100).toFixed(0)}%
                </span>
              )}
            </div>

            {article.description && (
              <p className="mt-5 text-base text-foreground/90 leading-relaxed font-medium">
                {article.description}
              </p>
            )}

            {article.content && (
              <div className="mt-6 bg-card border border-border/40 rounded-2xl p-6">
                <p className="text-sm text-foreground/80 leading-relaxed whitespace-pre-wrap">
                  {article.content}
                </p>
              </div>
            )}

            {article.event && (
              <div className="mt-6 bg-primary/5 border border-primary/20 rounded-2xl p-5">
                <div className="text-xs uppercase tracking-wider text-muted mb-1">
                  Part of an event
                </div>
                <div className="text-sm font-medium text-foreground">{article.event.title}</div>
                <div className="flex gap-3 mt-3">
                  <a
                    href={`/events?focus=${article.event.id}`}
                    className="text-sm text-primary hover:text-primary-hover"
                  >
                    Open event timeline →
                  </a>
                </div>
              </div>
            )}

            <div className="mt-6">
              <a
                href={article.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-5 py-2.5 bg-primary text-white rounded-xl text-sm font-medium hover:bg-primary-hover"
              >
                Read original
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H5.25A2.25 2.25 0 003 8.25v10.5A2.25 2.25 0 005.25 21h10.5A2.25 2.25 0 0018 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25" />
                </svg>
              </a>
            </div>
          </article>
        )}
      </div>
    </Shell>
  );
}
