"""Source-neutral citation-preserving content chunking."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from app.config import ChunkingConfig
from app.models import Chunk, ContentUnit, SourceLocator


def estimate_tokens(text: str) -> int:
    words = len(re.findall(r"\S+", text))
    return max(1, round(words / 0.75))


class ConsecutiveUnitChunker:
    def __init__(self, config: ChunkingConfig) -> None:
        self.config = config

    @staticmethod
    def _line(unit: ContentUnit) -> str:
        text = unit.text.strip()
        return f"{unit.author}: {text}" if unit.author else text

    def chunk(self, units: Sequence[ContentUnit]) -> list[Chunk]:
        ordered = sorted((unit for unit in units if unit.text.strip()), key=lambda u: u.sequence)
        if not ordered:
            return []

        groups: list[list[ContentUnit]] = []
        current: list[ContentUnit] = []
        current_tokens = 0
        for unit in ordered:
            unit_tokens = estimate_tokens(self._line(unit))
            if (
                current
                and current_tokens >= self.config.minimum_tokens
                and current_tokens + unit_tokens > self.config.target_tokens
            ):
                groups.append(current)
                overlap: list[ContentUnit] = []
                overlap_tokens = 0
                for previous in reversed(current):
                    candidate_tokens = estimate_tokens(self._line(previous))
                    if overlap and overlap_tokens + candidate_tokens > self.config.overlap_tokens:
                        break
                    if self.config.overlap_tokens == 0:
                        break
                    overlap.insert(0, previous)
                    overlap_tokens += candidate_tokens
                current = overlap
                current_tokens = overlap_tokens
            current.append(unit)
            current_tokens += unit_tokens
        if current:
            groups.append(current)

        chunks: list[Chunk] = []
        for sequence, group in enumerate(groups, start=1):
            text = "\n".join(self._line(unit) for unit in group)
            locator = SourceLocator(
                {
                    "start": group[0].locator.value,
                    "end": group[-1].locator.value,
                }
            )
            chunks.append(
                Chunk(
                    sequence=sequence,
                    text=text,
                    token_estimate=estimate_tokens(text),
                    start_unit_sequence=group[0].sequence,
                    end_unit_sequence=group[-1].sequence,
                    locator=locator,
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                )
            )
        return chunks
