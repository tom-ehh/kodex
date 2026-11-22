"""Form Preview page: served locally, self-contained, and fed by job results."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coi_formularze.api.schemas import FileReportOut, ItemStatus
from coi_formularze.config import Settings, get_settings
from coi_formularze.main import create_app

STATIC = Path(__file__).parent.parent / "src" / "coi_formularze" / "static"

FORM = {
    "title": "Wniosek testowy",
    "tabs": [
        {"id": "t1", "title": "Strona 1", "type": "MAIN", "questions": []},
        {"id": "t2", "title": "Podsumowanie", "type": "FINISH", "questions": []},
    ],
    "flow": {"links": [{"id": "l1", "source": "t1", "target": "t2"}]},
}


def make_client(**overrides) -> TestClient:
    app = create_app()
    settings = Settings(_env_file=None, **overrides)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def seed_done_job(client: TestClient, result: dict) -> str:
    """Put a job whose single item is analysed into the app's in-memory store."""
    report = FileReportOut(
        filename="wniosek.pdf", size_bytes=10, extension=".pdf", declared_kind="pdf",
        detected_kind="pdf", detected_mime="application/pdf", client_mime="application/pdf",
        ok=True, issues=[],
    )
    job = client.app.state.jobs.create([report])
    job.items[0].status = ItemStatus.DONE
    job.items[0].result = result
    return job.id


@pytest.fixture
def client():
    with make_client() as c:
        yield c


def local_assets(html: str) -> list[str]:
    return re.findall(r'(?:src|href)="(/static/[^"]+)"', html)


def test_preview_page_served(client):
    r = client.get("/preview")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="main"' in r.text


def test_preview_not_in_openapi(client):
    assert "/preview" not in client.get("/openapi.json").json()["paths"]


def test_preview_assets_served(client):
    assets = local_assets(client.get("/preview").text)
    assert "/static/vendor/jsonata-2.0.6.min.js" in assets
    for path in assets:
        r = client.get(path)
        assert r.status_code == 200, path
        expected = "text/css" if path.endswith(".css") else "javascript"
        assert expected in r.headers["content-type"], path


def test_module_imports_resolve(client):
    for js in (STATIC / "preview").glob("*.js"):
        for target in re.findall(r'from "\./([^"]+)"', js.read_text(encoding="utf-8")):
            assert client.get(f"/static/preview/{target}").status_code == 200, f"{js.name} -> {target}"


def test_no_external_resources():
    """Runtime must not depend on a CDN or any other external host."""
    html = (STATIC / "preview.html").read_text(encoding="utf-8")
    assert not re.search(r'(?:src|href)="(?:https?:)?//', html)
    for js in (STATIC / "preview").glob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert not re.search(r'(?:import|from)\s*\(?\s*"(?:https?:)?//', text), js.name
        assert "fetch(\"http" not in text and "fetch('http" not in text, js.name


def test_vendored_jsonata_license_is_mit():
    assert "MIT" in (STATIC / "vendor" / "jsonata-LICENSE.txt").read_text(encoding="utf-8")


def test_job_result_reachable_for_preview(client):
    """The preview reads items[n].result from GET /api/v1/jobs/{id}."""
    job_id = seed_done_job(client, FORM)
    body = client.get(f"/api/v1/jobs/{job_id}").json()
    item = body["items"][0]
    assert item["index"] == 0 and item["status"] == "done"
    assert item["result"] == FORM


def test_job_fetch_still_requires_jwt_when_enabled():
    with make_client(jwt_enabled=True, jwt_secret="s" * 32) as c:
        job_id = seed_done_job(c, FORM)
        assert c.get(f"/api/v1/jobs/{job_id}").status_code == 401
        assert c.get("/preview").status_code == 200  # the page itself is public


def test_index_links_to_preview(client):
    # Legacy page (fallback until the React app links job previews itself).
    html = client.get("/static/index.html").text
    assert 'href="/preview"' in html
    assert "/preview?job=" in html
