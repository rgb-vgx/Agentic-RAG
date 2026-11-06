"""Prompt registry with versioning (Section 9.7, 14.1).

Every prompt carries an explicit version recorded in the agent run's
versioning snapshot. Changing prompt text requires bumping the version —
results must be reproducible from their recorded versions.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    template: str


# ---------------------------------------------------------------------------
# Conversation context resolution (Section 8.2)
# ---------------------------------------------------------------------------

RESOLVE_QUERY = Prompt(
    name="resolve_query",
    version="1.0",
    template="""\
You resolve a user's new message against the conversation history.

Given the recent history and the latest user message, produce JSON:
{
  "standalone_query": "<the message rewritten as a self-contained question>",
  "intent": "<short intent label>",
  "entities": ["<named entities>"],
  "constraints": {{"<key>": "<value>"}},
  "is_follow_up": <true|false>
}

Rules:
- Preserve the user's intent and every important condition.
- Resolve pronouns and references using the history.
- Keep constraints from earlier turns only when still relevant.
- If the message is a new question, set is_follow_up=false and keep
  standalone_query close to the original.

Conversation history:
{history}

Latest user message:
{query}
""",
)

# ---------------------------------------------------------------------------
# Query analysis (Section 6.3)
# ---------------------------------------------------------------------------

ANALYZE_QUERY = Prompt(
    name="analyze_query",
    version="1.0",
    template="""\
Classify this question for a document Q&A system.

Query: {query}

Respond with JSON:
{{
  "query_type": "simple_fact|conditional|multi_hop|comparison|summarization|follow_up|no_answer",
  "complexity": "low|medium|high",
  "entities": ["..."],
  "required_scope": ["..."],
  "time_constraints": ["..."],
  "evidence_granularity": "chunk|section|document",
  "requires_history": true|false
}}

Complexity guide:
- low: one direct fact, answerable from a single passage → fast path
- medium: needs several passages but no reasoning chain → standard path
- high: comparison, multi-hop, conditional or summarization → agentic path
""",
)

# ---------------------------------------------------------------------------
# Retrieval planning (Section 7.1)
# ---------------------------------------------------------------------------

PLAN_RETRIEVAL = Prompt(
    name="plan_retrieval",
    version="1.0",
    template="""\
Plan the retrieval steps needed to answer this question.

Query: {query}
Analysis: {analysis}

Respond with JSON:
{{
  "query_type": "...",
  "complexity": "...",
  "retrieval_tasks": [
    {{"task_id": "t1", "query": "<sub-query>", "retrieval_strategy": "hybrid"}}
  ],
  "requires_reranking": true|false,
  "requires_context_expansion": true|false
}}

Rules:
- At most {max_tasks} tasks.
- Each task query must be self-contained.
- Use a single hybrid task when one passage suffices.
""",
)

# ---------------------------------------------------------------------------
# Evidence evaluation (Section 7.3)
# ---------------------------------------------------------------------------

EVALUATE_EVIDENCE = Prompt(
    name="evaluate_evidence",
    version="1.0",
    template="""\
Evaluate whether the retrieved evidence can answer the query.

Query: {query}

Evidence:
{evidence}

Respond with JSON:
{{
  "status": "SUFFICIENT|PARTIALLY_SUFFICIENT|INSUFFICIENT|CONFLICTING",
  "missing_information": ["<what's missing, if anything>"],
  "contradictions": ["<conflicting statements, if any>"],
  "rationale": "<one sentence>"
}}

Rules:
- Only judge from the provided evidence, never from prior knowledge.
- CONFLICTING when two passages contradict each other on the answer.
- SUFFICIENT only when the evidence fully answers the query.
""",
)

# ---------------------------------------------------------------------------
# Answer synthesis (Section 7.4)
# ---------------------------------------------------------------------------

SYNTHESIZE_ANSWER = Prompt(
    name="synthesize_answer",
    version="1.0",
    template="""\
Answer the user's question using ONLY the evidence below.

Query: {query}

Evidence:
{evidence}

Rules:
- Cite every factual claim with its reference id like [R1].
- Use only reference ids that appear in the evidence.
- If the evidence is insufficient, say what is missing instead of guessing.
- Answer in the same language as the query.

Answer:
""",
)

# ---------------------------------------------------------------------------
# Citation validation (Section 7.5)
# ---------------------------------------------------------------------------

VALIDATE_CITATIONS = Prompt(
    name="validate_citations",
    version="1.0",
    template="""\
Extract every citation marker of the form [R<n>] from the answer below.
Return JSON: {{"citations": ["R1", "R2", ...]}}
Answer:
{answer}
""",
)
