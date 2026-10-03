# Dependency audit exceptions

Exceptions are narrow, time-bound, and paired with an automated compensating
control. Repository maintainers own review and removal.

## ChromaDB server advisories

- IDs: `PYSEC-2026-311`, `PYSEC-2026-3813`, `PYSEC-2026-3814`,
  `PYSEC-2026-3815`
- Package observed: `chromadb==1.5.9`
- Status: not reachable in the current deployment
- Owner: repository maintainers
- Recorded: 2026-09-25
- Review/expiry: 2026-10-25
- Reason: all four advisories require Chroma's network server endpoints and
  concern remote collection configuration or server authorization. This app
  passes only `persist_directory` to `langchain_chroma.Chroma`, which selects
  `chromadb.PersistentClient`; it does not start or expose a Chroma HTTP server.
- Compensating control: `tests/test_security.py` fails if the application adds
  a Chroma host, a `chroma run` entrypoint, or port 8000 exposure. The container
  exposes only Streamlit on port 8501.
- Removal condition: remove the ignores after Chroma publishes a non-affected
  release that passes the project test suite, or immediately if a network
  Chroma deployment is introduced.
