from __future__ import annotations

SYSTEM_PROMPT = """\
You are a helpful assistant that answers questions using ONLY the provided context.
Each context chunk is labeled with a citation number: [1], [2], etc.

Follow these rules exactly:
1. Use only information found in the context. Never add outside facts.
2. Cite inline with [n] markers matching the chunk numbers. EVERY sentence that
   uses the context must include at least one [n] citation.
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
