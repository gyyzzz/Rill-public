from pathlib import Path

from fastapi.testclient import TestClient

from orionxcore.config import Settings, get_settings
from orionxcore.main import app


def _make_settings(tmp_path: Path, **overrides) -> Settings:
    base = {
        "api_key": "test",
        "enable_upload": True,
        "upload_workdir": tmp_path / "uploads",
        "terminal_workdir": tmp_path,
    }
    base.update(overrides)
    return Settings(**base)


def test_upload_txt_file(tmp_path):
    settings = _make_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    content = b"hello world\nthis is a test file"
    response = client.post(
        "/v1/upload",
        files={"file": ("test.txt", content, "text/plain")},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["filename"] == "test.txt"
    assert data["size_bytes"] == len(content)
    assert Path(data["path"]).exists()
    assert Path(data["path"]).read_bytes() == content


def test_upload_csv_file(tmp_path):
    settings = _make_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    content = b"name,age\nAlice,30\nBob,25"
    response = client.post(
        "/v1/upload",
        files={"file": ("data.csv", content, "text/csv")},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["filename"] == "data.csv"
    assert Path(data["path"]).read_bytes() == content


def test_upload_json_file(tmp_path):
    settings = _make_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    content = b'{"key": "value"}'
    response = client.post(
        "/v1/upload",
        files={"file": ("config.json", content, "application/json")},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["filename"] == "config.json"


def test_upload_disabled(tmp_path):
    settings = _make_settings(tmp_path, enable_upload=False)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post(
        "/v1/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 403


def test_upload_file_too_large(tmp_path):
    settings = _make_settings(tmp_path, upload_max_size_mb=1)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    large_content = b"x" * (2 * 1024 * 1024)  # 2MB, exceeds 1MB limit
    response = client.post(
        "/v1/upload",
        files={"file": ("large.txt", large_content, "text/plain")},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 413


def test_upload_disallowed_extension(tmp_path):
    settings = _make_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post(
        "/v1/upload",
        files={"file": ("virus.exe", b"binary", "application/octet-stream")},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]


def test_upload_path_traversal_blocked(tmp_path):
    settings = _make_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post(
        "/v1/upload",
        files={"file": ("../../../etc/passwd", b"root:x:0", "text/plain")},
    )
    app.dependency_overrides.clear()
    # Route strips path components, so it should succeed with a sanitized name
    assert response.status_code == 200
    data = response.json()
    assert "passwd" in data["filename"]
    uploaded_path = Path(data["path"])
    upload_dir = tmp_path / "uploads"
    assert upload_dir in uploaded_path.parents or uploaded_path == upload_dir


def test_upload_dotfile_sanitized(tmp_path):
    settings = _make_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post(
        "/v1/upload",
        files={"file": (".hidden", b"secret", "text/plain")},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert not data["filename"].startswith(".")


def test_upload_no_extension(tmp_path):
    settings = _make_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    content = b"no extension file"
    response = client.post(
        "/v1/upload",
        files={"file": ("Makefile", content, "text/plain")},
    )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["filename"] == "Makefile"
