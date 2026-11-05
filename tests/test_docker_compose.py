from pathlib import Path
import yaml


def test_docker_compose_waits_for_ready_model_and_env_is_optional():
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()

    assert "required: false" in compose
    assert 'ollama show \\"$${MODEL_NAME}\\"' in compose
    assert "condition: service_healthy" in compose


def test_only_app_loopback_is_published_and_worker_shares_persistent_audio():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    services = compose["services"]
    assert services["app"]["ports"] == ["127.0.0.1:8501:8501"]
    for service in ("postgres", "neo4j", "ollama", "worker"):
        assert not services[service].get("ports")
    for service in ("app", "worker"):
        assert "mock-exam-data:/app/.data/mock-exam" in services[service]["volumes"]
        assert services[service]["environment"]["MOCK_EXAM_DATA_DIR"] == "/app/.data/mock-exam"
