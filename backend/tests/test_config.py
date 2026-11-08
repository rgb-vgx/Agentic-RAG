"""Settings loading: conf.json defaults and environment overrides."""

from app.config.settings import Settings


def test_conf_json_provides_defaults():
    settings = Settings()
    assert settings.qdrant_collection == "chunks_qwen3_8b"
    assert settings.agent_max_retrieval_iterations == 3
    assert "http://localhost:3000" in settings.cors_origins
    assert "https://rag.thuyetmt.io.vn" in settings.cors_origins


def test_environment_overrides_conf_json(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_RETRIEVAL_ITERATIONS", "7")
    monkeypatch.setenv("QDRANT_COLLECTION", "override")
    settings = Settings()
    assert settings.agent_max_retrieval_iterations == 7
    assert settings.qdrant_collection == "override"


def test_model_catalogue_and_default():
    settings = Settings()
    models = settings.available_models()
    assert models
    assert settings.default_model_option().id in {m.id for m in models}
