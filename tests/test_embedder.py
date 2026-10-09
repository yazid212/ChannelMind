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


def test_qdrant_existing_collection_adds_video_id_index():
    with (
        patch("src.embedder.HuggingFaceEmbeddings"),
        patch("src.embedder.QDRANT_URL", "https://qdrant.example"),
        patch("qdrant_client.QdrantClient") as client_class,
    ):
        client = client_class.return_value
        client.collection_exists.return_value = True
        client.get_collection.return_value.payload_schema = {"workspace_id": models.PayloadSchemaType.KEYWORD}
        Embedder(workspace_id="workspace", backend="qdrant")

    client.create_payload_index.assert_called_once_with(
        collection_name="youtube_transcripts",
        field_name="video_id",
        field_schema=models.PayloadSchemaType.KEYWORD,
        wait=True,
    )


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


def test_document_creation_normalizes_fractional_export_timestamps():
    document = Embedder._document(
        {"video_id": "abcdefghijk", "title": "Exported lesson"}, ["spoken words"], 65.7,
    )
    assert document.metadata["start_time"] == 65
    assert document.metadata["url"].endswith("&t=65s")


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


def test_named_video_gets_enough_chunks_for_a_multi_part_question():
    embedder = Embedder.__new__(Embedder)
    embedder.vectorstore = Mock()
    relevant = [
        Document(page_content=text, metadata={"video_id": "sorting", "start_time": start})
        for start, text in [(3, "sort changes the list"), (78, "sorted makes a new list"),
                            (156, "sort by price using a key"), (233, "key function example")]
    ]

    def search(_query, *, k, filter):
        if filter == {"document_type": "catalog"}:
            return [
                Document(page_content="", metadata={"video_id": "sorting", "title": "How to Sort Lists in Python"}),
                Document(page_content="", metadata={"video_id": "sets", "title": "What are Sets in Python?"}),
            ]
        if filter == {"$and": [{"document_type": "transcript"}, {"video_id": "sorting"}]}:
            return relevant[:k]
        return [Document(page_content="unrelated", metadata={"video_id": "sets", "start_time": 0})]

    embedder.vectorstore.similarity_search.side_effect = search
    question = "In How to Sort Lists in Python, how do sort and sorted differ, and how are items sorted by price?"

    assert embedder._title_video_ids(question, 4) == ["sorting"]
    docs = embedder.search(question, k=4)

    assert [doc.metadata["start_time"] for doc in docs] == [3, 78, 156, 233]
    embedder.vectorstore.similarity_search.assert_any_call(
        question, k=4,
        filter={"$and": [{"document_type": "transcript"}, {"video_id": "sorting"}]},
    )


def test_named_title_phrase_beats_shared_topic_words():
    embedder = Embedder.__new__(Embedder)
    embedder.search_catalog = Mock(return_value=[
        Document(page_content="", metadata={"video_id": "map", "title": "Map and Filter Functions in Python"}),
        Document(page_content="", metadata={"video_id": "functions", "title": "Python Functions Tutorial"}),
        Document(page_content="", metadata={"video_id": "stacks", "title": "Python Stacks"}),
        Document(page_content="", metadata={"video_id": "sorting", "title": "How to Sort Lists"}),
    ])

    assert embedder._title_video_ids(
        "Why does the Python Functions tutorial split programs, and what is a parameter?", 4
    ) == ["functions"]


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


def test_transcript_reader_filters_one_video_and_orders_all_chroma_chunks():
    embedder = Embedder.__new__(Embedder)
    embedder.backend = "chroma"
    embedder.vectorstore = Mock()
    embedder.vectorstore.get.return_value = {
        "documents": ["Later", "Earlier"],
        "metadatas": [
            {"video_id": "abcdefghijk", "start_time": 70, "title": "Demo"},
            {"video_id": "abcdefghijk", "start_time": 5, "title": "Demo"},
        ],
    }

    documents = embedder.transcript_chunks("abcdefghijk")

    assert [doc.page_content for doc in documents] == ["Earlier", "Later"]
    embedder.vectorstore.get.assert_called_once_with(
        where={"$and": [{"document_type": "transcript"}, {"video_id": "abcdefghijk"}]},
        include=["documents", "metadatas"],
    )


def test_transcript_reader_qdrant_scroll_keeps_workspace_and_video_filter():
    embedder = Embedder.__new__(Embedder)
    embedder.backend = "qdrant"
    embedder.workspace_key = "tenant-a"
    embedder.collection_name = "shared"
    embedder._qdrant_models = models
    embedder.vectorstore = Mock()
    embedder.vectorstore.scroll.side_effect = [
        ([Mock(payload={"page_content": "Later", "title": "Demo", "start_time": 70})], "next"),
        ([Mock(payload={"page_content": "Earlier", "title": "Demo", "start_time": 5})], None),
    ]

    documents = embedder.transcript_chunks("abcdefghijk")

    assert [doc.page_content for doc in documents] == ["Earlier", "Later"]
    first = embedder.vectorstore.scroll.call_args_list[0].kwargs
    assert [(condition.key, condition.match.value) for condition in first["scroll_filter"].must] == [
        ("workspace_id", "tenant-a"), ("document_type", "transcript"), ("video_id", "abcdefghijk"),
    ]
    assert embedder.vectorstore.scroll.call_args_list[1].kwargs["offset"] == "next"
