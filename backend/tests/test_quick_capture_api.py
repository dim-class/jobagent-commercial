"""Quick Capture API: parse -> preview -> confirm, and the AI fallbacks.

No test here spends OpenAI credit: every extraction call is patched. No test
here contacts a recruitment site - the URL is metadata and is never fetched.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest

from app.schemas.quick_capture import AIExtractedJob
from app.services import quick_capture

FIXTURES = Path(__file__).parent / "fixtures" / "quick_capture"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_extraction_cache():
    quick_capture.cache_clear()
    yield
    quick_capture.cache_clear()


# --------------------------------------------------------------------------
# image builders (tiny valid files, generated - no binaries in the repo)
# --------------------------------------------------------------------------


def make_png(width: int = 4, height: int = 4, padding: int = 0) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return png + b"\x00" * padding


JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
WEBP_BYTES = b"RIFF" + struct.pack("<I", 32) + b"WEBP" + b"VP8 " + b"\x00" * 24


def upload(client, payload: bytes, filename: str, content_type: str, **data):
    return client.post(
        "/api/quick-capture/image/parse",
        files={"file": (filename, io.BytesIO(payload), content_type)},
        data=data or None,
    )


# --------------------------------------------------------------------------
# text parse
# --------------------------------------------------------------------------


def test_parse_text_returns_a_candidate_without_saving(client):
    response = client.post(
        "/api/quick-capture/text/parse", json={"text": load("boss_like")}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["ai_used"] is False, "a clean paste must not call AI"

    candidate = body["candidate"]
    assert candidate["title"] == "DevOps工程师"
    assert candidate["city"] == "杭州"
    assert candidate["extraction_method"] == "deterministic"
    assert candidate["confidence"]["overall"] == "high"

    assert client.get("/api/jobs").json()["total"] == 0, "parsing must not persist"


def test_parse_text_rejects_empty_input(client):
    response = client.post("/api/quick-capture/text/parse", json={"text": "   "})
    assert response.status_code == 422
    assert "没有检测到职位文本" in response.json()["message"]


def test_parse_text_rejects_a_too_short_paste(client):
    response = client.post("/api/quick-capture/text/parse", json={"text": "运维"})
    assert response.status_code == 422
    assert "太短" in response.json()["message"]


def test_parse_text_detects_source_from_url_without_fetching(client, monkeypatch):
    """The backend must never make an outbound request for the supplied URL."""
    import httpx

    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the backend fetched the source URL")

    monkeypatch.setattr(httpx.Client, "request", _boom)
    monkeypatch.setattr(httpx.AsyncClient, "request", _boom)

    response = client.post(
        "/api/quick-capture/text/parse",
        json={
            "text": load("boss_like"),
            "source_url": "https://www.zhipin.com/job_detail/abc.html?securityId=secret",
        },
    )
    candidate = response.json()["candidate"]
    assert candidate["source"] == "boss"
    assert candidate["source_url"] == "https://www.zhipin.com/job_detail/abc.html"


# --------------------------------------------------------------------------
# AI fallback for text
# --------------------------------------------------------------------------


@pytest.fixture
def fake_text_ai(monkeypatch):
    calls: list[str] = []
    box = {
        "result": AIExtractedJob(
            company="某某科技有限公司",
            title="云原生开发工程师",
            city="成都",
            salary_text="25-35K",
            experience_text="3-5年",
            education_text="本科",
            raw_description="岗位职责：负责云原生平台建设。\n任职要求：熟悉 Kubernetes。",
            notes=["公司名称来自页面顶部"],
        )
    }

    async def _fake(text, *, settings=None):
        calls.append(text)
        return box["result"], "test-model-fast"

    monkeypatch.setattr("app.agents.job_import_agent.run_text_extraction", _fake)
    return type("FakeAI", (), {"calls": calls, "box": box})


def test_weak_text_falls_back_to_ai(client, fake_text_ai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        response = client.post(
            "/api/quick-capture/text/parse", json={"text": load("noisy_minimal") + "x" * 40}
        )
        body = response.json()
        assert body["ai_used"] is True
        assert len(fake_text_ai.calls) == 1

        candidate = body["candidate"]
        assert candidate["title"] == "云原生开发工程师"
        assert candidate["city"] == "成都"
        assert candidate["extraction_method"] == "hybrid"
        assert "公司名称来自页面顶部" in candidate["warnings"]
    finally:
        get_settings.cache_clear()


def test_ai_result_is_cached_per_input(client, fake_text_ai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        payload = {"text": load("noisy_minimal") + "y" * 40}
        client.post("/api/quick-capture/text/parse", json=payload)
        second = client.post("/api/quick-capture/text/parse", json=payload).json()

        assert len(fake_text_ai.calls) == 1, "identical input must not re-call the model"
        assert second["ai_used"] is True
        assert "缓存" in second["message"]
    finally:
        get_settings.cache_clear()


def test_ai_failure_falls_back_to_deterministic_result(client, monkeypatch):
    """An AI outage must not make Quick Capture unusable."""
    from app.core.errors import UpstreamError

    async def _fail(text, *, settings=None):
        raise UpstreamError("AI提取服务暂时不可用，请使用文字粘贴或手动补充字段。")

    monkeypatch.setattr("app.agents.job_import_agent.run_text_extraction", _fail)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        response = client.post(
            "/api/quick-capture/text/parse", json={"text": load("noisy_minimal") + "z" * 40}
        )
        assert response.status_code == 200, "still usable without AI"

        body = response.json()
        assert body["ai_used"] is False
        assert "AI提取服务暂时不可用" in body["ai_error"]
        assert body["candidate"]["extraction_method"] == "deterministic"
    finally:
        get_settings.cache_clear()


def test_missing_api_key_still_parses_deterministically(client):
    """No key configured (the suite default): text import keeps working."""
    response = client.post(
        "/api/quick-capture/text/parse", json={"text": load("boss_like")}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["ai_available"] is False
    assert body["candidate"]["title"] == "DevOps工程师"


def test_allow_ai_false_skips_the_model(client, fake_text_ai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        response = client.post(
            "/api/quick-capture/text/parse",
            json={"text": load("noisy_minimal") + "q" * 40, "allow_ai": False},
        )
        assert response.json()["ai_used"] is False
        assert fake_text_ai.calls == []
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------
# image parse
# --------------------------------------------------------------------------


@pytest.fixture
def fake_vision_ai(monkeypatch):
    seen: list[tuple[int, str]] = []
    box = {
        "result": AIExtractedJob(
            company="截图科技有限公司",
            title="SRE工程师",
            city="上海",
            salary_text="30-45K",
            experience_text="5-10年",
            education_text="本科",
            raw_description="岗位职责：保障核心链路稳定性，建设 SLO 体系。",
            partial_description=True,
        )
    }

    async def _fake(image_bytes, mime_type, *, settings=None):
        seen.append((len(image_bytes), mime_type))
        return box["result"], "test-model-fast"

    monkeypatch.setattr("app.agents.job_import_agent.run_vision_extraction", _fake)
    return type("FakeVision", (), {"seen": seen, "box": box})


@pytest.fixture
def ai_enabled(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_image_parse_extracts_a_candidate(client, fake_vision_ai, ai_enabled):
    response = upload(client, make_png(), "shot.png", "image/png")
    assert response.status_code == 200

    body = response.json()
    assert body["ai_used"] is True
    candidate = body["candidate"]
    assert candidate["title"] == "SRE工程师"
    assert candidate["extraction_method"] == "ai_vision"
    assert "截图可能仅包含部分职位描述" in candidate["warnings"]
    assert fake_vision_ai.seen[0][1] == "image/png"


@pytest.mark.parametrize(
    ("payload", "filename", "content_type"),
    [
        (make_png(), "a.png", "image/png"),
        (JPEG_BYTES, "a.jpg", "image/jpeg"),
        (WEBP_BYTES, "a.webp", "image/webp"),
    ],
)
def test_accepted_image_formats(client, fake_vision_ai, ai_enabled, payload, filename, content_type):
    assert upload(client, payload, filename, content_type).status_code == 200


def test_image_mime_is_validated_from_magic_bytes(client, fake_vision_ai, ai_enabled):
    """A .png name and an image/png header do not make a GIF a PNG."""
    response = upload(client, b"GIF89a" + b"\x00" * 40, "fake.png", "image/png")
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_image"
    assert "格式不支持" in response.json()["message"]
    assert fake_vision_ai.seen == [], "nothing was sent to the model"


def test_pdf_upload_is_rejected(client, fake_vision_ai, ai_enabled):
    response = upload(client, b"%PDF-1.4\n" + b"\x00" * 40, "jd.pdf", "application/pdf")
    assert response.status_code == 415


def test_oversized_image_is_rejected(client, fake_vision_ai, ai_enabled, monkeypatch):
    monkeypatch.setenv("QUICK_CAPTURE_MAX_IMAGE_MB", "1")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        oversized = make_png(padding=1024 * 1024 + 10)
        response = upload(client, oversized, "big.png", "image/png")
        assert response.status_code == 422
        assert response.json()["code"] == "image_too_large"
        assert "图片过大" in response.json()["message"]
        assert fake_vision_ai.seen == []
    finally:
        get_settings.cache_clear()


def test_empty_image_is_rejected(client, fake_vision_ai, ai_enabled):
    assert upload(client, b"", "empty.png", "image/png").status_code == 422


def test_image_is_never_written_to_disk(client, fake_vision_ai, ai_enabled):
    """Screenshots are ephemeral: only the hash survives the request."""
    from app.core.paths import DATA_DIR

    before = {p for p in DATA_DIR.rglob("*") if p.is_file()}
    upload(client, make_png(width=8, height=8), "shot.png", "image/png")
    after = {p for p in DATA_DIR.rglob("*") if p.is_file()}

    assert after == before, "quick capture must not persist the uploaded image"


def test_image_parse_without_ai_key_explains_the_fallback(client):
    response = upload(client, make_png(), "shot.png", "image/png")
    assert response.status_code == 503
    assert response.json()["code"] == "ai_extraction_disabled"
    assert "文字粘贴" in response.json()["message"]


def test_image_parse_respects_the_disable_flag(client, fake_vision_ai, ai_enabled, monkeypatch):
    monkeypatch.setenv("QUICK_CAPTURE_AI_EXTRACTION", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        response = upload(client, make_png(), "shot.png", "image/png")
        assert response.status_code == 503
        assert "已在设置中关闭" in response.json()["message"]
        assert fake_vision_ai.seen == []
    finally:
        get_settings.cache_clear()


def test_text_parsing_still_works_when_ai_extraction_is_disabled(client, monkeypatch):
    monkeypatch.setenv("QUICK_CAPTURE_AI_EXTRACTION", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        body = client.post(
            "/api/quick-capture/text/parse", json={"text": load("boss_like")}
        ).json()
        assert body["candidate"]["title"] == "DevOps工程师"
        assert body["ai_used"] is False
    finally:
        get_settings.cache_clear()


# --------------------------------------------------------------------------
# confirm -> existing intake pipeline
# --------------------------------------------------------------------------


def parse_then_candidate(client, fixture="boss_like", **extra):
    body = client.post(
        "/api/quick-capture/text/parse", json={"text": load(fixture), **extra}
    ).json()
    return body["candidate"]


def test_confirm_saves_through_the_existing_pipeline(client):
    candidate = parse_then_candidate(client)
    response = client.post("/api/quick-capture/confirm", json={"candidate": candidate})
    assert response.status_code == 200

    body = response.json()
    assert body["duplicate"] is False

    detail = client.get(f"/api/jobs/{body['job_id']}").json()
    assert detail["title"] == "DevOps工程师"
    assert detail["city"] == "杭州"
    assert detail["content_hash"], "the existing hashing ran"
    assert detail["normalized_description"], "the existing normalizer ran"
    assert "\n\n\n" not in detail["normalized_description"]
    assert any("快速采集" in (e["notes"] or "") for e in detail["events"])


def test_confirmed_job_is_an_ordinary_job(client):
    """Quick Capture jobs must need no special-case handling anywhere."""
    candidate = parse_then_candidate(client)
    job_id = client.post("/api/quick-capture/confirm", json={"candidate": candidate}).json()[
        "job_id"
    ]

    listing = client.get("/api/jobs").json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == job_id

    assert client.get("/api/jobs", params={"city": "杭州"}).json()["total"] == 1
    assert client.get("/api/jobs", params={"keyword": "Terraform"}).json()["total"] == 1

    summary = client.get("/api/dashboard/summary").json()
    assert summary["total_jobs"] == 1
    assert summary["by_city"]["杭州"] == 1

    assert client.patch(f"/api/jobs/{job_id}", json={"status": "saved"}).json()["status"] == "saved"


def test_user_edits_win_over_extraction(client):
    candidate = parse_then_candidate(client)
    candidate["company"] = "我手动改的公司"
    candidate["title"] = "我手动改的职位"
    candidate["city"] = "成都"
    candidate["salary_text"] = "40-60K"

    job_id = client.post("/api/quick-capture/confirm", json={"candidate": candidate}).json()[
        "job_id"
    ]
    detail = client.get(f"/api/jobs/{job_id}").json()

    assert detail["company"] == "我手动改的公司"
    assert detail["title"] == "我手动改的职位"
    assert detail["city"] == "成都"
    assert detail["salary_text"] == "40-60K"


def test_confirming_twice_is_a_duplicate_not_an_error(client):
    candidate = parse_then_candidate(client)
    first = client.post("/api/quick-capture/confirm", json={"candidate": candidate}).json()

    second_response = client.post("/api/quick-capture/confirm", json={"candidate": candidate})
    assert second_response.status_code == 200

    second = second_response.json()
    assert second["duplicate"] is True
    assert second["job_id"] == first["job_id"]
    assert "已存在" in second["message"]
    assert client.get("/api/jobs").json()["total"] == 1


def test_quick_capture_duplicates_a_manually_pasted_job(client):
    """Dedup is content-based, so the same JD via two intake paths is one job."""
    candidate = parse_then_candidate(client)
    client.post("/api/quick-capture/confirm", json={"candidate": candidate})

    manual = client.post(
        "/api/jobs",
        json={
            "title": candidate["title"],
            "company": candidate["company"],
            "city": candidate["city"],
            "raw_description": candidate["raw_description"],
        },
    )
    assert manual.status_code == 409
    assert client.get("/api/jobs").json()["total"] == 1


def test_confirm_requires_a_title(client):
    candidate = parse_then_candidate(client)
    candidate["title"] = "   "
    response = client.post("/api/quick-capture/confirm", json={"candidate": candidate})

    assert response.status_code == 422
    assert "无法识别职位标题" in response.json()["message"]
    assert client.get("/api/jobs").json()["total"] == 0


def test_confirm_requires_a_description(client):
    candidate = parse_then_candidate(client)
    candidate["raw_description"] = "太短"
    response = client.post("/api/quick-capture/confirm", json={"candidate": candidate})

    assert response.status_code == 422
    assert "职位描述" in response.json()["message"]


def test_confirmed_job_keeps_the_detected_source(client):
    candidate = parse_then_candidate(
        client, source_url="https://www.zhipin.com/job_detail/abc.html?securityId=x"
    )
    job_id = client.post("/api/quick-capture/confirm", json={"candidate": candidate}).json()[
        "job_id"
    ]
    detail = client.get(f"/api/jobs/{job_id}").json()

    assert detail["source"] == "boss"
    assert detail["source_url"] == "https://www.zhipin.com/job_detail/abc.html"


def test_quick_captured_job_runs_through_the_existing_analysis_pipeline(
    client, active_resume, monkeypatch
):
    """JobMatchAgent must not know or care that this came from Quick Capture."""
    from app.schemas.analysis import JobMatchResult

    calls: list[dict] = []

    async def _fake_run_job_match(**kwargs):
        calls.append(kwargs)
        return JobMatchResult.model_validate(
            {
                "overall_score": 81,
                "verdict": "apply",
                "role_fit_score": 84,
                "skill_fit_score": 80,
                "experience_fit_score": 78,
                "location_fit_score": 100,
                "salary_fit_score": 75,
                "matched_skills": ["AWS"],
                "missing_skills": [],
                "strengths": ["云运维经验匹配"],
                "gaps": [],
                "risk_flags": [],
                "experience_gap": "无明显差距",
                "role_summary": "云平台运维",
                "reasoning_summary": "技能重合度高。",
                "greeting_message": "您好，期待沟通。",
            }
        )

    monkeypatch.setattr("app.services.job_matcher.run_job_match", _fake_run_job_match)

    candidate = parse_then_candidate(client)
    job_id = client.post("/api/quick-capture/confirm", json={"candidate": candidate}).json()[
        "job_id"
    ]

    analysis = client.post(f"/api/jobs/{job_id}/analyze", json={}).json()
    assert analysis["result"]["overall_score"] == 81

    assert len(calls) == 1
    assert set(calls[0]) == {
        "model_name",
        "resume_profile",
        "resume_excerpt",
        "strategy",
        "job",
        "pre_analysis",
        "settings",
    }
    assert "Terraform" in calls[0]["job"]["normalized_description"]

    cached = client.post(f"/api/jobs/{job_id}/analyze", json={}).json()
    assert cached["meta"]["cached"] is True
    assert len(calls) == 1


# --------------------------------------------------------------------------
# capability probe
# --------------------------------------------------------------------------


def test_settings_endpoint_reports_capabilities_without_secrets(client):
    body = client.get("/api/quick-capture/settings").json()
    assert body["ai_extraction_enabled"] is True
    assert body["openai_configured"] is False
    assert body["max_image_mb"] == 10
    assert "image/png" in body["accepted_image_types"]
    assert not any("key" in k.lower() for k in body)
