import io

import pytest
from pypdf import PdfReader
from ruamel.yaml import YAML

from careerpilot.core import render, secrets

CV = YAML(typ="safe").load(open("careerpilot/examples/sam-nurse-uk/master_cv.yaml", encoding="utf-8"))


def text(pdf):
    return "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    return tmp_path


def test_thirteen_designs_render():
    assert len(render.designs()) >= 13
    for d in render.designs():
        assert "Sam Sample" in text(render.cv_pdf(CV, d, "en", "GB")), d


def test_designer_settings():
    s = {"hidden": ["volunteering"], "titles": {"experience": "Work history"}, "max_items": {"experience": 1},
         "max_bullets": 1, "order": ["experience", "summary", "education", "skills"], "style": {"accent": "#ff0000"}}
    out = text(render.cv_pdf(CV, "classic", "en", "GB", settings=s))
    assert "Work history" in out and "First aid volunteer" not in out and "Care of the Elderly" not in out
    assert "student nurses per year" not in out  # only the first line of the job is kept
    assert out.index("Work history") < out.index("Registered adult nurse")


def test_presets(home):
    render.save_preset("Research look", "academic", {"hidden": ["interests"]})
    assert render.resolve("Research look") == ("academic", {"hidden": ["interests"]})
    assert render.resolve("classic") == ("classic", {}) and render.resolve("nonsense") == ("classic", {})


def test_secrets_round_trip(monkeypatch):
    store = {}
    import keyring
    monkeypatch.setattr(keyring, "get_password", lambda s, n: store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: store.__setitem__((s, n), v))
    monkeypatch.setattr(keyring, "delete_password", lambda s, n: store.pop((s, n)))
    secrets.put("portal:example.com", "Xq9-secret-7731")
    assert secrets.get("portal:example.com") == "Xq9-secret-7731" and secrets.names() == ["portal:example.com"]
    assert secrets.redact("pw=Xq9-secret-7731") == "pw=•••"
    secrets.delete_all()
    assert secrets.get("portal:example.com") == "" and store == {}


def long_cv():
    cv = {**CV, "experience": [{**e, "title": f"{e['title']} {n}"} for n in range(6) for e in CV["experience"]]}
    return cv


def test_every_page_as_svg():
    pages = render.cv_pages(long_cv(), "classic", "en", "GB")
    assert len(pages) >= 2 and all(b"<svg" in p[:300] for p in pages)


def test_thumb_key_follows_what_it_shows(home):
    import os
    from careerpilot.core import profile as pf
    pf.write("preferences", {"targets": [{"code": "GB"}]})
    k = render.thumb_key("classic")
    pf.write("preferences", {"targets": [{"code": "DE"}]})
    assert render.thumb_key("classic") != k
    k = render.thumb_key("classic")
    (pf.folder() / "photo.png").write_bytes(b"x")
    assert render.thumb_key("classic") != k
    k = render.thumb_key("classic")
    yaml = render.TPL / "designs" / "classic.yaml"
    st = yaml.stat()
    try:
        os.utime(yaml, (st.st_atime, st.st_mtime + 5))
        assert render.thumb_key("classic") != k and render.thumb_key("modern") == render.thumb_key("modern")
    finally:
        os.utime(yaml, (st.st_atime, st.st_mtime))
    render.save_preset("Mine", "classic", {"style": {"accent": "#ff0000"}})
    assert render.thumb_key("Mine") != render.thumb_key("classic")


def test_thumb_and_preview_api(home):
    import shutil
    from fastapi.testclient import TestClient
    from careerpilot.app import server
    from careerpilot.core import profile as pf
    shutil.copy("careerpilot/examples/sam-nurse-uk/master_cv.yaml", pf.folder() / "master_cv.yaml")
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    key = c.get("/api/designs").json()["keys"]["classic"]
    r = c.get(f"/api/designs/classic/thumb.png?v={key}")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert [p.name for p in (home / "cache").iterdir()] == [f"thumb-classic-{key}.png"]
    assert c.get("/api/designs/nonsense/thumb.png").status_code == 404
    r = c.post("/api/designs/preview", json={"design": "classic", "settings": {"pages": 3}}).json()
    assert r["limit"] == 3 and len(r["pages"]) >= 1 and "<svg" in r["pages"][0][:300]
