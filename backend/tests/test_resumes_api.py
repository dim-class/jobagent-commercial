"""Resume upload / activation endpoints."""

from __future__ import annotations


def _upload(client, payload: bytes, filename: str):
    return client.post(
        "/api/resumes/upload",
        files={"file": (filename, payload, "application/octet-stream")},
    )


def test_upload_pdf_resume(client, pdf_bytes):
    response = _upload(client, pdf_bytes, "简历.pdf")
    assert response.status_code == 201

    body = response.json()
    assert body["reused_existing"] is False

    resume = body["resume"]
    assert resume["file_type"] == "pdf"
    assert resume["is_active"] is True
    assert resume["text_length"] > 100
    assert "AWS" in resume["skills"]
    assert "AWS" in resume["raw_text_preview"]
    assert resume["parsed_profile"]["sections_detected"]


def test_upload_docx_resume(client, docx_bytes):
    resume = _upload(client, docx_bytes, "resume.docx").json()["resume"]
    assert resume["file_type"] == "docx"
    assert "Terraform" in resume["skills"]


def test_upload_rejects_unsupported_type(client):
    response = _upload(client, b"hello world" * 20, "resume.doc")
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_file_type"


def test_upload_rejects_an_empty_file(client):
    assert _upload(client, b"", "resume.pdf").status_code == 422


def test_reuploading_the_same_bytes_reactivates_instead_of_duplicating(client, pdf_bytes):
    first = _upload(client, pdf_bytes, "resume.pdf").json()["resume"]
    second_body = _upload(client, pdf_bytes, "resume-copy.pdf").json()

    assert second_body["reused_existing"] is True
    assert second_body["resume"]["id"] == first["id"]
    assert len(client.get("/api/resumes").json()) == 1


def test_only_one_resume_is_active(client, pdf_bytes, docx_bytes):
    first_id = _upload(client, pdf_bytes, "a.pdf").json()["resume"]["id"]
    second_id = _upload(client, docx_bytes, "b.docx").json()["resume"]["id"]

    listed = client.get("/api/resumes").json()
    assert {r["id"]: r["is_active"] for r in listed} == {first_id: False, second_id: True}
    assert client.get("/api/resumes/active").json()["id"] == second_id

    reactivated = client.post(f"/api/resumes/{first_id}/activate").json()
    assert reactivated["is_active"] is True
    assert client.get("/api/resumes/active").json()["id"] == first_id


def test_get_resume_and_404(client, pdf_bytes):
    resume_id = _upload(client, pdf_bytes, "a.pdf").json()["resume"]["id"]
    assert client.get(f"/api/resumes/{resume_id}").status_code == 200
    assert client.get("/api/resumes/999999").status_code == 404


def test_active_resume_404_when_none_uploaded(client):
    response = client.get("/api/resumes/active")
    assert response.status_code == 404
    assert response.json()["detail"]["action"] == "upload_resume"
