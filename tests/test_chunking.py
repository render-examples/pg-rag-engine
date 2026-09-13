from app.chunking import ConsecutiveUnitChunker
from app.config import ChunkingConfig
from app.models import ContentUnit, SourceLocator


def unit(sequence: int, words: int) -> ContentUnit:
    return ContentUnit(
        sequence=sequence,
        text=" ".join([f"word{sequence}"] * words),
        author=f"Author {sequence % 2}",
        locator=SourceLocator({"unit": sequence}),
    )


def test_chunker_preserves_ranges_authors_and_overlap():
    chunker = ConsecutiveUnitChunker(
        ChunkingConfig(
            strategy="consecutive_units",
            target_tokens=100,
            overlap_tokens=25,
            minimum_tokens=20,
        )
    )
    chunks = chunker.chunk([unit(index, 30) for index in range(1, 7)])
    assert len(chunks) >= 3
    assert chunks[0].start_unit_sequence == 1
    assert chunks[-1].end_unit_sequence == 6
    assert chunks[1].start_unit_sequence <= chunks[0].end_unit_sequence
    assert "Author" in chunks[0].text
    assert chunks[0].locator.value["start"] == {"unit": 1}
    assert all(chunk.content_hash for chunk in chunks)
