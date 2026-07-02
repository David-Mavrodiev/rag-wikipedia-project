from app.core.chunking import chunk_text


def test_chunk_single_sentence():
    text = "Hello world."
    chunks = chunk_text(text, "art_001")
    assert len(chunks) == 1
    assert chunks[0].text == "Hello world."
    assert chunks[0].source_id == "art_001"
    assert chunks[0].chunk_index == 0


def test_chunk_deterministic_ids():
    text = "A " * 600
    chunks1 = chunk_text(text, "art_001")
    chunks2 = chunk_text(text, "art_001")
    assert [chunk.point_id for chunk in chunks1] == [chunk.point_id for chunk in chunks2]


def test_chunk_overlap_produces_multiple():
    text = "word " * 700
    chunks = chunk_text(text, "art_002")
    assert len(chunks) >= 2


def test_chunk_different_sources_different_ids():
    text = "word " * 600
    chunks_a = chunk_text(text, "art_a")
    chunks_b = chunk_text(text, "art_b")
    assert chunks_a[0].point_id != chunks_b[0].point_id


def test_clean_normalize():
    from pipeline.tasks import clean_text

    raw = "==Section==\n[[Link|Text]] {{template}} <b>bold</b> content"
    cleaned = clean_text(raw)
    assert "{{" not in cleaned
    assert "[[" not in cleaned
    assert "<b>" not in cleaned
    assert "content" in cleaned
