from app.core.citations import build_citations, extract_citation_indices
from app.core.prompt import build_prompt, build_refusal_prompt

MOCK_CHUNKS = [
    {"text": "Python is a programming language.", "title": "Python", "source_id": "1"},
    {"text": "It was created by Guido van Rossum.", "title": "Python", "source_id": "1"},
]


def test_build_prompt_includes_citations():
    prompt = build_prompt("What is Python?", MOCK_CHUNKS)
    assert "[1]" in prompt
    assert "[2]" in prompt
    assert "Python is a programming language" in prompt
    assert "What is Python?" in prompt


def test_build_refusal_prompt():
    prompt = build_refusal_prompt("What is Python?")
    assert "I don't know" in prompt or "none" in prompt.lower()


def test_extract_citation_indices():
    answer = "Python [1] was created by Guido [2]. See also [1] for details."
    indices = extract_citation_indices(answer)
    assert indices == [1, 2]


def test_extract_no_citations():
    answer = "I don't know based on the provided context."
    assert extract_citation_indices(answer) == []


def test_build_citations():
    cited = build_citations(MOCK_CHUNKS, [1, 2])
    assert len(cited) == 2
    assert cited[0]["title"] == "Python"
    assert cited[0]["index"] == 1


def test_build_citations_out_of_range():
    cited = build_citations(MOCK_CHUNKS, [1, 99])
    assert len(cited) == 1
