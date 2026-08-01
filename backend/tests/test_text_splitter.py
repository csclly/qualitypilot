import pytest

from app.services.text_splitter import split_text


def test_splits_at_target_size_with_overlap() -> None:
    text = "甲" * 1900

    chunks = split_text(text, chunk_size=800, overlap=100)

    assert [len(chunk.content) for chunk in chunks] == [800, 800, 500]
    assert chunks[0].char_start == 0
    assert chunks[1].char_start == 700
    assert chunks[0].content[-100:] == chunks[1].content[:100]
    assert chunks[-1].char_end == len(text)


def test_prefers_sentence_boundary_near_target() -> None:
    text = "甲" * 700 + "。" + "乙" * 300

    chunks = split_text(text, chunk_size=800, overlap=100)

    assert chunks[0].content.endswith("。")
    assert chunks[0].char_end == 701
    assert chunks[1].char_start == 601


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (100, -1), (100, 100), (100, 101)],
)
def test_rejects_invalid_configuration(chunk_size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        split_text("内容", chunk_size=chunk_size, overlap=overlap)
