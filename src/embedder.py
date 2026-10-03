"""Transcript chunking and tenant-isolated vector storage."""

import hashlib
import os
import re
import uuid
from typing import Dict, List

from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

try:
    from langchain_chroma import Chroma
except ImportError:  # The production Qdrant image intentionally omits Chroma.
    Chroma = None

from config import (
    CHUNK_SIZE,
    DIVERSE_FETCH_K,
    EMBEDDING_DIMENSION,
    EMBEDDING_LOCAL_FILES_ONLY,
    EMBEDDING_MODEL,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_URL,
    VECTOR_BACKEND,
    VECTORSTORE_PATH,
)
from .utils import logger


def _workspace_key(workspace_id: str) -> str:
    """Return a stable, path-safe tenant key without exposing the raw id."""
    return hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:24]


class Embedder:
    """Store and retrieve transcript chunks for exactly one workspace.

    Chroma uses one local collection per workspace. Qdrant uses a shared
    collection with a mandatory workspace payload filter, which scales much
    better for many short-lived public demo sessions.
    """

    def __init__(
        self,
        collection_name: str = "youtube_transcripts",
        workspace_id: str = "streamlit",
        backend: str | None = None,
        expires_at: int | None = None,
    ):
        if not workspace_id or len(workspace_id) > 128:
            raise ValueError("Invalid workspace identifier")

        self.workspace_id = workspace_id
        self.workspace_key = _workspace_key(workspace_id)
        self.expires_at = expires_at
        self.backend = (backend or VECTOR_BACKEND).lower()
        self.collection_name = collection_name
        self.embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"local_files_only": EMBEDDING_LOCAL_FILES_ONLY},
        )

        if self.backend == "qdrant":
            self._initialize_qdrant()
        elif self.backend == "chroma":
            self._initialize_chroma()
        else:
            raise ValueError(f"Unsupported vector backend: {self.backend}")

    def _initialize_chroma(self) -> None:
        if Chroma is None:
            raise RuntimeError("Install requirements.txt to use the local Chroma backend")
        self.collection_name = f"youtube_{self.workspace_key}"
        persist_dir = os.path.join(VECTORSTORE_PATH, self.collection_name)
        os.makedirs(persist_dir, exist_ok=True)
        self.vectorstore = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embeddings,
            persist_directory=persist_dir,
        )

    def _initialize_qdrant(self) -> None:
        if not QDRANT_URL:
            raise ValueError("QDRANT_URL is required when VECTOR_BACKEND=qdrant")

        try:
            from qdrant_client import QdrantClient, models
        except ImportError as error:  # pragma: no cover - deployment packaging
            raise RuntimeError("Install qdrant-client to use the Qdrant backend") from error

        self.collection_name = QDRANT_COLLECTION
        self._qdrant_models = models
        self.vectorstore = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

        if not self.vectorstore.collection_exists(self.collection_name):
            self.vectorstore.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=EMBEDDING_DIMENSION,
                    distance=models.Distance.COSINE,
                ),
            )
            self.vectorstore.create_payload_index(
                collection_name=self.collection_name,
                field_name="workspace_id",
                field_schema=models.KeywordIndexParams(
                    type=models.KeywordIndexType.KEYWORD,
                    is_tenant=True,
                ),
            )
            self.vectorstore.create_payload_index(
                collection_name=self.collection_name,
                field_name="expires_at",
                field_schema=models.PayloadSchemaType.INTEGER,
            )
            self.vectorstore.create_payload_index(
                collection_name=self.collection_name,
                field_name="document_type",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    def _create_documents(self, transcripts: List[Dict]) -> List[Document]:
        """Chunk transcripts while retaining source and timestamp metadata."""
        documents = []
        for video in transcripts:
            segments = video.get("segments", [])
            if not segments:
                continue

            chunk_texts = []
            chunk_start = None
            chunk_len = 0

            for segment in segments:
                text = segment.get("text", "").strip()
                if not text:
                    continue
                if chunk_start is None:
                    chunk_start = segment["start"]

                chunk_texts.append(text)
                chunk_len += len(text) + 1
                if chunk_len >= CHUNK_SIZE:
                    documents.append(
                        self._document(video, chunk_texts, chunk_start)
                    )
                    chunk_texts = []
                    chunk_start = None
                    chunk_len = 0

            if chunk_texts:
                documents.append(self._document(video, chunk_texts, chunk_start or 0))

        logger.info("Created %d chunks from %d videos", len(documents), len(transcripts))
        return documents

    @staticmethod
    def _document(video: Dict, texts: List[str], start_time: int) -> Document:
        return Document(
            page_content=" ".join(texts),
            metadata={
                "video_id": video["video_id"],
                "title": video["title"],
                "start_time": start_time,
                "url": (
                    f"https://youtube.com/watch?v={video['video_id']}"
                    f"&t={start_time}s"
                ),
                "document_type": "transcript",
            },
        )

    @staticmethod
    def _catalog_document(video: Dict) -> Document:
        title = video.get("title", "Untitled video")
        description = video.get("description", "")
        return Document(
            page_content=f"{title}\n{description}".strip(),
            metadata={
                "video_id": video["video_id"],
                "title": title,
                "description": description,
                "published_at": video.get("published_at", ""),
                "start_time": 0,
                "url": f"https://youtube.com/watch?v={video['video_id']}",
                "document_type": "catalog",
            },
        )

    def ingest(self, transcripts: List[Dict], replace_existing: bool = True) -> None:
        if not transcripts:
            logger.warning("No transcripts to ingest")
            return

        documents = self._create_documents(transcripts)
        if not documents:
            logger.warning("No transcript chunks to ingest")
            return

        if replace_existing:
            self.clear("transcript")

        if getattr(self, "backend", "chroma") == "qdrant":
            self._qdrant_add_documents(documents)
        else:
            self.vectorstore.add_documents(documents)
        logger.info("Ingested %d chunks into vector store", len(documents))

    def ingest_catalog(self, videos: List[Dict], replace_existing: bool = True) -> None:
        documents = [self._catalog_document(video) for video in videos]
        documents = [document for document in documents if document.page_content]
        if replace_existing:
            self.clear("catalog")
        if not documents:
            logger.warning("No channel catalog documents to ingest")
            return
        if getattr(self, "backend", "chroma") == "qdrant":
            self._qdrant_add_documents(documents)
        else:
            self.vectorstore.add_documents(documents)
        logger.info("Ingested %d catalog entries into vector store", len(documents))

    def _qdrant_add_documents(self, documents: List[Document]) -> None:
        models = self._qdrant_models
        vectors = self.embeddings.embed_documents([doc.page_content for doc in documents])
        points = []
        for index, (document, vector) in enumerate(zip(documents, vectors)):
            payload = {
                **document.metadata,
                "workspace_id": self.workspace_key,
                "page_content": document.page_content,
            }
            if self.expires_at is not None:
                payload["expires_at"] = self.expires_at
            stable_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{self.workspace_key}:{document.metadata['document_type']}:"
                f"{document.metadata['video_id']}:"
                f"{document.metadata['start_time']}:{index}",
            )
            points.append(
                models.PointStruct(id=str(stable_id), vector=vector, payload=payload)
            )
        self.vectorstore.upload_points(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )

    def _workspace_filter(self, document_type: str | None = None, video_id: str | None = None):
        models = self._qdrant_models
        conditions = [
            models.FieldCondition(
                key="workspace_id",
                match=models.MatchValue(value=self.workspace_key),
            )
        ]
        if document_type:
            conditions.append(
                models.FieldCondition(
                    key="document_type",
                    match=models.MatchValue(value=document_type),
                )
            )
        if video_id:
            conditions.append(
                models.FieldCondition(
                    key="video_id",
                    match=models.MatchValue(value=video_id),
                )
            )
        return models.Filter(
            must=conditions
        )

    def _search_type(self, query: str, k: int, document_type: str, video_id: str | None = None) -> List[Document]:
        if getattr(self, "backend", "chroma") == "qdrant":
            vector = self.embeddings.embed_query(query)
            response = self.vectorstore.query_points(
                collection_name=self.collection_name,
                query=vector,
                query_filter=self._workspace_filter(document_type, video_id),
                limit=k,
                with_payload=True,
            )
            return [
                Document(
                    page_content=point.payload["page_content"],
                    metadata={
                        key: value
                        for key, value in point.payload.items()
                        if key not in {"page_content", "workspace_id", "expires_at"}
                    },
                )
                for point in response.points
            ]
        filters = {"document_type": document_type}
        if video_id:
            filters = {"$and": [{"document_type": document_type}, {"video_id": video_id}]}
        return self.vectorstore.similarity_search(query, k=k, filter=filters)

    @staticmethod
    def _search_terms(text: str) -> set[str]:
        common = {"about", "across", "all", "and", "are", "can", "channel", "does",
                  "explain", "for", "from", "how", "into", "lesson", "lessons",
                  "main", "playlist", "the", "these", "this", "those", "video",
                  "videos", "what", "when", "where", "which", "with", "you"}
        return {term for term in re.findall(r"\w+", text.casefold())
                if len(term) >= 3 and term not in common}

    def _title_video_ids(self, query: str, k: int) -> list[str]:
        """Find named lessons even when their spoken captions use another language."""
        terms = self._search_terms(query)
        if not terms:
            return []
        catalog = self.search_catalog(query, k=min(80, max(24, k * 8)))
        title_terms = [(doc, self._search_terms(doc.metadata.get("title", ""))) for doc in catalog]
        frequency = {term: sum(term in words for _, words in title_terms) for term in terms}
        distinct = {term for term in terms if frequency[term] and frequency[term] <= max(1, len(catalog) // 2)}
        if not distinct:
            return []
        ranked = sorted(
            ((sum(1 / frequency[term] for term in distinct & words), doc.metadata.get("video_id"))
             for doc, words in title_terms), key=lambda match: match[0], reverse=True,
        )
        return [video_id for score, video_id in ranked if score > 0 and video_id][:min(2, k)]

    def search(self, query: str, k: int = 5, diversify: bool = False) -> List[Document]:
        fetch_k = max(DIVERSE_FETCH_K, k) if diversify else k
        candidates = self._search_type(query, fetch_k, "transcript")

        # Captions may be Hindi, Arabic, or otherwise unlike an English question.
        # Playlist/catalog titles can still identify the right video; search its
        # transcript chunks directly before filling remaining slots globally.
        targeted = []
        for video_id in self._title_video_ids(query, k):
            targeted.extend(self._search_type(query, 2 if k > 1 else 1, "transcript", video_id=video_id))
        if targeted:
            seen = set()
            merged = []
            for doc in targeted + candidates:
                key = (doc.metadata.get("video_id"), doc.metadata.get("start_time"))
                if key not in seen:
                    seen.add(key)
                    merged.append(doc)
            candidates = merged

        if not diversify:
            return candidates[:k]

        selected = []
        seen_videos = set()
        for document in candidates:
            video_id = document.metadata.get("video_id")
            if video_id in seen_videos:
                continue
            selected.append(document)
            seen_videos.add(video_id)
            if len(selected) == k:
                return selected

        for document in candidates:
            if document not in selected:
                selected.append(document)
            if len(selected) == k:
                break
        return selected

    def search_catalog(self, query: str, k: int = 10) -> List[Document]:
        return self._search_type(query, k, "catalog")

    def indexed_video_ids(self) -> set[str]:
        """Return video ids that already have transcript chunks."""
        if getattr(self, "backend", "chroma") == "qdrant":
            ids: set[str] = set()
            offset = None
            while True:
                points, offset = self.vectorstore.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=self._workspace_filter("transcript"),
                    limit=256,
                    offset=offset,
                    with_payload=["video_id"],
                    with_vectors=False,
                )
                ids.update(
                    point.payload["video_id"]
                    for point in points
                    if point.payload and point.payload.get("video_id")
                )
                if offset is None:
                    return ids

        data = self.vectorstore.get(
            where={"document_type": "transcript"},
            include=["metadatas"],
        )
        return {
            metadata["video_id"]
            for metadata in data.get("metadatas", [])
            if metadata and metadata.get("video_id")
        }

    def clear(self, document_type: str | None = None) -> None:
        if getattr(self, "backend", "chroma") == "qdrant":
            models = self._qdrant_models
            self.vectorstore.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=self._workspace_filter(document_type)
                ),
                wait=True,
            )
            return

        if document_type:
            data = self.vectorstore.get(
                where={"document_type": document_type},
                include=[],
            )
            ids = data.get("ids", [])
            if ids:
                self.vectorstore.delete(ids=ids)
            return

        self.vectorstore.delete_collection()
        persist_dir = os.path.join(VECTORSTORE_PATH, self.collection_name)
        self.vectorstore = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embeddings,
            persist_directory=persist_dir,
        )
        logger.info("Vector store cleared")
