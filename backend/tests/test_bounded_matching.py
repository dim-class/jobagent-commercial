"""Offline acceptance: actual routes, temporary DB, mocked model only."""
from copy import deepcopy

import pytest

from app.db.session import SessionLocal
from app.models import JobSearchTask
from app.services import bounded_matching, search_task_runner
from tests.conftest import make_job_payload
from tests.test_analysis import build_result


def task(client):
    client.post('/api/tasks/search-plan/generate', json={'cities': ['上海'], 'keywords': ['AWS']})
    return client.get('/api/tasks/search-plan').json()['items'][0]['id']


def approve(client, tid, cap=3):
    q = client.get(f'/api/tasks/{tid}/auto-match/quote?cap={cap}')
    assert q.status_code == 200, q.text
    return {'match_approval': {'confirmed': True, 'cap': cap, 'fingerprint': q.json()['fingerprint']}}


def job(client, tid, number, **overrides):
    url = f'https://www.zhipin.com/job_detail/fixture{number}.html'
    result = client.post('/api/jobs', json=make_job_payload(
        title=f'AWS 工程师 {number}', source='boss', source_url=url, external_id=f'fixture{number}', **overrides))
    assert result.status_code == 201, result.text
    jid = result.json()['job']['id']
    client.post(f'/api/tasks/{tid}/candidates', json={'job_id': jid})
    return {'job_id': jid, 'canonical_url': url}


@pytest.fixture
def calls(monkeypatch):
    seen = []
    async def fake(**kwargs):
        seen.append(kwargs)
        return build_result(risk_flags=[])
    monkeypatch.setattr('app.services.job_matcher.run_job_match', fake)
    return seen


def test_quote_and_review_free_and_start_requires_current_confirmation(client, active_resume, calls):
    tid = task(client)
    approval = approve(client, tid)
    assert calls == []
    assert client.get(f'/api/tasks/{tid}/auto-match/review').json()['enabled'] is False
    approval['match_approval']['confirmed'] = False
    assert client.post(f'/api/tasks/{tid}/run/start', json=approval).status_code == 422
    approval['match_approval']['confirmed'] = True
    approval['match_approval']['fingerprint'] = '0' * 64
    assert client.post(f'/api/tasks/{tid}/run/start', json=approval).status_code == 422
    assert client.get(f'/api/tasks/search-plan/{tid}').json()['state'] == 'pending'
    assert calls == []


def test_three_jobs_canonical_pipeline_cache_repeat_and_no_human_status(client, active_resume, calls):
    tid = task(client)
    assert client.post(f'/api/tasks/{tid}/run/start', json=approve(client, tid)).status_code == 200
    for i in range(4):
        payload = job(client, tid, i)
        response = client.post(f'/api/tasks/{tid}/auto-match/step', json=payload)
        assert response.status_code == (200 if i < 3 else 422), response.text
        if i < 3:
            assert response.json()['state'] == 'done'
            assert client.post(f'/api/tasks/{tid}/auto-match/step', json=payload).json() == response.json()
    assert len(calls) == 3
    assert all(c['no_retries'] is True for c in calls)
    review = client.get(f'/api/tasks/{tid}/auto-match/review').json()
    assert (review['cap'], review['used'], review['completed']) == (3, 3, 3)
    candidates = client.get(f'/api/tasks/{tid}/candidates').json()['items']
    assert all(c['status'] == 'new' for c in candidates)
    assert client.get(f'/api/tasks/{tid}/events').json()['items'] == []


@pytest.mark.parametrize('cap', [0, 4, 20, 1.5, True])
def test_invalid_caps_rejected(client, active_resume, calls, cap):
    tid = task(client)
    response = client.post(f'/api/tasks/{tid}/run/start', json={
        'match_approval': {'confirmed': True, 'cap': cap, 'fingerprint': 'a'*64}})
    assert response.status_code == 422
    assert calls == []


def test_disabled_mode_never_spends_and_preexisting_association_excluded(client, active_resume, calls):
    tid = task(client)
    payload = job(client, tid, 1)
    assert client.post(f'/api/tasks/{tid}/auto-match/step', json=payload).status_code == 422
    assert client.post(f'/api/tasks/{tid}/run/start', json=approve(client, tid)).status_code == 200
    assert client.post(f'/api/tasks/{tid}/auto-match/step', json=payload).status_code == 422
    assert calls == []


def test_plain_search_is_backward_compatible(client, active_resume, calls):
    tid = task(client)
    assert client.post(f'/api/tasks/{tid}/run/start').status_code == 200
    payload = job(client, tid, 1)
    assert client.post(f'/api/tasks/{tid}/auto-match/step', json=payload).status_code == 422
    assert calls == []


def test_identity_and_config_drift_refuse_without_reservation(client, active_resume, calls, db):
    tid = task(client)
    client.post(f'/api/tasks/{tid}/run/start', json=approve(client, tid))
    payload = job(client, tid, 1)
    for url in [payload['canonical_url']+'?secret=x', 'https://example.com/', payload['canonical_url'].replace('fixture1', 'fixture2')]:
        assert client.post(f'/api/tasks/{tid}/auto-match/step', json={**payload, 'canonical_url': url}).status_code == 422
    active_resume.raw_text += '\nNew experience'
    db.commit()
    assert client.post(f'/api/tasks/{tid}/auto-match/step', json=payload).status_code == 422
    assert client.get(f'/api/tasks/{tid}/auto-match/review').json()['used'] == 0
    assert calls == []


def test_failure_uses_slot_is_safe_and_never_retried(client, active_resume, monkeypatch):
    seen = []
    async def fail(**kwargs):
        seen.append(1)
        raise RuntimeError('PRIVATE_CANARY')
    monkeypatch.setattr('app.services.job_matcher.run_job_match', fail)
    tid = task(client)
    client.post(f'/api/tasks/{tid}/run/start', json=approve(client, tid, 1))
    payload = job(client, tid, 1)
    for _ in range(2):
        r = client.post(f'/api/tasks/{tid}/auto-match/step', json=payload)
        assert r.status_code == 200 and r.json()['state'] == 'failed'
        assert 'PRIVATE_CANARY' not in r.text
    assert client.post(f'/api/tasks/{tid}/auto-match/step', json=job(client, tid, 2)).status_code == 422
    assert len(seen) == 1


@pytest.mark.parametrize('action', ['pause', 'cancel'])
def test_pause_cancel_during_model_preserves_state_and_budget(client, active_resume, monkeypatch, action):
    tid = task(client)
    client.post(f'/api/tasks/{tid}/run/start', json=approve(client, tid, 2))
    first, second = job(client, tid, 1), job(client, tid, 2)
    seen = []
    async def model(**kwargs):
        seen.append(1)
        with SessionLocal() as other:
            # Fresh DB sees the reservation before the model runs.
            assert len(other.get(JobSearchTask, tid).match_run_json['entries']) == len(seen)
            if len(seen) == 1:
                getattr(search_task_runner, action+'_run')(other, tid)
        return build_result(risk_flags=[])
    monkeypatch.setattr('app.services.job_matcher.run_job_match', model)
    assert client.post(f'/api/tasks/{tid}/auto-match/step', json=first).json()['state'] == 'done'
    assert client.get(f'/api/tasks/search-plan/{tid}').json()['state'] == ('paused' if action == 'pause' else 'cancelled')
    assert client.post(f'/api/tasks/{tid}/auto-match/step', json=second).status_code == 422
    if action == 'pause':
        assert client.post(f'/api/tasks/{tid}/run/resume').status_code == 200
        assert client.post(f'/api/tasks/{tid}/auto-match/step', json=first).json()['state'] == 'done'
        assert len(seen) == 1
        assert client.post(f'/api/tasks/{tid}/auto-match/step', json=second).json()['state'] == 'done'
        assert len(seen) == 2


def test_inflight_claim_replay_and_other_candidate_do_not_spend(client, active_resume, calls, db):
    tid = task(client)
    client.post(f'/api/tasks/{tid}/run/start', json=approve(client, tid))
    first, second = job(client, tid, 1), job(client, tid, 2)
    row = db.get(JobSearchTask, tid)
    ledger = deepcopy(row.match_run_json)
    ledger['entries'] = [{'job_id': first['job_id'], 'state': 'running', 'analysis_id': None, 'cache_key': 'x', 'cached': False, 'error': None}]
    bounded_matching._save(db, row, ledger)
    assert client.post(f'/api/tasks/{tid}/auto-match/step', json=first).json()['state'] == 'running'
    assert client.post(f'/api/tasks/{tid}/auto-match/step', json=second).status_code == 422
    assert calls == []


def test_high_score_conflict_is_not_direct_recommendation(client, active_resume, calls):
    tid = task(client)
    client.post(f'/api/tasks/{tid}/run/start', json=approve(client, tid))
    payload = job(client, tid, 1, raw_description='[演示数据] 工作地点：东京品川。月薪25-30万日元。AWS运维及Linux。'*8)
    assert client.post(f'/api/tasks/{tid}/auto-match/step', json=payload).status_code == 200
    item = client.get(f'/api/tasks/{tid}/auto-match/review').json()['items'][0]
    assert item['bucket'] == '待确认' and item['score'] == 86
    assert any('币种' in r for r in item['review_reasons'])


def test_stale_compare_and_swap_cannot_reserve_twice(client, active_resume):
    tid = task(client)
    client.post(f'/api/tasks/{tid}/run/start', json=approve(client, tid))
    with SessionLocal() as a, SessionLocal() as b:
        ra, rb = a.get(JobSearchTask, tid), b.get(JobSearchTask, tid)
        bounded_matching._save(a, ra, deepcopy(ra.match_run_json), require_running=True)
        with pytest.raises(Exception, match='并发'):
            bounded_matching._save(b, rb, deepcopy(rb.match_run_json), require_running=True)


def test_duplicate_start_cannot_overwrite_approval(client, active_resume):
    tid = task(client)
    with SessionLocal() as a, SessionLocal() as b:
        stale = b.get(JobSearchTask, tid)
        assert stale.run_status.value == 'pending'
        search_task_runner.start_run(a, tid)
        with pytest.raises(Exception, match='启动'):
            search_task_runner.start_run(b, tid, match_approval={'cap': 3})
        b.refresh(stale)
        assert stale.match_run_json is None


def test_existing_global_cache_reused_without_new_model_call(client, active_resume, calls):
    tid = task(client)
    client.post(f'/api/tasks/{tid}/run/start', json=approve(client, tid))
    payload = job(client, tid, 1)
    client.post(f'/api/tasks/{tid}/auto-match/step', json=payload)
    assert len(calls) == 1
    client.post('/api/tasks/search-plan/generate', json={'cities': ['北京'], 'keywords': ['Cache']})
    tid2 = client.get('/api/tasks/search-plan').json()['items'][-1]['id']
    client.post(f'/api/tasks/{tid2}/run/start', json=approve(client, tid2))
    client.post(f'/api/tasks/{tid2}/candidates', json={'job_id': payload['job_id']})
    r = client.post(f'/api/tasks/{tid2}/auto-match/step', json=payload)
    assert r.status_code == 200 and r.json()['cached'] is True
    assert len(calls) == 1
