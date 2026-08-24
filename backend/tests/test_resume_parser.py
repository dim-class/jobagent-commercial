"""Local resume extraction - PDF, DOCX, TXT - and the structured profile."""

from __future__ import annotations

import pytest

from app.core.errors import UnsupportedFileType, ValidationError
from app.services.resume_parser import (
    build_profile,
    detect_file_type,
    extract_certifications,
    extract_skills,
    parse_resume,
    split_sections,
)
from tests.conftest import RESUME_TEXT


# --------------------------------------------------------------------------
# file type handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("resume.pdf", "pdf"), ("我的简历.DOCX", "docx"), ("cv.txt", "txt")],
)
def test_detect_file_type(filename, expected):
    assert detect_file_type(filename) == expected


def test_detect_file_type_rejects_unsupported():
    with pytest.raises(UnsupportedFileType):
        detect_file_type("resume.doc")


def test_empty_upload_is_rejected():
    with pytest.raises(ValidationError):
        parse_resume(b"", "resume.pdf")


def test_text_too_short_is_rejected():
    with pytest.raises(ValidationError):
        parse_resume("短".encode("utf-8"), "resume.txt")


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------


def test_parse_txt_resume():
    parsed = parse_resume(RESUME_TEXT.encode("utf-8"), "resume.txt")
    assert parsed.file_type == "txt"
    assert "AWS" in parsed.raw_text
    assert len(parsed.content_hash) == 64


def test_parse_pdf_resume(pdf_bytes):
    parsed = parse_resume(pdf_bytes, "resume.pdf")
    assert parsed.file_type == "pdf"
    assert "AWS" in parsed.raw_text
    assert "Terraform" in parsed.raw_text
    assert parsed.profile["text_length"] > 100


def test_parse_docx_resume(docx_bytes):
    parsed = parse_resume(docx_bytes, "resume.docx")
    assert parsed.file_type == "docx"
    assert "AWS" in parsed.raw_text
    # table cells must be picked up, not just paragraphs
    assert "语言能力" in parsed.raw_text


def test_corrupt_pdf_gives_a_clear_error():
    with pytest.raises(ValidationError) as excinfo:
        parse_resume(b"%PDF-1.4 this is not really a pdf" * 5, "resume.pdf")
    assert "PDF" in excinfo.value.message


def test_identical_bytes_hash_identically(pdf_bytes):
    assert parse_resume(pdf_bytes, "a.pdf").content_hash == parse_resume(pdf_bytes, "b.pdf").content_hash


# --------------------------------------------------------------------------
# structured profile
# --------------------------------------------------------------------------


def test_sections_are_detected():
    sections = split_sections(RESUME_TEXT)
    assert {"work_experience", "projects", "skills", "education"} <= set(sections)


def test_profile_extracts_skills_and_certifications():
    profile = build_profile(RESUME_TEXT, strategy_skills=["WebSphere", "IHS", "AIX"])
    assert {"AWS", "Linux", "Terraform", "Kubernetes", "Python"} <= set(profile["skills"])
    assert {"WebSphere", "IHS", "AIX"} <= set(profile["skills"])
    assert any("AWS Certified" in c for c in profile["certifications"])
    assert profile["years_of_experience"] == 5.0
    assert profile["contact"]["emails"] == ["zhangsan@example.com"]


def test_profile_survives_an_unstructured_resume():
    """Missing sections must not fail the parse - v0.1 requirement."""
    text = "我叫李四，做过一些运维工作，用过 Linux 和 Docker。" * 3
    profile = build_profile(text)
    assert profile["work_experience"] == []
    assert profile["education"] == []
    assert "Linux" in profile["skills"]
    assert profile["summary"]


def test_extract_skills_is_deduplicated():
    skills = extract_skills("AWS AWS aws Linux linux")
    assert skills.count("AWS") == 1
    assert skills.count("Linux") == 1


def test_extract_certifications_deduplicates():
    certs = extract_certifications("RHCE\nRHCE\nCKA")
    assert sorted(certs) == ["CKA", "RHCE"]
