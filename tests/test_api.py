import jwt
import pytest
from fastapi.testclient import TestClient

from coi_formularze.config import Settings, get_settings
from coi_formularze.main import create_app
from tests import samples


def make_client(**overrides) -> TestClient:
    app = create_app()
    settings = Settings(_env_file=None, **overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.fixture
def client():
    with make_client(max_file_size_bytes=1024, max_batch_files=3) as c:
        yield c


def test_health_and_index(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "<form" in client.get("/").text


def test_formats(client):
    body = client.get("/api/v1/formats").json()
    assert body["max_file_size_bytes"] == 1024
    assert {f["kind"] for f in body["formats"]} == {"pdf", "doc", "docx", "ods", "xlsx", "jpeg", "png"}


def test_validate_one(client):
    r = client.post("/api/v1/validate", files={"file": ("a.pdf", samples.PDF, "application/pdf")})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_validate_batch_mixed(client):
    files = [
        ("files", ("a.docx", samples.DOCX)),
        ("files", ("b.pdf", samples.PNG)),
        ("files", ("c.png", b"x" * 2000)),
    ]
    body = client.post("/api/v1/validate/batch", files=files).json()
    assert (body["total"], body["accepted"], body["rejected"]) == (3, 1, 2)
    assert body["files"][2]["issues"][0]["code"] == "file_too_large"


def test_batch_limit(client):
    files = [("files", (f"{i}.pdf", samples.PDF)) for i in range(4)]
    assert client.post("/api/v1/validate/batch", files=files).status_code == 422


def test_analyze_rejects_invalid_file(client):
    bad = client.post("/api/v1/forms/analyze", files={"file": ("a.pdf", samples.PNG)})
    assert bad.status_code == 422 and bad.json()["file"]["ok"] is False


def test_jobs_crud(client):
    files = [("files", ("a.pdf", samples.PDF)), ("files", ("b.exe", samples.PDF))]
    job = client.post("/api/v1/jobs", files=files).json()
    assert [i["status"] for i in job["items"]] == ["pending", "rejected"]

    assert client.get(f"/api/v1/jobs/{job['id']}").json()["id"] == job["id"]
    listed = client.get("/api/v1/jobs").json()
    assert listed[0]["id"] == job["id"] and "items" not in listed[0]

    assert client.delete(f"/api/v1/jobs/{job['id']}").status_code == 204
    assert client.get(f"/api/v1/jobs/{job['id']}").status_code == 404
    assert client.delete(f"/api/v1/jobs/{job['id']}").status_code == 404


def test_jwt():
    secret = "s" * 32
    with make_client(jwt_enabled=True, jwt_secret=secret) as c:
        assert c.get("/api/v1/formats").status_code == 401
        assert c.get("/health").status_code == 200
        bad = {"Authorization": "Bearer nope"}
        assert c.get("/api/v1/formats", headers=bad).status_code == 401
        token = jwt.encode({"sub": "tester"}, secret, algorithm="HS256")
        good = {"Authorization": f"Bearer {token}"}
        assert c.get("/api/v1/formats", headers=good).status_code == 200


def test_jwt_requires_secret():
    with pytest.raises(ValueError):
        Settings(_env_file=None, jwt_enabled=True)


def test_config_errors_hide_secrets():
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None, jwt_enabled=True, jwt_algorithm="top-secret-value")
    assert "top-secret-value" not in str(exc.value)
