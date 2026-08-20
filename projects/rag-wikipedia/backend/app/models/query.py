from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class Citation(BaseModel):
    index: int
    title: str
    source_id: str
    excerpt: str


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    # Explicit refusal signal so clients never have to infer it from citation
    # count (a grounded answer can legitimately have zero citations).
    refused: bool = False
