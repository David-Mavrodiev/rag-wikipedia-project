from __future__ import annotations

SYSTEM_PROMPT = """\
You are a helpful assistant that answers questions based only on the provided context.
Each context chunk is labeled with a citation number [1], [2], etc.
You MUST cite your sources inline using [number] format.
If the context does not contain relevant information, respond with exactly:
"I don't know based on the provided context."
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
