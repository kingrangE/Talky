from pathlib import Path


def test_docker_compose_waits_for_ready_model_and_env_is_optional():
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()

    assert "required: false" in compose
    assert 'ollama show \\"$${MODEL_NAME}\\"' in compose
    assert "condition: service_healthy" in compose
