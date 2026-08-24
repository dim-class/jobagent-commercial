"""Recruiter conversation endpoints (v0.5).

    GET/POST   /api/recruiter-conversations
    GET/PATCH  /api/recruiter-conversations/{id}
    GET        /api/recruiter-conversations/{id}/messages
    POST       /api/recruiter-conversations/{id}/messages/text
    POST       /api/recruiter-conversations/{id}/messages/image
    POST       /api/recruiter-conversations/{id}/messages/{mid}/analyze
    POST       /api/recruiter-conversations/{id}/messages/{mid}/reanalyze-smart
    POST       /api/recruiter-conversations/{id}/mark-sent | follow-up | close

Every route is thin: persistence lives in ``services.recruiter_conversations``
and analysis in ``services.recruiter_message_analyzer``. No route reads an
inbox, sends a message, or writes ``Job.status``.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger, log_event
from app.db.session import get_db
from app.models import ConversationStatus, MessageDirection, RecruiterConversation
from app.schemas.application import ApplicationEventOut
from app.schemas.recruiter import (
    AnalysisOut,
    AnalyzeRequest,
    CloseRequest,
    ConversationActionOut,
    ConversationCreate,
    ConversationDetailOut,
    ConversationStage,
    ConversationSummaryOut,
    ConversationUpdate,
    FollowUpRequest,
    InboxResponse,
    InboxSummary,
    InputMode,
    LanguagePreference,
    MarkSentRequest,
    MessageCreatedOut,
    MessageTextIn,
    RecruiterMessageAnalysisResult,
    RecruiterMessageOut,
    Sentiment,
)
from app.services import quick_capture, recruiter_conversations as convo
from app.services import recruiter_message_analyzer as analyzer
from app.services.timezones import is_local_today, local_now

logger = get_logger(__name__)
router = APIRouter(prefix="/api/recruiter-conversations", tags=["recruiter"])

PREVIEW_CHARS = 160


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------


def _message_out(message) -> RecruiterMessageOut:  # noqa: ANN001
    row = analyzer.latest_analysis(message)
    return RecruiterMessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        direction=message.direction,
        raw_text=message.raw_text,
        source_message_time_text=message.source_message_time_text,
        captured_at=message.captured_at,
        created_at=message.created_at,
        analysis=analyzer.result_of(row) if row else None,
        analyzed_at=row.created_at if row else None,
        analysis_model=row.model if row else None,
    )


def _latest_recruiter_analysis(
    conversation: RecruiterConversation,
) -> RecruiterMessageAnalysisResult | None:
    """The reading of the newest recruiter turn - what the UI summarises."""
    best = None
    best_key = None
    for message in conversation.messages:
        if message.direction is not MessageDirection.recruiter:
            continue
        row = analyzer.latest_analysis(message)
        if row is None:
            continue
        key = (message.created_at, message.id)
        if best_key is None or key > best_key:
            best, best_key = row, key
    return analyzer.result_of(best) if best else None


def _summary_out(conversation: RecruiterConversation) -> ConversationSummaryOut:
    newest = convo.latest_message(conversation)
    analysis = _latest_recruiter_analysis(conversation)
    job = conversation.job

    return ConversationSummaryOut(
        id=conversation.id,
        job_id=conversation.job_id,
        job_title=job.title if job else None,
        job_company=job.company if job else None,
        source=conversation.source,
        recruiter_name=conversation.recruiter_name,
        company=conversation.company,
        title=conversation.title,
        status=convo.effective_status(conversation),
        close_reason=conversation.close_reason,
        last_message_at=conversation.last_message_at,
        next_action_at=conversation.next_action_at,
        created_at=conversation.created_at,
        message_count=len(conversation.messages),
        latest_preview=(newest.raw_text if newest else "").replace("\n", " ")[:PREVIEW_CHARS],
        latest_direction=newest.direction if newest else None,
        sentiment=analysis.sentiment if analysis else None,
        stage=analysis.conversation_stage if analysis else None,
        needs_reply=bool(analysis.needs_reply) if analysis else False,
        action_item_count=len(analysis.action_items) if analysis else 0,
        summary=analysis.summary if analysis else "",
    )


def _detail_out(db: Session, conversation: RecruiterConversation) -> ConversationDetailOut:
    base = _summary_out(conversation).model_dump()
    events: list[ApplicationEventOut] = []
    if conversation.job is not None:
        events = [
            ApplicationEventOut(
                id=e.id,
                event_type=e.event_type.value,
                notes=e.notes,
                metadata_json=e.metadata_json or {},
                created_at=e.created_at,
            )
            for e in sorted(conversation.job.events, key=lambda e: (e.created_at, e.id))
        ]

    ordered = sorted(conversation.messages, key=lambda m: (m.created_at, m.id))
    return ConversationDetailOut(
        **base,
        messages=[_message_out(m) for m in ordered],
        latest_analysis=_latest_recruiter_analysis(conversation),
        job_events=events,
        suggests_recruiter_reply_event=convo.suggests_recruiter_reply_event(conversation),
    )


# --------------------------------------------------------------------------
# inbox
# --------------------------------------------------------------------------


@router.get("", response_model=InboxResponse)
def list_conversations(
    db: Session = Depends(get_db),
    status: ConversationStatus | None = Query(default=None),
    job_id: int | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> InboxResponse:
    """The HR沟通 inbox. This is a local record, not a connected mailbox."""
    settings = get_settings()
    conversations = convo.list_conversations(db)
    items = [_summary_out(c) for c in conversations]

    filtered = items
    if status is not None:
        filtered = [i for i in filtered if i.status is status]
    if job_id is not None:
        filtered = [i for i in filtered if i.job_id == job_id]
    if keyword:
        needle = keyword.strip().lower()
        filtered = [
            i
            for i in filtered
            if needle
            in " ".join(
                filter(None, [i.company, i.title, i.recruiter_name, i.latest_preview])
            ).lower()
        ]

    received_today = 0
    replied_today = 0
    for conversation in conversations:
        for message in conversation.messages:
            if not is_local_today(message.created_at):
                continue
            if message.direction is MessageDirection.recruiter:
                received_today += 1
            else:
                replied_today += 1

    summary = InboxSummary(
        needs_reply=sum(1 for i in items if i.status is ConversationStatus.needs_reply),
        received_today=received_today,
        replied_today=replied_today,
        interview_scheduling=sum(
            1 for i in items if i.stage is ConversationStage.interview_scheduling
        ),
        follow_up_due=sum(1 for i in items if i.status is ConversationStatus.follow_up_due),
        waiting_recruiter=sum(
            1 for i in items if i.status is ConversationStatus.waiting_recruiter
        ),
        closed=sum(1 for i in items if i.status is ConversationStatus.closed),
        timezone=settings.report_timezone,
    )

    # Newest activity first; threads awaiting the user sort above the rest.
    order = {
        ConversationStatus.needs_reply: 0,
        ConversationStatus.follow_up_due: 1,
        ConversationStatus.waiting_recruiter: 2,
        ConversationStatus.no_action: 3,
        ConversationStatus.closed: 4,
    }
    filtered.sort(
        key=lambda i: (
            order.get(i.status, 9),
            -(i.last_message_at or i.created_at).timestamp(),
        )
    )
    return InboxResponse(items=filtered, total=len(filtered), summary=summary)


@router.post("", response_model=ConversationDetailOut, status_code=201)
def create_conversation(
    payload: ConversationCreate, db: Session = Depends(get_db)
) -> ConversationDetailOut:
    return _detail_out(db, convo.create_conversation(db, payload))


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(conversation_id: int, db: Session = Depends(get_db)) -> ConversationDetailOut:
    return _detail_out(db, convo.load_conversation(db, conversation_id))


@router.patch("/{conversation_id}", response_model=ConversationDetailOut)
def patch_conversation(
    conversation_id: int, payload: ConversationUpdate, db: Session = Depends(get_db)
) -> ConversationDetailOut:
    return _detail_out(db, convo.update_conversation(db, conversation_id, payload))


@router.get("/{conversation_id}/messages", response_model=list[RecruiterMessageOut])
def list_messages(conversation_id: int, db: Session = Depends(get_db)) -> list[RecruiterMessageOut]:
    conversation = convo.load_conversation(db, conversation_id)
    ordered = sorted(conversation.messages, key=lambda m: (m.created_at, m.id))
    return [_message_out(m) for m in ordered]


# --------------------------------------------------------------------------
# message intake
# --------------------------------------------------------------------------


async def _maybe_analyze(
    db: Session,
    message,  # noqa: ANN001
    *,
    language: LanguagePreference,
    enabled: bool,
) -> tuple[bool, bool, str | None]:
    """Returns ``(ai_used, ai_available, ai_error)``. Never raises upward."""
    settings = get_settings()
    available = settings.openai_configured
    if not enabled or not available:
        return False, available, None
    if message.direction is not MessageDirection.recruiter:
        return False, available, None  # our own text needs no analysis

    from app.core.errors import AppError

    try:
        await analyzer.analyze_message(db, message, language=language)
    except AppError as exc:
        log_event(logger, "recruiter.analysis_failed", message_id=message.id, code=exc.code)
        return False, available, exc.message
    return True, available, None


@router.post("/{conversation_id}/messages/text", response_model=MessageCreatedOut)
async def add_text_message(
    conversation_id: int, payload: MessageTextIn, db: Session = Depends(get_db)
) -> MessageCreatedOut:
    """Store a pasted message (or transcript) and optionally analyze it."""
    conversation = convo.load_conversation(db, conversation_id)
    intake = convo.add_text_intake(
        db,
        conversation,
        text=payload.text,
        direction=payload.direction,
        input_mode=payload.input_mode,
        source_message_time_text=payload.source_message_time_text,
    )

    ai_used, ai_available, ai_error = await _maybe_analyze(
        db, intake.message, language=payload.language, enabled=payload.analyze
    )
    refreshed = convo.load_conversation(db, conversation_id)
    message = convo.get_message(db, conversation_id, intake.message.id)

    return MessageCreatedOut(
        message=_message_out(message),
        duplicate=intake.duplicate,
        parsed_message_count=intake.parsed_count,
        warnings=intake.warnings,
        ai_used=ai_used,
        ai_available=ai_available,
        ai_error=ai_error,
        conversation=_detail_out(db, refreshed),
    )


@router.post("/{conversation_id}/messages/image", response_model=MessageCreatedOut)
async def add_image_message(
    conversation_id: int,
    file: UploadFile = File(..., description="对话截图 PNG / JPG / JPEG / WEBP"),
    language: LanguagePreference = Form(default=LanguagePreference.auto),
    analyze: bool = Form(default=True),
    db: Session = Depends(get_db),
) -> MessageCreatedOut:
    """Read a conversation screenshot.

    Validation, size limits and hashing are the v0.3 Quick Capture helpers -
    not re-implemented here. The image is dropped after extraction; only text
    is stored.
    """
    conversation = convo.load_conversation(db, conversation_id)
    payload = await file.read()

    settings = get_settings()
    mime, digest = quick_capture.validate_image(
        payload, content_type=file.content_type, filename=file.filename, settings=settings
    )
    if not settings.quick_capture_ai_extraction:
        raise quick_capture.AIExtractionDisabledError(
            "截图识别需要 AI，但它已在设置中关闭（QUICK_CAPTURE_AI_EXTRACTION=false）。"
            "请改用文字粘贴。",
            detail={"setting": "QUICK_CAPTURE_AI_EXTRACTION"},
        )
    if not settings.openai_configured:
        raise quick_capture.AIExtractionDisabledError(
            "截图识别需要 OpenAI API，但尚未配置 OPENAI_API_KEY。请改用文字粘贴。",
            detail={"env_var": "OPENAI_API_KEY"},
        )

    from app.agents.recruiter_agent import run_vision_transcript

    transcript, _model = await run_vision_transcript(payload, mime, settings=settings)
    # The bytes go out of scope here - nothing is written to disk or logged.
    log_event(
        logger,
        "recruiter.image_parsed",
        conversation_id=conversation.id,
        hash=digest[:12],
        messages=len(transcript.messages),
        partial=transcript.partial,
    )

    parsed = convo.transcript_to_messages(
        [m.model_dump() for m in transcript.messages], default=MessageDirection.recruiter
    )
    if not parsed:
        from app.core.errors import ValidationError

        raise ValidationError("截图中没有识别到对话内容，请改用文字粘贴。")

    stored = []
    for item in parsed:
        stored.append(
            convo.add_message(
                db, conversation, text=item.text, direction=item.direction, commit=False
            )
        )
    db.commit()
    for message, _ in stored:
        db.refresh(message)

    target = next(
        (m for m, _ in reversed(stored) if m.direction is MessageDirection.recruiter),
        stored[-1][0],
    )
    warnings = list(transcript.notes)
    if transcript.partial:
        warnings.append("截图可能只包含部分对话")

    ai_used, ai_available, ai_error = await _maybe_analyze(
        db, target, language=language, enabled=analyze
    )
    refreshed = convo.load_conversation(db, conversation_id)

    return MessageCreatedOut(
        message=_message_out(convo.get_message(db, conversation_id, target.id)),
        duplicate=all(dup for _, dup in stored),
        parsed_message_count=len(stored),
        warnings=warnings,
        ai_used=ai_used,
        ai_available=ai_available,
        ai_error=ai_error,
        conversation=_detail_out(db, refreshed),
    )


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------


async def _analyze(
    db: Session,
    conversation_id: int,
    message_id: int,
    payload: AnalyzeRequest,
    *,
    smart: bool,
) -> AnalysisOut:
    message = convo.get_message(db, conversation_id, message_id)
    outcome = await analyzer.analyze_message(
        db,
        message,
        language=payload.language,
        force=payload.force,
        use_smart_model=smart,
    )
    return AnalysisOut(
        message_id=message.id,
        result=outcome.result,
        model=outcome.row.model,
        prompt_version=outcome.row.prompt_version,
        cached=outcome.cached,
        created_at=outcome.row.created_at,
        deterministic_signals=outcome.signals,
    )


@router.post("/{conversation_id}/messages/{message_id}/analyze", response_model=AnalysisOut)
async def analyze_message(
    conversation_id: int,
    message_id: int,
    payload: AnalyzeRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    return await _analyze(
        db, conversation_id, message_id, payload or AnalyzeRequest(), smart=False
    )


@router.post(
    "/{conversation_id}/messages/{message_id}/reanalyze-smart", response_model=AnalysisOut
)
async def reanalyze_smart(
    conversation_id: int,
    message_id: int,
    payload: AnalyzeRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> AnalysisOut:
    """Re-read with ``OPENAI_MODEL_SMART``. Never called automatically."""
    return await _analyze(
        db, conversation_id, message_id, payload or AnalyzeRequest(), smart=True
    )


# --------------------------------------------------------------------------
# human decisions
# --------------------------------------------------------------------------


@router.post("/{conversation_id}/mark-sent", response_model=ConversationActionOut)
def mark_sent(
    conversation_id: int, payload: MarkSentRequest, db: Session = Depends(get_db)
) -> ConversationActionOut:
    """Record that the user sent a reply themselves.

    Requires ``confirmed=true`` and stores the final, possibly edited text -
    JobAgent did not send anything and must not imply otherwise.
    """
    conversation, _message = convo.mark_sent(db, conversation_id, payload)
    return ConversationActionOut(
        conversation=_detail_out(db, conversation), message="已记录你发送的回复"
    )


@router.post("/{conversation_id}/follow-up", response_model=ConversationActionOut)
def schedule_follow_up(
    conversation_id: int,
    payload: FollowUpRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> ConversationActionOut:
    conversation = convo.schedule_follow_up(db, conversation_id, payload or FollowUpRequest())
    return ConversationActionOut(
        conversation=_detail_out(db, conversation), message="已安排跟进时间"
    )


@router.post("/{conversation_id}/close", response_model=ConversationActionOut)
def close_conversation(
    conversation_id: int,
    payload: CloseRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> ConversationActionOut:
    conversation, rejected = convo.close_conversation(
        db, conversation_id, payload or CloseRequest()
    )
    return ConversationActionOut(
        conversation=_detail_out(db, conversation),
        message="已结束沟通" + ("，并已记录职位拒绝" if rejected else ""),
    )
