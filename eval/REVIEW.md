# Query-set review (20% sample) — DRAFT, not frozen

Status: `eval/queries.jsonl` is **draft_unreviewed**. It is NOT frozen and no
scored harness run may use it until Yaswanth approves and the pools are graded.

## What this set is

- 60 queries over corpus snapshot `20261011_18b35cf` (80 articles / 397
  chunks, Qdrant-consistent), split 20 fact / 15 compare / 15 multi-hop /
  10 time-bound, all English, with fast/deep path hints.
- Written by the coding agent **from article text only** (never from retrieval
  output), per the plan §1.3. Every `required_facts` string was machine-checked
  to be a literal substring (case-insensitive) of the chunk text of one of the
  query's relevant articles — but that only proves the string exists, not that
  it is the *right* fact to answer the question. That is what the review is for.
- `relevant_chunk_ids` currently lists all chunks of the relevant articles;
  per-chunk 2/1/0 grades come from the blinded pool pass afterwards.

## The 20% review sample (seed 20261011, file `review_sample.txt`)

q001, q003, q004, q018, q022, q023, q031, q032, q045, q054, q059, q060
(3 fact, 4 compare, 1 multi-hop, 4 time-bound)

## What to check per sampled query

1. **Answerable**: does the corpus actually contain the answer?
2. **required_facts**: are they the facts a good answer must contain — neither
   too loose ("66%") nor impossible to state naturally?
3. **Relevant articles**: is `relevant_article_ids` right (missing any?
   extras?)?
4. **Type label**: fact / compare / multi-hop / time-bound correct?

## After approval

1. Pools are built (union of top-10 over all 12 ablation configs + these
   articles' chunks), shuffled and blinded, graded 2/1/0.
2. `status` flips to `frozen` in a commit; the first scored run happens after
   that commit.
