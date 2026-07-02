from __future__ import annotations

import re


def extract_citation_indices(answer: str) -> list[int]:
    matches = re.findall(r"\[(\d+)\]", answer)
    return sorted({int(match) for match in matches})


def build_citations(chunks: list[dict], cited_indices: list[int]) -> list[dict]:
    result = []
    for idx in cited_indices:
        chunk_index = idx - 1
        if 0 <= chunk_index < len(chunks):
            chunk = chunks[chunk_index]
            text = chunk["text"]
            result.append(
                {
                    "index": idx,
                    "title": chunk.get("title", ""),
                    "source_id": chunk.get("source_id", ""),
                    "excerpt": text[:200] + "..." if len(text) > 200 else text,
                }
            )
    return result
