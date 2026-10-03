"""Delete expired anonymous workspaces from the production Qdrant collection.

Run this module from a scheduled job, for example once per hour:
    python -m api.cleanup
"""

import time

from qdrant_client import QdrantClient, models

from config import QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_URL, VECTOR_BACKEND


def cleanup_expired_vectors(now: int | None = None) -> None:
    if VECTOR_BACKEND != "qdrant" or not QDRANT_URL:
        raise RuntimeError("Cleanup requires VECTOR_BACKEND=qdrant and QDRANT_URL")
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    if not client.collection_exists(QDRANT_COLLECTION):
        return
    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="expires_at",
                        range=models.Range(lt=now or int(time.time())),
                    )
                ]
            )
        ),
        wait=True,
    )


if __name__ == "__main__":
    cleanup_expired_vectors()
