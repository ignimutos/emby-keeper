from pathlib import Path


def test_runtime_dockerfile_keeps_virtualenv_path_stable():
    dockerfile = Path("Dockerfile").read_text()

    assert "uv venv /opt/venv" in dockerfile
    assert "uv sync --active --locked --no-dev --no-editable" in dockerfile
    assert "COPY --from=builder /opt/venv /opt/venv" in dockerfile
    assert "COPY --from=builder /src/.venv /opt/venv" not in dockerfile
