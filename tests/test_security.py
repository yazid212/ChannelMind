import logging
from pathlib import Path

from src.utils import log_exception_safely


def test_safe_exception_logging_never_serializes_exception_details(caplog):
    sentinel = "secret-test-sentinel"

    with caplog.at_level(logging.ERROR, logger="youtube-rag"):
        log_exception_safely("Test operation", RuntimeError(sentinel))

    assert sentinel not in caplog.text
    assert "RuntimeError" in caplog.text


def test_web_ui_does_not_render_exception_objects():
    app_source = (Path(__file__).parents[1] / "app" / "app.py").read_text(
        encoding="utf-8"
    )

    assert "str(error)" not in app_source
    assert "str(e)" not in app_source
    assert "st.exception(" not in app_source


def test_chroma_remains_embedded_and_has_no_network_server_entrypoint():
    root = Path(__file__).parents[1]
    embedder_source = (root / "src" / "embedder.py").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8").lower()

    assert "persist_directory=persist_dir" in embedder_source
    assert "host=" not in embedder_source
    assert "chroma run" not in dockerfile
    assert "expose 8000" not in dockerfile


def test_production_api_never_accepts_provider_keys_from_requests():
    root = Path(__file__).parents[1]
    schemas = (root / "api" / "schemas.py").read_text(encoding="utf-8")
    assert "api_key" not in schemas.lower()
    assert "provider" not in schemas.lower()
