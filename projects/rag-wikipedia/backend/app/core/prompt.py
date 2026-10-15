from __future__ import annotations

# Rule 2 used to read "Cite inline with [n] markers". llama3.2:3b sometimes
# copied the placeholder literally - measured 2026-09-21: two of six answers in a
# live probe ended in "[n]", rendered with zero sources and not refused. The
# prompt now shows only real numbers; eval/grounding.py counts any "[n]" that
# still appears as placeholder_citation_rate, so the effect is measurable.
SYSTEM_PROMPT = """\
You are a helpful assistant that answers questions using ONLY the provided context.
Each context chunk is labeled with a citation number: [1], [2], etc.

Follow these rules exactly:
1. Use only information found in the context. Never add outside facts.
2. Cite inline with the number of the chunk you used, in square brackets, such
   as [1] or [2]. EVERY sentence that uses the context must include at least one
   citation, and a citation is always a chunk number, never a letter.
3. If the context does not contain the answer, reply with EXACTLY this line and
   nothing else:
I don't know based on the provided context.

Example of a correctly cited answer:
Question: Where is the Eiffel Tower and when was it built?
Answer: The Eiffel Tower is in Paris [1]. It was completed in 1889 [2].
"""


def build_prompt(query: str, chunks: list[dict]) -> str:
    context_parts = []
    for index, chunk in enumerate(chunks, 1):
        context_parts.append(f"[{index}] (Source: {chunk['title']})\n{chunk['text']}")
    context = "\n\n".join(context_parts)
    return f"""{SYSTEM_PROMPT}

Context:
{context}

Question: {query}

Answer:"""


def build_refusal_prompt(query: str) -> str:
    return f"""{SYSTEM_PROMPT}

Context: (none)

Question: {query}

Answer:"""
