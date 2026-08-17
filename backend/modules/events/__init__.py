"""Events module — Phase 3 (FR-16..FR-18), spec §14 incremental clustering.

* **Fast path (FR-18)** — ``cluster_article_job`` (cluster queue) matches each
  newly-embedded article against open-event centroids in Qdrant; on a hit it
  attaches the article, grows the centroid as a running average, and bumps
  ``last_updated``.
* **Slow path (FR-16)** — ``cluster_unmatched_articles`` runs UMAP+HDBSCAN over
  a bounded recent window of unmatched articles (scheduler, default every
  30 min) to detect genuinely new events; each becomes an ``events`` row with
  title, extractive summary, confidence, and a centroid point (FR-17).
* **Closure (FR-17)** — ``close_stale_events`` closes events idle for
  ``event_close_hours`` (default 72h) and drops them from the centroid
  collection.

The ``events``/``event_articles`` tables have existed since Phase 1; this
module is what populates them. ``GET /api/v1/events`` (list + detail with
timeline) is served by the events router (spec §20).
"""
