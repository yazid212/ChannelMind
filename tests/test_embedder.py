from unittest.mock import Mock, patch

from langchain_core.documents import Document
from qdrant_client import models

from src.embedder import Embedder


def test_embedder_initialization():
    with (
        patch("src.embedder.HuggingFaceEmbeddings") as embeddings_class,
        patch("src.embedder.Chroma") as chroma_class,
        patch("src.embedder.os.makedirs"),
    ):
        embedder = Embedder()

    embeddings_class.assert_called_once()
    chroma_class.assert_called_once()
    assert embedder.vectorstore is chroma_class.return_value


def test_workspaces_use_distinct_local_collections():
    with (
        patch("src.embedder.HuggingFaceEmbeddings"),
        patch("src.embedder.Chroma") as chroma_class,
        patch("src.embedder.os.makedirs"),
    ):
        Embedder(workspace_id="workspace-a")
        Embedder(workspace_id="workspace-b")

    first = chroma_class.call_args_list[0].kwargs["collection_name"]
    second = chroma_class.call_args_list[1].kwargs["collection_name"]
    assert first != second
    assert "workspace-a" not in first


def test_qdrant_queries_always_include_workspace_filter():
    embedder = Embedder.__new__(Embedder)
    embedder._qdrant_models = models
    embedder.workspace_key = "hashed-tenant"

    query_filter = embedder._workspace_filter()

    assert len(query_filter.must) == 1
    assert query_filter.must[0].key == "workspace_id"
    assert query_filter.must[0].match.value == "hashed-tenant"

    typed_filter = embedder._workspace_filter("catalog")
    assert len(typed_filter.must) == 2
    assert typed_filter.must[1].key == "document_type"
    assert typed_filter.must[1].match.value == "catalog"
    video_filter = embedder._workspace_filter("transcript", "video-1")
    assert len(video_filter.must) == 3
    assert video_filter.must[2].key == "video_id"


def test_document_creation():
    # Document creation is pure and does not require loading the real model.
    embedder = Embedder.__new__(Embedder)
    transcripts = [{
        "video_id": "test123",
        "title": "Test Video",
        "transcript": "This is a test transcript. " * 100,  # Make it long enough to chunk
        "segments": [
            {"text": "This is a test transcript.", "start": 0, "duration": 5}
        ]
    }]

    docs = embedder._create_documents(transcripts)
    assert len(docs) > 0
    assert docs[0].metadata["video_id"] == "test123"
    assert docs[0].metadata["document_type"] == "transcript"
    assert "url" in docs[0].metadata


def test_ingest_replaces_existing_collection_before_adding_documents():
    embedder = Embedder.__new__(Embedder)
    embedder.vectorstore = Mock()
    embedder.clear = Mock()
    documents = [Document(page_content="chunk", metadata={})]
    embedder._create_documents = Mock(return_value=documents)

    embedder.ingest([{"video_id": "video-1"}])

    embedder.clear.assert_called_once_with("transcript")
    embedder.vectorstore.add_documents.assert_called_once_with(documents)


def test_diversified_search_prefers_different_videos():
    embedder = Embedder.__new__(Embedder)
    embedder.vectorstore = Mock()
    embedder.vectorstore.similarity_search.return_value = [
        Document(page_content="first", metadata={"video_id": "video-a"}),
        Document(page_content="second", metadata={"video_id": "video-a"}),
        Document(page_content="third", metadata={"video_id": "video-b"}),
        Document(page_content="fourth", metadata={"video_id": "video-c"}),
    ]

    results = embedder.search("across the channel", k=3, diversify=True)

    assert [doc.metadata["video_id"] for doc in results] == [
        "video-a",
        "video-b",
        "video-c",
    ]
    embedder.vectorstore.similarity_search.assert_any_call(
        "across the channel",
        k=100,
        filter={"document_type": "transcript"},
    )


def test_search_routes_named_lesson_to_its_transcript_when_global_similarity_misses_it():
    embedder = Embedder.__new__(Embedder)
    embedder.vectorstore = Mock()
    def search(_query, *, k, filter):
        if filter == {"document_type": "catalog"}:
            return [
                Document(page_content="", metadata={"video_id": "intro", "title": "Introduction to Python"}),
                Document(page_content="", metadata={"video_id": "variables", "title": "Python variables and data types"}),
                Document(page_content="", metadata={"video_id": "setup", "title": "Installing Python"}),
            ]
        if filter == {"$and": [{"document_type": "transcript"}, {"video_id": "variables"}]}:
            return [Document(page_content="Variables are named values", metadata={"video_id": "variables", "start_time": 10})]
        return [Document(page_content="unrelated", metadata={"video_id": "intro", "start_time": 0})]
    embedder.vectorstore.similarity_search.side_effect = search

    docs = embedder.search("What are variables and data types?", k=2)

    assert docs[0].metadata["video_id"] == "variables"
    assert docs[1].metadata["video_id"] == "intro"


def test_catalog_documents_are_separate_from_transcripts():
    embedder = Embedder.__new__(Embedder)
    embedder.vectorstore = Mock()
    embedder.clear = Mock()

    embedder.ingest_catalog([{
        "video_id": "older-video",
        "title": "An older topic",
        "description": "A detailed explanation",
        "published_at": "2022-01-01T00:00:00Z",
    }])

    embedder.clear.assert_called_once_with("catalog")
    document = embedder.vectorstore.add_documents.call_args.args[0][0]
    assert document.metadata["document_type"] == "catalog"
    assert document.metadata["video_id"] == "older-video"
    assert "detailed explanation" in document.page_content


def test_indexed_video_ids_reads_only_transcript_metadata():
    embedder = Embedder.__new__(Embedder)
    embedder.vectorstore = Mock()
    embedder.vectorstore.get.return_value = {
        "metadatas": [{"video_id": "a"}, {"video_id": "a"}, {"video_id": "b"}]
    }

    assert embedder.indexed_video_ids() == {"a", "b"}
    embedder.vectorstore.get.assert_called_once_with(
        where={"document_type": "transcript"},
        include=["metadatas"],
    )
