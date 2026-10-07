import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from schnittwerk import app as app_module
from schnittwerk import jobs


@pytest.fixture()
def client(tmp_path, monkeypatch):
    settings = app_module.settings.__class__(**{**app_module.settings.__dict__, "data_dir": tmp_path,
                                                "access_token": "geheim"})
    monkeypatch.setattr(app_module, "settings", settings)
    monkeypatch.setattr(jobs, "settings", settings)
    with TestClient(app_module.app) as c:
        yield c


def test_config_is_public_and_jobs_need_token(client):
    assert client.get("/api/config").json()["auth"] is True
    assert client.get("/api/jobs/doesnotexist").status_code == 401
    assert client.get("/api/jobs/doesnotexist", headers={"Authorization": "Bearer falsch"}).status_code == 401
    assert client.get("/api/jobs/doesnotexist?token=geheim").status_code == 404


def test_url_job_rejects_internal_address(client):
    res = client.post("/api/jobs/url", json={"url": "http://127.0.0.1/video"},
                      headers={"Authorization": "Bearer geheim"})
    assert res.status_code == 422


def test_upload_rejects_non_video(client):
    res = client.post("/api/jobs/upload", files={"file": ("notes.txt", b"hallo")},
                      headers={"Authorization": "Bearer geheim"})
    assert res.status_code == 422


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_produces_vertical_mp4(client, tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-shortest",
                    "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
    job = jobs.new_job({"min_len": 15, "max_len": 30, "count": 1, "language": None, "use_ai": False})
    jobs._update(jobs._jobs, job["id"], status="ready", source=str(src), duration=6.0, width=640, height=360,
                 has_audio=True, chunks=[{"s": 1.0, "e": 2.0, "words": [{"w": "Hallo", "s": 1.0, "e": 2.0}]}])
    auth = {"Authorization": "Bearer geheim"}
    res = client.post(f"/api/jobs/{job['id']}/renders", headers=auth,
                      json={"start": 0.5, "end": 3.5, "title": "Test Clip", "hook": "Achtung", "aspect": "9:16"})
    assert res.status_code == 200
    rid = res.json()["id"]
    for _ in range(120):
        status = client.get(f"/api/renders/{rid}", headers=auth).json()
        if status["status"] in ("done", "error"):
            break
        import time
        time.sleep(0.25)
    assert status["status"] == "done", status
    assert status["filename"] == "test-clip.mp4"
    out = tmp_path / "out.mp4"
    out.write_bytes(client.get(f"/api/renders/{rid}/file?token=geheim").content)
    probe = jobs.media.probe(out)
    assert (probe["width"], probe["height"]) == (720, 1280)
    assert 2.8 < probe["duration"] < 3.3
