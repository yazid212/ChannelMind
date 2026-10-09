from unittest.mock import Mock

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from src.rag import YouTubeRAG
from src.utils import format_timestamp


def _rag_with_test_doubles():
    rag = YouTubeRAG.__new__(YouTubeRAG)
    rag.embedder = Mock()
    rag.chain = Mock()
    rag.general_chain = Mock()
    rag.chain.invoke.return_value = AIMessage(
        content="According to Demo Video at 01:05, this is a grounded answer.",
        usage_metadata={
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
        },
    )
    rag.format_docs = lambda docs: "\n".join(doc.page_content for doc in docs)
    return rag


def test_ask_retrieves_once_and_reuses_documents_for_sources():
    rag = _rag_with_test_doubles()
    document = Document(
        page_content="Relevant transcript chunk",
        metadata={
            "video_id": "abc123",
            "title": "Demo Video",
            "start_time": 65,
            "url": "https://youtube.com/watch?v=abc123&t=65s",
        },
    )
    rag.embedder.search.return_value = [document]

    result = rag.ask("What is explained?")

    rag.embedder.search.assert_called_once()
    prompt_input = rag.chain.invoke.call_args.args[0]
    assert prompt_input["context"] == "Relevant transcript chunk"
    assert "grounded answer" in result["answer"]
    assert result["sources"][0]["timestamp"] == "01:05"
    assert result["sources"][0]["quote"] == "Relevant transcript chunk"
    assert result["sources"][0]["video_id"] == "abc123"
    assert result["sources"][0]["start_time"] == 65
    assert result["usage"]["total_tokens"] == 150


def test_source_verification_tolerates_title_punctuation_changes():
    answer = "According to [L-3 | Python variables and data types] at 10:32, variables store values."
    assert YouTubeRAG._source_mentioned(answer, "L-3 | Python variables and data types |", "10:32")
    assert not YouTubeRAG._source_mentioned(answer, "L-4 | Type casting |", "10:32")
    shortened = 'According to "Python Variables Made Easy | Python Programming Tutorial" at 00:15'
    assert YouTubeRAG._source_mentioned(
        shortened,
        "Entire Python 3 For Absolute Beginner - Python Variables Made Easy | Python Programming Tutorial",
        "00:15",
    )
    assert YouTubeRAG._source_mentioned(
        "According to [Python Variables Made Easy] at 00:15",
        "Entire Python 3 For Absolute Beginner - Python Variables Made Easy | Python Programming Tutorial",
        "00:15",
    )


def test_source_markers_render_stored_title_and_timestamp():
    rag = _rag_with_test_doubles()
    rag.embedder.search.return_value = [
        Document(page_content="The list is sorted in place", metadata={
            "video_id": "sorting", "title": "Sorting Lists", "start_time": 3,
            "url": "https://youtube.com/watch?v=sorting&t=3s"}),
        Document(page_content="Use a key function that returns each price", metadata={
            "video_id": "sorting", "title": "Sorting Lists", "start_time": 156,
            "url": "https://youtube.com/watch?v=sorting&t=156s"}),
    ]
    rag.chain.invoke.return_value = AIMessage(
        content="Sorting changes the list [S1]. Price is returned by the key function [S2]."
    )

    result = rag.ask("How does sorting work by price?")

    assert "[S1]" not in result["answer"]
    assert "[Sorting Lists at 00:03]" in result["answer"]
    assert "[Sorting Lists at 02:36]" in result["answer"]
    assert [source["timestamp"] for source in result["sources"]] == ["00:03", "02:36"]


def test_fractional_export_timestamps_format_as_whole_seconds():
    assert format_timestamp(65.7) == "01:05"


def test_missing_evidence_stays_within_imported_videos():
    rag = _rag_with_test_doubles()
    rag.embedder.search.return_value = []
    rag.chain.invoke.return_value = AIMessage(
        content=YouTubeRAG.INSUFFICIENT_ANSWER,
        usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
    )
    result = rag.ask("Why does a computer use binary?")

    assert result["answer"] == YouTubeRAG.INSUFFICIENT_ANSWER
    assert result["sources"] == []
    assert result["usage"] == {}
    rag.chain.invoke.assert_not_called()


def test_model_insufficient_answer_does_not_invoke_general_knowledge():
    rag = _rag_with_test_doubles()
    rag.embedder.search.return_value = [Document(
        page_content="A lesson about Python errors",
        metadata={"video_id": "abc123", "title": "Demo Video", "start_time": 0,
                  "url": "https://youtube.com/watch?v=abc123&t=0s"},
    )]
    rag.chain.invoke.return_value = AIMessage(content=YouTubeRAG.INSUFFICIENT_ANSWER)
    rag.llm = Mock()

    result = rag.ask("Does this explain JavaScript promises?")

    assert result["answer"] == YouTubeRAG.INSUFFICIENT_ANSWER
    assert result["sources"] == []
    rag.llm.invoke.assert_not_called()


def test_general_knowledge_mode_is_labeled_and_never_uses_video_evidence():
    rag = _rag_with_test_doubles()
    rag.general_chain.invoke.return_value = AIMessage(
        content="A general explanation.",
        usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
    )

    result = rag.ask("Explain variables", mode="general")

    assert result["answer"].startswith(YouTubeRAG.GENERAL_KNOWLEDGE_LABEL)
    assert result["sources"] == []
    assert result["retrieved_chunks"] == 0
    assert result["usage"]["total_tokens"] == 25
    rag.embedder.search.assert_not_called()
    rag.chain.invoke.assert_not_called()


def test_unknown_answer_mode_is_rejected_before_retrieval():
    rag = _rag_with_test_doubles()
    with pytest.raises(ValueError, match="Unsupported answer mode"):
        rag.ask("Explain variables", mode="invalid")
    rag.embedder.search.assert_not_called()


def test_broad_question_requests_diversified_retrieval():
    rag = _rag_with_test_doubles()
    rag.embedder.search.return_value = []

    rag.ask("What are the main lessons across these videos?")

    assert rag.embedder.search.call_args.kwargs["diversify"] is True


def test_history_excludes_current_question_and_keeps_two_pairs():
    history = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
        {"role": "user", "content": "newer question"},
        {"role": "assistant", "content": "newer answer"},
        {"role": "user", "content": "current question"},
    ]

    formatted = YouTubeRAG._format_history(history, "current question")

    assert "current question" not in formatted
    assert "old question" not in formatted
    assert "recent question" in formatted
    assert "newer answer" in formatted
