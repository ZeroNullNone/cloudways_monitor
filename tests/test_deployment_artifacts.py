from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deployment_artifacts_package_single_container_for_caddy() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    deployment = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")

    assert "EXPOSE 8000" in dockerfile
    assert (
        'CMD ["uvicorn", "cloudways_monitor.app:create_app", "--factory",'
        ' "--host", "0.0.0.0", "--port", "8000"]'
    ) in dockerfile

    assert "cloudways-monitor:" in compose
    assert "build: ." in compose
    assert "env_file:" in compose
    assert "- .env" in compose
    assert '"127.0.0.1:8000:8000"' in compose
    assert "cloudways-monitor-data:/data" in compose
    assert "cloudways-monitor-data:" in compose
    assert "SQLITE_PATH=/data/cloudways-monitor.sqlite3" in deployment

    assert "reverse_proxy 127.0.0.1:8000" in deployment
    assert "curl -c" in deployment
    assert "curl -b" in deployment
    assert "/api/auth/login" in deployment
    assert "/api/doctor" in deployment
