"""Local-only crop validation/OCR/parser/intake tests. No AI or recruitment network."""
import base64
import json
import os
import struct
import subprocess
import zlib

import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from app.main import app
from app.models import Job, ApplicationEvent
from app.services import salary_ocr


def png(width=100, height=30):
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    return base64.b64encode(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * width * 3) * height)) + chunk(b'IEND', b'')).decode()


@pytest.mark.parametrize('raw,expected', [('8-13K', '8-13K'), ('8 - 13 k', '8-13K'), ('15-25K·15薪', '15-25K·15薪'), ('1.5-2.5万', '1.5-2.5万')])
def test_strict_parser(raw, expected):
    assert salary_ocr.parse_salary(raw) == expected


@pytest.mark.parametrize('raw', ['8-1OK', '8·13K', '8一13K', '13-8K', '0-13K', '8-999K', '8-13', '8-13K 20-30K', '8-13K·99薪', '8-13K·15', '职位8-13K', '\ue039-\ue032\ue033K'])
def test_uncertain_values_are_not_guessed(raw):
    assert salary_ocr.parse_salary(raw) is None


@pytest.mark.parametrize('image', ['oops', '', png(801, 30), png(100, 161), 'a' * 360000], ids=['invalid', 'empty', 'wide', 'tall', 'oversize'])
def test_bad_images_never_launch_a_process(image, monkeypatch):
    monkeypatch.setattr(salary_ocr.subprocess, 'run', lambda *a, **k: pytest.fail('must not launch'))
    with pytest.raises(ValueError):
        salary_ocr.recognize_salary(image)


@pytest.mark.skipif(os.name != 'nt', reason='Windows worker contract')
@pytest.mark.parametrize('texts,expected', [(['8-13K', '8-13K'], '8-13K'), (['8-13K', '8-18K'], None), (['8-13K'], None), (['8-13K', '??'], None)])
def test_worker_agreement_and_stdin_only(texts, expected, monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        assert kwargs['timeout'] == 20 and kwargs['creationflags'] == subprocess.CREATE_NO_WINDOW
        assert json.loads(kwargs['input']) == {'image': png()}
        assert png() not in ' '.join(args)
        return subprocess.CompletedProcess(args, 0, json.dumps({'texts': texts}), '')
    monkeypatch.setattr(salary_ocr.subprocess, 'run', run)
    result = salary_ocr.recognize_salary(png())
    assert result['salary_text'] == expected
    assert len(calls) == 1 and not salary_ocr._LOCK.locked()


@pytest.mark.skipif(os.name != 'nt', reason='Windows worker contract')
def test_timeout_is_unknown_and_releases_lock(monkeypatch):
    def run(*a, **k):
        raise subprocess.TimeoutExpired('local-ocr', 20)
    monkeypatch.setattr(salary_ocr.subprocess, 'run', run)
    assert salary_ocr.recognize_salary(png())['salary_text'] is None
    assert not salary_ocr._LOCK.locked()
    salary_ocr._LOCK.acquire()
    try:
        assert salary_ocr.recognize_salary(png())['reason'] == 'busy'
    finally:
        salary_ocr._LOCK.release()


def test_ocr_route_is_read_only_and_rejects_page_origins(client, db, monkeypatch):
    from app.api.routes import extension
    calls = []
    def recognize(image):
        calls.append(image)
        return {'salary_text': '8-13K', 'source': 'windows_local_ocr', 'reason': 'two_scale_agreement'}
    monkeypatch.setattr(extension, 'recognize_salary', recognize)
    response = client.post('/api/extension/salary-ocr', json={'image': png()}, headers={'Origin': 'chrome-extension://' + 'a' * 32})
    assert response.status_code == 200 and response.json()['salary_text'] == '8-13K'
    assert db.scalars(select(Job)).all() == []
    assert db.scalars(select(ApplicationEvent)).all() == []
    assert client.post('/api/extension/salary-ocr', json={'image': png()}, headers={'Origin': 'https://www.zhipin.com'}).status_code == 403
    with TestClient(app, client=('192.0.2.5', 123)) as remote:
        assert remote.post('/api/extension/salary-ocr', json={'image': png()}).status_code == 403
    assert len(calls) == 1


@pytest.mark.parametrize('payload', [{'image': 3}, {'image': 'a', 'url': 'https://example.com'}, [], None])
def test_payload_shape_rejected(client, payload):
    assert client.post('/api/extension/salary-ocr', json=payload).status_code == 422


def test_large_request_is_bounded(client):
    assert client.post('/api/extension/salary-ocr', content=b'x' * 400000).status_code == 422


def test_ocr_provenance_uses_existing_intake_note(client, db):
    candidate = {'title': '薪资测试岗位', 'company': '本地测试', 'salary_text': '8-13K',
                 'description': '岗位职责：维护云计算平台与网络系统，负责系统监控、故障排查与日常运维。任职要求：熟悉 Linux 和云平台技术。' * 3,
                 'source_url': 'https://www.zhipin.com/job_detail/local-ocr-fixture.html',
                 'external_id': 'local-ocr-fixture', 'matched_selectors': {'salary_text': 'local_screenshot_ocr (two-scale agreement)'}}
    response = client.post('/api/extension/jobs/import', json={'confirmed': True, 'candidate': candidate})
    assert response.status_code == 200, response.text
    assert db.get(Job, response.json()['job_id']).salary_text == '8-13K'
    notes = db.scalars(select(ApplicationEvent)).all()
    assert len(notes) == 1 and 'OCR' in notes[0].notes and '人工核对' in notes[0].notes
