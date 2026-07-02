from __future__ import annotations

import hashlib
from dataclasses import dataclass

import tiktoken

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64

_enc = tiktoken.get_encoding("cl100k_base")


@dataclass
class Chunk:
    text: str
    source_id: str
    chunk_index: int
    point_id: str


def _make_point_id(source_id: str, chunk_index: int) -> str:
    raw = f"{source_id}::{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def chunk_text(
    text: str,
    source_id: str,
    *,
    size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[Chunk]:
    tokens = _enc.encode(text)
    chunks: list[Chunk] = []
    start = 0
    idx = 0
    while start < len(tokens):
        end = min(start + size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text_str = _enc.decode(chunk_tokens)
        point_id = _make_point_id(source_id, idx)
        chunks.append(
            Chunk(
                text=chunk_text_str,
                source_id=source_id,
                chunk_index=idx,
                point_id=point_id,
            )
        )
        if end == len(tokens):
            break
        start += size - overlap
        idx += 1
    return chunks
