from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextChunk:
    content: str
    char_start: int
    char_end: int


BREAK_CHARACTERS = "\n。！？；.!?;"


def split_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[TextChunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于 0 且小于 chunk_size")
    if not text:
        return []

    chunks: list[TextChunk] = []
    start = 0
    text_length = len(text)
    minimum_break = max(chunk_size // 2, 1)

    while start < text_length:
        target_end = min(start + chunk_size, text_length)
        end = target_end

        if target_end < text_length:
            search_start = min(start + minimum_break, target_end)
            candidates = [text.rfind(char, search_start, target_end + 1) for char in BREAK_CHARACTERS]
            best_break = max(candidates, default=-1)
            if best_break >= search_start:
                end = best_break + 1

        content = text[start:end].strip()
        if content:
            leading_whitespace = len(text[start:end]) - len(text[start:end].lstrip())
            trailing_whitespace = len(text[start:end]) - len(text[start:end].rstrip())
            actual_start = start + leading_whitespace
            actual_end = end - trailing_whitespace
            chunks.append(TextChunk(content=content, char_start=actual_start, char_end=actual_end))

        if end >= text_length:
            break
        next_start = end - overlap
        if next_start <= start:
            next_start = end
        start = next_start

    return chunks
