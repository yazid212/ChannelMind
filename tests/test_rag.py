from unittest.mock import Mock

from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from src.rag import YouTubeRAG
from src.utils import format_timestamp


def _rag_with_test_doubles():
    rag = YouTubeRAG.__new__(YouTubeRAG)
    rag.embedder = Mock()
    rag.chain = Mock()
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


def test_fractional_export_timestamps_format_as_whole_seconds():
    assert format_timestamp(65.7) == "01:05"


def test_missing_evidence_gets_labeled_general_knowledge_answer_without_sources():
    rag = _rag_with_test_doubles()
    rag.embedder.search.return_value = []
    rag.chain.invoke.return_value = AIMessage(
        content=YouTubeRAG.INSUFFICIENT_ANSWER,
        usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
    )
    rag.llm = Mock()
    rag.llm.invoke.return_value = AIMessage(
        content="Computers process input into output by following instructions in binary.",
        usage_metadata={"input_tokens": 20, "output_tokens": 12, "total_tokens": 32},
    )

    result = rag.ask("Why does a computer use binary?")

    assert result["answer"].startswith("General knowledge (not from the imported videos):")
    assert "process input into output" in result["answer"]
    assert result["sources"] == []
    assert result["usage"]["total_tokens"] == 182
    rag.llm.invoke.assert_called_once()


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
