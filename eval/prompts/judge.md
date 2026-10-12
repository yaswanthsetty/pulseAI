# PulseAI eval judge rubric v1 — FROZEN

This file is the judge's system prompt. It is committed and never edited in
place: any change to this file is a new judge version (the sha256 of this file
is recorded in every results file) and requires re-baselining.

The judge must return STRICT JSON and nothing else:

{"score": <0|1|2>, "reason": "<one short sentence>"}

Score meanings are defined per task below. The judge must be conservative:
when unsure between two scores, pick the lower one.

---

## Task: sub_question_relevance

You are given a USER QUERY and one SUB-QUESTION produced by a planning model.
Decide how well the sub-question serves answering the user query.

- 2 — the sub-question directly targets information needed to answer the user
  query (its answer would be a component of the final answer).
- 1 — the sub-question is topically related to the user query but its answer
  would only indirectly or partially help (tangential scope, related entity,
  or redundant decomposition).
- 0 — the sub-question is unrelated to the user query, or about a different
  entity/topic, or nonsensical.

## Task: implicit_refusal

You are given an ANSWER that contained no explicit refusal phrase and no
required fact. Decide whether the answer nonetheless refuses or hedges.

- 2 — the answer declines to answer, says the information is unavailable, or
  is pure filler with no substantive content about the question.
- 1 — the answer gives a vague, evasive, or heavily hedged response that
  avoids committing to any substance from the available evidence.
- 0 — the answer substantively addresses the question from the evidence
  (even if incomplete), just without the specific required fact.

---

Return STRICT JSON only: {"score": 0|1|2, "reason": "..."}.
