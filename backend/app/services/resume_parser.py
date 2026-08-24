"""Local resume parsing - no AI required.

``.pdf`` goes through PyMuPDF, ``.docx`` through python-docx (paragraphs *and*
table cells, because Chinese resumes are very often one big table). A plain
``.txt`` is accepted too since it costs nothing.

The structured profile is produced by deterministic heading detection plus a
skill keyword scan. It is intentionally forgiving: a resume that yields no
recognisable sections still parses successfully with empty section lists, and
the raw text is always stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import UnsupportedFileType, ValidationError
from app.core.logging import get_logger, log_event
from app.services.hashing import sha256_bytes

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024

# Section heading -> canonical profile key. Matching is done on a normalized,
# punctuation-stripped, lowercased line.
_SECTION_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("summary", ("个人简介", "自我评价", "个人总结", "个人评价", "profile", "summary", "about me", "objective")),
    ("work_experience", ("工作经历", "工作经验", "职业经历", "实习经历", "work experience", "experience", "employment")),
    ("projects", ("项目经历", "项目经验", "项目描述", "projects", "project experience")),
    ("skills", ("专业技能", "技能清单", "技术栈", "个人技能", "技能", "skills", "technical skills", "tech stack")),
    ("certifications", ("证书", "认证", "资格证书", "certifications", "certificates", "licenses")),
    ("education", ("教育经历", "教育背景", "学历", "education", "academic")),
    ("languages", ("语言能力", "语言", "languages", "language skills")),
]

# Skills we look for even when they are not in the career strategy - keeps the
# resume profile useful if the user rewrites the strategy.
_BASE_SKILL_VOCAB = [
    "AWS", "Azure", "GCP", "阿里云", "腾讯云", "华为云",
    "Linux", "CentOS", "Ubuntu", "RedHat", "AIX", "Windows Server",
    "Docker", "Kubernetes", "K8s", "OpenShift", "Helm",
    "Terraform", "Ansible", "CloudFormation", "Puppet", "Chef",
    "Jenkins", "GitLab", "GitHub Actions", "ArgoCD", "CI/CD",
    "Python", "Shell", "Bash", "Go", "Java", "SQL", "PowerShell",
    "Prometheus", "Grafana", "Zabbix", "ELK", "Nagios", "Datadog",
    "Nginx", "Apache", "Tomcat", "WebSphere", "WAS", "IHS", "MQ",
    "MySQL", "PostgreSQL", "Redis", "MongoDB", "Oracle",
    "EC2", "S3", "VPC", "ECS", "EKS", "ALB", "ELB", "RDS", "IAM", "Route53",
    "Git", "Jira", "Zabbix", "网络", "TCP/IP", "DNS", "负载均衡", "监控", "中间件",
]

# Character classes exclude "\n" on purpose: with "\s" the greedy tail ran past
# the end of the line and swallowed the next section heading.
_CERT_PATTERNS = re.compile(
    r"(AWS[ \w\-]{0,30}(?:Certified|认证)[ \w\-]{0,40}"
    r"|Certified Kubernetes[ \w]{0,20}"
    r"|CK[AS]\b|RHCE|RHCSA|CCNA|CCNP|PMP|ITIL|软考[一-龥]{0,10}"
    r"|系统集成项目管理[一-龥]{0,6}|信息系统项目管理[一-龥]{0,6})",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d{9}(?!\d)")
# "5年经验" but also "5年云计算与系统运维经验" / "5+ years of cloud experience".
_YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:年|\+?\s*years?)\s*(?:以上)?[^。；;\n]{0,24}?(?:经验|经历|experience)",
    re.IGNORECASE,
)
_PUNCT_STRIP = re.compile(r"[\s:：·・\-—_|/\\*#【】\[\]()（）]+")


@dataclass(slots=True)
class ParsedResume:
    raw_text: str
    file_type: str
    content_hash: str
    profile: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# text extraction
# --------------------------------------------------------------------------


def detect_file_type(filename: str) -> str:
    """Return ``pdf`` / ``docx`` / ``txt`` or raise :class:`UnsupportedFileType`."""
    name = (filename or "").lower().strip()
    for ext in SUPPORTED_EXTENSIONS:
        if name.endswith(ext):
            return ext.lstrip(".")
    raise UnsupportedFileType(
        "仅支持 .pdf / .docx / .txt 格式的简历",
        detail={"filename": filename, "supported": sorted(SUPPORTED_EXTENSIONS)},
    )


def extract_pdf_text(payload: bytes) -> str:
    try:
        import pymupdf  # PyMuPDF >= 1.24 module name
    except ImportError:  # pragma: no cover - older PyMuPDF
        import fitz as pymupdf

    chunks: list[str] = []
    with pymupdf.open(stream=payload, filetype="pdf") as doc:
        for page in doc:
            chunks.append(page.get_text("text"))
    return "\n".join(chunks)


def extract_docx_text(payload: bytes) -> str:
    import io

    from docx import Document

    document = Document(io.BytesIO(payload))
    chunks: list[str] = [p.text for p in document.paragraphs]

    # Chinese resumes are frequently laid out as a single big table.
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            # De-duplicate merged cells, which python-docx repeats per column.
            deduped: list[str] = []
            for cell in cells:
                if cell and (not deduped or deduped[-1] != cell):
                    deduped.append(cell)
            if deduped:
                chunks.append("\t".join(deduped))
    return "\n".join(chunks)


def extract_text(payload: bytes, file_type: str) -> str:
    if file_type == "pdf":
        return extract_pdf_text(payload)
    if file_type == "docx":
        return extract_docx_text(payload)
    if file_type == "txt":
        for encoding in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="replace")
    raise UnsupportedFileType(f"unsupported file type: {file_type}")


def clean_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    lines = [re.sub(r"[ \t]{2,}", " ", line).rstrip() for line in text.split("\n")]
    out: list[str] = []
    blank = 0
    for line in lines:
        if line.strip():
            blank = 0
            out.append(line.strip())
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


# --------------------------------------------------------------------------
# structured profile
# --------------------------------------------------------------------------


def _canonical_heading(line: str) -> str:
    return _PUNCT_STRIP.sub("", line).lower()


def _match_section(line: str) -> str | None:
    """Return the profile key if ``line`` looks like a section heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 24:
        return None
    canon = _canonical_heading(stripped)
    if not canon or len(canon) > 20:
        return None
    for key, aliases in _SECTION_PATTERNS:
        for alias in aliases:
            alias_canon = _canonical_heading(alias)
            if canon == alias_canon:
                return key
    return None


def split_sections(text: str) -> dict[str, list[str]]:
    """Split the resume into ``{section_key: [lines]}`` by heading detection."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        key = _match_section(line)
        if key:
            current = key
            sections.setdefault(current, [])
            continue
        if current and line.strip():
            sections[current].append(line.strip())
    return sections


def extract_skills(text: str, extra_vocab: list[str] | None = None) -> list[str]:
    """Keyword scan against the base vocabulary plus the career-strategy skills."""
    vocab: list[str] = list(_BASE_SKILL_VOCAB)
    for skill in extra_vocab or []:
        if skill not in vocab:
            vocab.append(skill)

    # Reuse the matcher the scorer uses so "WAS" and "ECS" behave identically
    # on a resume and on a JD.
    from app.services.scoring import contains_term

    found: list[str] = []
    for skill in vocab:
        if contains_term(text or "", skill) and skill not in found:
            found.append(skill)
    return found


def extract_certifications(text: str) -> list[str]:
    seen: list[str] = []
    for match in _CERT_PATTERNS.finditer(text or ""):
        value = re.sub(r"\s+", " ", match.group(0)).strip(" ,.;、，。")
        if value and value.lower() not in {s.lower() for s in seen}:
            seen.append(value)
    return seen[:20]


def estimate_years_of_experience(text: str) -> float | None:
    """Take the largest explicitly stated "N years of experience" claim."""
    values = [int(m.group(1)) for m in _YEARS_RE.finditer(text or "")]
    values = [v for v in values if 0 < v <= 40]
    return float(max(values)) if values else None


def _bullets(lines: list[str], limit: int = 25) -> list[str]:
    """Compact a section into readable entries."""
    out: list[str] = []
    for line in lines:
        cleaned = re.sub(r"^[\-*+·•▪]\s*", "", line).strip()
        if cleaned:
            out.append(cleaned)
        if len(out) >= limit:
            break
    return out


def build_profile(text: str, *, strategy_skills: list[str] | None = None) -> dict[str, Any]:
    """Deterministic structured profile. Missing sections are simply empty."""
    sections = split_sections(text)
    summary_lines = sections.get("summary") or []
    summary = " ".join(summary_lines)[:600]
    if not summary:
        # Fall back to the first substantial paragraph.
        for line in text.split("\n"):
            if len(line.strip()) >= 30:
                summary = line.strip()[:600]
                break

    contact = {
        "emails": _EMAIL_RE.findall(text)[:3],
        "phones": _PHONE_RE.findall(text)[:3],
    }

    skills = extract_skills(text, strategy_skills)
    # A dedicated skills section is the most reliable source; merge it in.
    section_skills = extract_skills("\n".join(sections.get("skills") or []), strategy_skills)

    return {
        "summary": summary,
        "work_experience": _bullets(sections.get("work_experience") or []),
        "projects": _bullets(sections.get("projects") or []),
        "skills": skills,
        "highlighted_skills": section_skills,
        "certifications": extract_certifications(text),
        "education": _bullets(sections.get("education") or [], limit=10),
        "languages": _bullets(sections.get("languages") or [], limit=6),
        "years_of_experience": estimate_years_of_experience(text),
        "contact": contact,
        "sections_detected": sorted(sections.keys()),
        "text_length": len(text),
    }


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def parse_resume(
    payload: bytes, filename: str, *, strategy_skills: list[str] | None = None
) -> ParsedResume:
    """Parse an uploaded resume into raw text + structured profile."""
    if not payload:
        raise ValidationError("上传的文件为空")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"文件过大（上限 {MAX_UPLOAD_BYTES // 1024 // 1024} MB）",
            detail={"size_bytes": len(payload)},
        )

    file_type = detect_file_type(filename)
    try:
        raw = extract_text(payload, file_type)
    except Exception as exc:  # noqa: BLE001 - surfaced as a 422 to the user
        log_event(logger, "resume.extract_failed", file_type=file_type, error=type(exc).__name__)
        raise ValidationError(
            f"无法解析该{file_type.upper()}文件，请确认文件未加密且未损坏",
            detail={"reason": type(exc).__name__},
        ) from exc

    text = clean_text(raw)
    if len(text) < 20:
        raise ValidationError(
            "未能从文件中提取到足够的文本（扫描件 PDF 需要先做 OCR）",
            detail={"extracted_chars": len(text)},
        )

    profile = build_profile(text, strategy_skills=strategy_skills)
    parsed = ParsedResume(
        raw_text=text,
        file_type=file_type,
        content_hash=sha256_bytes(payload),
        profile=profile,
    )
    log_event(
        logger,
        "resume.parsed",
        file_type=file_type,
        chars=len(text),
        skills=len(profile["skills"]),
        sections=len(profile["sections_detected"]),
        hash=parsed.content_hash[:12],
    )
    return parsed
