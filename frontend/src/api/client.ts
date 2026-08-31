// Thin typed wrapper over fetch.
//
// There is no API key here and there never should be: the OpenAI key lives on
// the backend only. Requests go to the Vite dev proxy (/api -> 127.0.0.1:8000).

import type {
  SearchPlanTask,
  SalaryBackfillPlan,
  SearchKeywordAnalytics,
  SalaryBackfillRun,
  QuickSearchPrepareResponse,
  SearchPlanOptions,
  AutoMatchReview,
  AnalysisResponse,
  AppSettings,
  ApplyProposalResponse,
  ApplicationEventOut,
  ApplicationApprovalOut,
  ApplicationMetrics,
  ConversationActionOut,
  ConversationDetailOut,
  BrowserStartResponse,
  BrowserStatus,
  CaptureResponse,
  ConfirmResponse,
  CurrentPage,
  BatchAnalyzePlan,
  BatchAnalyzeResponse,
  CareerAnalyticsResult,
  CareerStrategy,
  CareerStrategyResponse,
  DashboardAnalytics,
  DashboardSummary,
  DismissProposalResponse,
  HealthResponse,
  JobCreatePayload,
  JobDetail,
  JobListResponse,
  FollowUpPreset,
  InboxResponse,
  InputMode,
  InterviewRound,
  JobImportCandidate,
  JobStatus,
  LaterPreset,
  ParseResponse,
  QuickCaptureSettings,
  LanguagePreference,
  MessageCreatedOut,
  MessageDirection,
  QueueResponse,
  RecommendationsResponse,
  RecruiterAnalysisOut,
  RecruiterMessageOut,
  ResponseType,
  InterviewAnalyticsResult,
  InterviewBoardResponse,
  InterviewProcessListResponse,
  InterviewProcessOut,
  CounterPayload,
  JobResumeAnalyses,
  MarkAppliedPayload,
  OfferActionResponse,
  OfferAnalyticsResult,
  OfferBoardResponse,
  OfferComparisonResponse,
  OfferCreatePayload,
  OfferListResponse,
  OfferOut,
  ParsedSalaryOut,
  RevisionCreatePayload,
  ProcessActionResponse,
  RoundActionResponse,
  RoundCompletePayload,
  RoundCreatePayload,
  SuggestionListResponse,
  UpcomingInterviewsResponse,
  WithdrawReason,
  ResumeAnalyticsResult,
  ResumeComparisonResponse,
  ResumeDetail,
  ResumeListItem,
  ResumeUsage,
  AssessmentOut,
  AssessmentUpdate,
  CompetingOffersResponse,
  DecisionComparisonOut,
  DecisionProfileOut,
  DecisionProfileUpdate,
  NegotiationPositionOut,
  SnapshotDetail,
  SnapshotListResponse,
  UnattributedResponse,
  ResumeUploadResponse,
  ConsoleAttentionOut,
  OrchestrationEventCreatePayload,
  OrchestrationEventListResponse,
  OrchestrationEventOut,
  TaskCandidateListResponse,
  TaskCreatePayload,
  TaskListResponse,
  TaskMatchPlanOut,
  TaskMatchRunResponse,
  CrossTaskMatchPlanOut,
  CrossTaskMatchRunResponse,
  TaskOut,
  TimeWindow,
  Verdict,
  WorkflowResponse,
} from '@/types'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: Record<string, unknown>

  constructor(status: number, code: string, message: string, detail: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }

  /** The id of the job that already exists, when this is a duplicate error. */
  get existingJobId(): number | null {
    const value = this.detail?.existing_job_id
    return typeof value === 'number' ? value : null
  }

  get isConfigurationError(): boolean {
    return this.code === 'configuration_error'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiError(0, 'network_error', '无法连接后端服务，请确认后端已在 127.0.0.1:8000 启动。')
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
  }

  if (!response.ok) {
    const body = (payload ?? {}) as { code?: string; message?: string; detail?: Record<string, unknown> }
    throw new ApiError(
      response.status,
      body.code ?? 'http_error',
      body.message ?? `请求失败（HTTP ${response.status}）`,
      body.detail ?? {},
    )
  }

  return payload as T
}

type QueryValue = string | number | boolean | null | undefined

function query(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

export interface ConversationCreatePayload {
  job_id?: number | null
  source?: string
  recruiter_name?: string | null
  company?: string | null
  title?: string | null
}

export interface QueueFilters {
  [key: string]: QueryValue
  city?: string
  verdict?: Verdict | ''
  min_score?: number
  keyword?: string
  status?: JobStatus | ''
  source?: string
  include_maybe?: boolean
  include_decided?: boolean
  include_early_career?: boolean
  sort?: 'recommended' | 'score' | 'newest' | 'salary'
  limit?: number
  offset?: number
}

export interface JobFilters {
  [key: string]: QueryValue
  city?: string
  min_score?: number
  verdict?: Verdict | ''
  status?: JobStatus | ''
  keyword?: string
  analyzed?: boolean
  early_career_cleanup?: boolean
  sort?: 'score' | 'created_at' | 'company'
  limit?: number
  offset?: number
}

export const api = {
  health: (signal?: AbortSignal) => request<HealthResponse>('/health', { signal }),
  listSearchPlan: (signal?: AbortSignal) => request<{ items: SearchPlanTask[] }>('/api/tasks/search-plan', { signal }),
  getSearchPlanOptions: (signal?: AbortSignal) =>
    request<SearchPlanOptions>('/api/tasks/search-plan/options', { signal }),
  generateSearchPlan: (city: string, keyword: string) =>
    request<{ created: number; skipped: number; total: number }>('/api/tasks/search-plan/generate', {
      method: 'POST', body: JSON.stringify({ cities: [city], keywords: [keyword] }),
    }),
  prepareResumeSearch: (cities: string[], targetCount: number) =>
    request<QuickSearchPrepareResponse>('/api/tasks/search-plan/quick-prepare', {
      method: 'POST', body: JSON.stringify({ cities, target_count: targetCount }),
    }),
  // Free: a local aggregate over analyses you already paid for.
  searchKeywordAnalytics: () =>
    request<SearchKeywordAnalytics>('/api/analytics/search-keywords'),
  getSalaryBackfillPlan: () => request<SalaryBackfillPlan>('/api/jobs/salary-backfill/plan'),
  getActiveSalaryBackfillRun: () =>
    request<SalaryBackfillRun | null>('/api/jobs/salary-backfill/runs/active'),
  createSalaryBackfillRun: (plan: SalaryBackfillPlan) =>
    request<SalaryBackfillRun>('/api/jobs/salary-backfill/runs', {
      method: 'POST', body: JSON.stringify({ confirmed: true, fingerprint: plan.fingerprint,
        job_ids: plan.items.map(item => item.job_id) }),
    }),
  getSalaryBackfillRun: (id: number) =>
    request<SalaryBackfillRun>(`/api/jobs/salary-backfill/runs/${id}`),
  authorizeSalaryBackfillRemaining: (id: number) =>
    request<SalaryBackfillRun>(`/api/jobs/salary-backfill/runs/${id}/authorize-remaining`, {
      method: 'POST', body: JSON.stringify({ confirmed: true }),
    }),

  // --- jobs ---
  listJobs: (filters: JobFilters = {}) => request<JobListResponse>(`/api/jobs${query(filters)}`),
  getJob: (id: number) => request<JobDetail>(`/api/jobs/${id}`),
  createJob: (payload: JobCreatePayload) =>
    request<{ job: JobDetail; duplicate: boolean; message: string }>('/api/jobs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateJob: (id: number, payload: { status?: JobStatus; note?: string; city?: string }) =>
    request<JobDetail>(`/api/jobs/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteJob: (id: number) =>
    request<{ message: string }>(`/api/jobs/${id}`, { method: 'DELETE' }),

  // --- analysis ---
  analyzeJob: (id: number, force = false) =>
    request<AnalysisResponse>(`/api/jobs/${id}/analyze`, {
      method: 'POST',
      body: JSON.stringify({ force, use_smart_model: false }),
    }),
  reanalyzeSmart: (id: number, force = false) =>
    request<AnalysisResponse>(`/api/jobs/${id}/reanalyze-smart${query({ force })}`, {
      method: 'POST',
    }),
  getAnalysis: (id: number) => request<AnalysisResponse>(`/api/jobs/${id}/analysis`),
  // Read-only: what analyzing this exact selection would cost. Spends nothing.
  analyzeBatchPlan: (jobIds: number[]) =>
    request<BatchAnalyzePlan>('/api/jobs/analyze-batch/plan', {
      method: 'POST',
      body: JSON.stringify({ job_ids: jobIds }),
    }),
  analyzeBatch: (force = false, jobIds?: number[]) =>
    request<BatchAnalyzeResponse>('/api/jobs/analyze-batch', {
      method: 'POST',
      body: JSON.stringify({
        force,
        use_smart_model: false,
        ...(jobIds ? { job_ids: jobIds } : {}),
      }),
    }),

  // --- resumes ---
  listResumes: (includeArchived = false) =>
    request<ResumeListItem[]>(`/api/resumes${query({ include_archived: includeArchived })}`),
  getActiveResume: () => request<ResumeDetail>('/api/resumes/active'),
  uploadResume: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ResumeUploadResponse>('/api/resumes/upload', { method: 'POST', body: form })
  },
  activateResume: (id: number) =>
    request<ResumeDetail>(`/api/resumes/${id}/activate`, { method: 'POST' }),

  // --- recruiter conversations (v0.5) ---
  // A local record of messages the user pasted. There is no inbox connection
  // and no send capability - drafts are copied by the user and sent elsewhere.
  recruiterInbox: (params: { status?: string; job_id?: number; keyword?: string } = {}) =>
    request<InboxResponse>(`/api/recruiter-conversations${query(params)}`),
  createConversation: (payload: ConversationCreatePayload) =>
    request<ConversationDetailOut>('/api/recruiter-conversations', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getConversation: (id: number) =>
    request<ConversationDetailOut>(`/api/recruiter-conversations/${id}`),
  updateConversation: (id: number, payload: Partial<ConversationCreatePayload>) =>
    request<ConversationDetailOut>(`/api/recruiter-conversations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  addRecruiterMessage: (
    id: number,
    text: string,
    options: {
      direction?: MessageDirection
      input_mode?: InputMode
      language?: LanguagePreference
      analyze?: boolean
    } = {},
  ) =>
    request<MessageCreatedOut>(`/api/recruiter-conversations/${id}/messages/text`, {
      method: 'POST',
      body: JSON.stringify({ text, ...options }),
    }),
  addRecruiterImage: (id: number, file: File, language: LanguagePreference = 'auto') => {
    const form = new FormData()
    form.append('file', file)
    form.append('language', language)
    return request<MessageCreatedOut>(`/api/recruiter-conversations/${id}/messages/image`, {
      method: 'POST',
      body: form,
    })
  },
  analyzeRecruiterMessage: (
    id: number,
    messageId: number,
    options: { force?: boolean; language?: LanguagePreference } = {},
  ) =>
    request<RecruiterAnalysisOut>(
      `/api/recruiter-conversations/${id}/messages/${messageId}/analyze`,
      { method: 'POST', body: JSON.stringify(options) },
    ),
  reanalyzeRecruiterMessageSmart: (id: number, messageId: number) =>
    request<RecruiterAnalysisOut>(
      `/api/recruiter-conversations/${id}/messages/${messageId}/reanalyze-smart`,
      { method: 'POST', body: JSON.stringify({}) },
    ),
  markReplySent: (id: number, finalText: string, recordJobEvent = true) =>
    request<ConversationActionOut>(`/api/recruiter-conversations/${id}/mark-sent`, {
      method: 'POST',
      body: JSON.stringify({
        confirmed: true,
        final_text: finalText,
        record_job_event: recordJobEvent,
      }),
    }),
  scheduleFollowUp: (id: number, preset: FollowUpPreset, nextActionAt?: string) =>
    request<ConversationActionOut>(`/api/recruiter-conversations/${id}/follow-up`, {
      method: 'POST',
      body: JSON.stringify({ preset, next_action_at: nextActionAt || null }),
    }),
  closeConversation: (id: number, reason: string, alsoRecordJobRejection = false) =>
    request<ConversationActionOut>(`/api/recruiter-conversations/${id}/close`, {
      method: 'POST',
      body: JSON.stringify({ reason, also_record_job_rejection: alsoRecordJobRejection }),
    }),
  listRecruiterMessages: (id: number) =>
    request<RecruiterMessageOut[]>(`/api/recruiter-conversations/${id}/messages`),

  // --- application queue (v0.4) ---
  // Every one of these records something the HUMAN did outside JobAgent.
  // Nothing here contacts a recruitment site.
  applicationQueue: (filters: QueueFilters = {}) =>
    request<QueueResponse>(`/api/application-queue${query(filters)}`),
  applicationMetrics: () => request<ApplicationMetrics>('/api/application-queue/metrics'),
  applicationEvents: (jobId: number) =>
    request<ApplicationEventOut[]>(`/api/jobs/${jobId}/application-events`),
  createApplicationApproval: (jobId: number, resumeId: number) =>
    request<ApplicationApprovalOut>(`/api/application-approvals/jobs/${jobId}`, {
      method: 'POST',
      body: JSON.stringify({
        resume_id: resumeId,
        answers_source: 'boss_dynamic_unverified',
        confirmed: true,
      }),
    }),
  applicationApproval: (approvalId: number) =>
    request<ApplicationApprovalOut>(`/api/application-approvals/${approvalId}`),
  abandonApplicationAttempt: (approvalId: number) =>
    request<ApplicationApprovalOut>(`/api/application-approvals/${approvalId}/abandon`, {
      method: 'POST',
      body: JSON.stringify({ confirmed: true }),
    }),
  // The resume passed here is the one the user says they actually submitted.
  // Leaving it undefined records `unknown` - the backend never substitutes the
  // active analysis resume.
  markApplied: (
    jobId: number,
    note?: string,
    resume?: { resumeId: number | null; usage: ResumeUsage },
  ) => {
    const payload: MarkAppliedPayload = { confirmed: true, note: note || null }
    if (resume) {
      payload.resume_id = resume.resumeId
      payload.resume_usage = resume.usage
    }
    return request<WorkflowResponse>(`/api/jobs/${jobId}/mark-applied`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  // 补充历史简历 - explicit human correction, appends a corrective event.
  attributeResume: (
    jobId: number,
    resumeId: number | null,
    usage: ResumeUsage,
    appliedEventId?: number | null,
  ) =>
    request<WorkflowResponse>(`/api/jobs/${jobId}/attribute-resume`, {
      method: 'POST',
      body: JSON.stringify({
        resume_id: resumeId,
        resume_usage: usage,
        applied_event_id: appliedEventId ?? null,
      }),
    }),
  skipJob: (jobId: number, reason?: string) =>
    request<WorkflowResponse>(`/api/jobs/${jobId}/skip`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason || null }),
    }),
  deferJob: (jobId: number, preset: LaterPreset, reviewAfter?: string) =>
    request<WorkflowResponse>(`/api/jobs/${jobId}/later`, {
      method: 'POST',
      body: JSON.stringify({ preset, review_after: reviewAfter || null }),
    }),
  resetJobStatus: (jobId: number) =>
    request<WorkflowResponse>(`/api/jobs/${jobId}/reset-status`, {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  recordReply: (jobId: number, responseType: ResponseType, note?: string) =>
    request<WorkflowResponse>(`/api/jobs/${jobId}/reply`, {
      method: 'POST',
      body: JSON.stringify({ response_type: responseType, note: note || null }),
    }),
  recordInterview: (jobId: number, round?: InterviewRound, note?: string) =>
    request<WorkflowResponse>(`/api/jobs/${jobId}/interview`, {
      method: 'POST',
      body: JSON.stringify({ round: round || null, note: note || null }),
    }),
  recordOffer: (jobId: number, salaryText?: string, note?: string) =>
    request<WorkflowResponse>(`/api/jobs/${jobId}/offer`, {
      method: 'POST',
      body: JSON.stringify({ salary_text: salaryText || null, note: note || null }),
    }),
  recordRejection: (jobId: number, reason?: string) =>
    request<WorkflowResponse>(`/api/jobs/${jobId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason || null }),
    }),

  // --- quick capture (v0.3) ---
  // Content the user pasted or uploaded. The backend never fetches the URL.
  quickCaptureSettings: () => request<QuickCaptureSettings>('/api/quick-capture/settings'),
  parseJobText: (text: string, sourceUrl?: string | null, allowAi = true) =>
    request<ParseResponse>('/api/quick-capture/text/parse', {
      method: 'POST',
      body: JSON.stringify({ text, source_url: sourceUrl || null, allow_ai: allowAi }),
    }),
  parseJobImage: (file: File, sourceUrl?: string | null) => {
    const form = new FormData()
    form.append('file', file)
    if (sourceUrl) form.append('source_url', sourceUrl)
    return request<ParseResponse>('/api/quick-capture/image/parse', {
      method: 'POST',
      body: form,
    })
  },
  confirmCandidate: (candidate: JobImportCandidate) =>
    request<ConfirmResponse>('/api/quick-capture/confirm', {
      method: 'POST',
      body: JSON.stringify({ candidate }),
    }),

  // --- browser capture (v0.2) ---
  // The browser is visible and human-driven: these only start/stop it and read
  // whichever job page the user has already opened.
  browserStatus: () => request<BrowserStatus>('/api/browser/status'),
  startBrowser: () => request<BrowserStartResponse>('/api/browser/start', { method: 'POST' }),
  stopBrowser: () => request<{ message: string }>('/api/browser/stop', { method: 'POST' }),
  currentPage: () => request<CurrentPage>('/api/browser/current-page'),
  captureCurrentJob: (analyze = false) =>
    request<CaptureResponse>(`/api/browser/capture-current-job${query({ analyze })}`, {
      method: 'POST',
    }),

  // --- dashboard / settings ---
  dashboard: () => request<DashboardSummary>('/api/dashboard/summary'),
  settings: () => request<AppSettings>('/api/settings'),
  getStrategy: () => request<CareerStrategyResponse>('/api/settings/career-strategy'),
  saveStrategy: (strategy: CareerStrategy) =>
    request<CareerStrategyResponse>('/api/settings/career-strategy', {
      method: 'PUT',
      body: JSON.stringify(strategy),
    }),

  // --- career analytics (v0.6) ---
  // Deterministic: these endpoints never call OpenAI, so they cost nothing and
  // can be refreshed freely. Reading them changes no data.
  careerAnalytics: (params: {
    window?: TimeWindow
    city?: string | null
    roleFamily?: string | null
    source?: string | null
  } = {}) =>
    request<CareerAnalyticsResult>(
      `/api/analytics/career${query({
        window: params.window,
        city: params.city || undefined,
        role_family: params.roleFamily || undefined,
        source: params.source || undefined,
      })}`,
    ),
  dashboardAnalytics: () => request<DashboardAnalytics>('/api/analytics/career/dashboard'),
  strategyRecommendations: (window: TimeWindow = '30d') =>
    request<RecommendationsResponse>(
      `/api/analytics/strategy-recommendations${query({ window })}`,
    ),
  // Preview writes nothing - it only shows what applying would change.
  previewProposal: (signature: string, window: TimeWindow = '30d') =>
    request<ApplyProposalResponse>(
      `/api/analytics/strategy-recommendations/${encodeURIComponent(signature)}/preview${query({ window })}`,
      { method: 'POST' },
    ),
  // `confirmed` is mandatory on the backend; the UI always asks first.
  applyProposal: (signature: string, note?: string, window: TimeWindow = '30d') =>
    request<ApplyProposalResponse>(
      `/api/analytics/strategy-recommendations/${encodeURIComponent(signature)}/apply${query({ window })}`,
      { method: 'POST', body: JSON.stringify({ confirmed: true, note: note || null }) },
    ),
  // --- resume variants (v0.7) ---
  updateResume: (
    id: number,
    payload: { variant_name?: string; variant_group?: string; notes?: string },
  ) =>
    request<ResumeDetail>(`/api/resumes/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  cloneResume: (id: number, variantName?: string) =>
    request<ResumeDetail>(`/api/resumes/${id}/clone`, {
      method: 'POST',
      body: JSON.stringify({ variant_name: variantName || null }),
    }),
  archiveResume: (id: number) =>
    request<ResumeDetail>(`/api/resumes/${id}/archive`, { method: 'POST' }),
  unarchiveResume: (id: number) =>
    request<ResumeDetail>(`/api/resumes/${id}/unarchive`, { method: 'POST' }),
  resumePerformance: (id: number) =>
    request<ResumeDetail>(`/api/resumes/${id}/performance`),

  // Deterministic - no AI call, so this is free to refresh.
  resumeAnalytics: (
    params: {
      window?: TimeWindow
      city?: string | null
      roleFamily?: string | null
      source?: string | null
    } = {},
  ) =>
    request<ResumeAnalyticsResult>(
      `/api/analytics/resumes${query({
        window: params.window,
        city: params.city || undefined,
        role_family: params.roleFamily || undefined,
        source: params.source || undefined,
      })}`,
    ),
  unattributedApplications: () =>
    request<UnattributedResponse>('/api/analytics/resumes/unattributed'),

  // Reading stored scores costs nothing.
  jobResumeAnalyses: (jobId: number) =>
    request<JobResumeAnalyses>(`/api/jobs/${jobId}/resume-analyses`),
  analyzeWithResume: (jobId: number, resumeId: number, force = false) =>
    request<AnalysisResponse>(`/api/jobs/${jobId}/analyze-with-resume/${resumeId}`, {
      method: 'POST',
      body: JSON.stringify({ force, use_smart_model: false }),
    }),
  // MAY SPEND OPENAI CREDITS. Call with confirmed=false first: the 422 carries
  // `detail.pending_analyses` so the UI can warn with an exact count.
  compareResumes: (jobId: number, resumeIds: number[], confirmed: boolean) =>
    request<ResumeComparisonResponse>(`/api/jobs/${jobId}/compare-resumes`, {
      method: 'POST',
      body: JSON.stringify({ resume_ids: resumeIds, confirmed }),
    }),

  // --- interview pipeline (v0.8) ---
  // Every one of these records something the human decided. JobAgent reads no
  // calendar and no inbox, and never accepts an interview on its own.
  interviewBoard: () => request<InterviewBoardResponse>('/api/interviews'),
  upcomingInterviews: (limit = 5) =>
    request<UpcomingInterviewsResponse>(`/api/interviews/upcoming${query({ limit })}`),
  jobInterviews: (jobId: number) =>
    request<InterviewProcessListResponse>(`/api/jobs/${jobId}/interviews`),
  getInterviewProcess: (processId: number) =>
    request<InterviewProcessOut>(`/api/interviews/${processId}`),
  // Opens a process on the job's current application cycle. One cycle, one
  // process - a second attempt is refused by the backend.
  createInterviewProcess: (jobId: number, appliedEventId?: number) =>
    request<ProcessActionResponse>(`/api/jobs/${jobId}/interviews`, {
      method: 'POST',
      body: JSON.stringify({ applied_event_id: appliedEventId ?? null }),
    }),
  addInterviewRound: (processId: number, payload: RoundCreatePayload) =>
    request<RoundActionResponse>(`/api/interviews/${processId}/rounds`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateInterviewRound: (roundId: number, payload: Record<string, unknown>) =>
    request<RoundActionResponse>(`/api/interview-rounds/${roundId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  // Requires an explicit confirmation. Re-recording an outcome additionally
  // needs `correction: true` - a stored result is never silently replaced.
  completeInterviewRound: (roundId: number, payload: RoundCompletePayload) =>
    request<RoundActionResponse>(`/api/interview-rounds/${roundId}/complete`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  cancelInterviewRound: (roundId: number, reason?: string) =>
    request<RoundActionResponse>(`/api/interview-rounds/${roundId}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason || null }),
    }),
  // The candidate ending the process. Never recorded as a rejection.
  withdrawInterviewProcess: (processId: number, reason: WithdrawReason, notes?: string) =>
    request<ProcessActionResponse>(`/api/interviews/${processId}/withdraw`, {
      method: 'POST',
      body: JSON.stringify({ confirmed: true, reason, notes: notes || null }),
    }),
  // Reading suggestions creates nothing and calls no model.
  interviewSuggestions: (conversationId: number) =>
    request<SuggestionListResponse>(
      `/api/recruiter/conversations/${conversationId}/interview-suggestions`,
    ),
  interviewAnalytics: (
    params: {
      window?: TimeWindow
      city?: string | null
      roleFamily?: string | null
      source?: string | null
      resumeId?: number | null
    } = {},
  ) =>
    request<InterviewAnalyticsResult>(
      `/api/analytics/interviews${query({
        window: params.window,
        city: params.city || undefined,
        role_family: params.roleFamily || undefined,
        source: params.source || undefined,
        resume_id: params.resumeId ?? undefined,
      })}`,
    ),

  // --- offers (v0.9) ---
  // JobAgent negotiates nothing and accepts nothing. Every call here records a
  // decision the human already made, or intends to make, outside the app.
  offerBoard: () => request<OfferBoardResponse>('/api/offers'),
  jobOffers: (jobId: number) => request<OfferListResponse>(`/api/jobs/${jobId}/offers`),
  getOffer: (offerId: number) => request<OfferOut>(`/api/offers/${offerId}`),
  createOffer: (jobId: number, payload: OfferCreatePayload) =>
    request<OfferActionResponse>(`/api/jobs/${jobId}/offers`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateOffer: (offerId: number, payload: Record<string, unknown>) =>
    request<OfferActionResponse>(`/api/offers/${offerId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  addOfferRevision: (offerId: number, payload: RevisionCreatePayload) =>
    request<OfferActionResponse>(`/api/offers/${offerId}/revisions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  // Recorded, never sent - and never treated as the company's offer.
  recordCounter: (offerId: number, payload: CounterPayload) =>
    request<OfferActionResponse>(`/api/offers/${offerId}/counter`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  // Freezes which revision was accepted.
  acceptOffer: (offerId: number, revisionId?: number | null, notes?: string) =>
    request<OfferActionResponse>(`/api/offers/${offerId}/accept`, {
      method: 'POST',
      body: JSON.stringify({
        confirmed: true,
        revision_id: revisionId ?? null,
        notes: notes || null,
      }),
    }),
  declineOffer: (offerId: number, reason: string, notes?: string) =>
    request<OfferActionResponse>(`/api/offers/${offerId}/decline`, {
      method: 'POST',
      body: JSON.stringify({ confirmed: true, reason, notes: notes || null }),
    }),
  expireOffer: (offerId: number) =>
    request<OfferActionResponse>(`/api/offers/${offerId}/expire`, {
      method: 'POST',
      body: JSON.stringify({ confirmed: true }),
    }),
  withdrawOffer: (offerId: number) =>
    request<OfferActionResponse>(`/api/offers/${offerId}/withdraw`, {
      method: 'POST',
      body: JSON.stringify({ confirmed: true }),
    }),
  compareOffers: (offerIds: number[]) =>
    request<OfferComparisonResponse>(
      `/api/offers/compare?${offerIds.map((id) => `offer_id=${id}`).join('&')}`,
    ),
  // Deterministic; saves nothing and calls no model.
  parseOfferText: (text: string) =>
    request<ParsedSalaryOut>('/api/offers/parse-text', {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  offerAnalytics: (
    params: { window?: TimeWindow; city?: string | null; roleFamily?: string | null } = {},
  ) =>
    request<OfferAnalyticsResult>(
      `/api/analytics/offers${query({
        window: params.window,
        city: params.city || undefined,
        role_family: params.roleFamily || undefined,
      })}`,
    ),

  // --- offer decision support (v1.0) ---
  // Deterministic throughout: weights and ratings come from the user, the
  // arithmetic is pure, and nothing here calls a model.
  decisionProfile: () => request<DecisionProfileOut>('/api/decision/profile'),
  updateDecisionProfile: (payload: DecisionProfileUpdate) =>
    request<DecisionProfileOut>('/api/decision/profile', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  offerAssessment: (offerId: number) =>
    request<AssessmentOut>(`/api/decision/offers/${offerId}/assessment`),
  // Only you rate a company. A blank rating stays blank - it is not a zero.
  saveOfferAssessment: (offerId: number, payload: AssessmentUpdate) =>
    request<AssessmentOut>(`/api/decision/offers/${offerId}/assessment`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  decisionCompare: (offerIds: number[]) =>
    request<DecisionComparisonOut>(
      `/api/decision/compare?${offerIds.map((id) => `offer_id=${id}`).join('&')}`,
    ),
  // Freezes the offers, revisions, weights, ratings and rates behind a result.
  createDecisionSnapshot: (offerIds: number[], name: string, notes?: string) =>
    request<SnapshotDetail>('/api/decision/snapshots', {
      method: 'POST',
      body: JSON.stringify({ offer_ids: offerIds, name, notes: notes || null }),
    }),
  decisionSnapshots: () => request<SnapshotListResponse>('/api/decision/snapshots'),
  decisionSnapshot: (snapshotId: number) =>
    request<SnapshotDetail>(`/api/decision/snapshots/${snapshotId}`),
  deleteDecisionSnapshot: (snapshotId: number) =>
    request<{ message: string }>(`/api/decision/snapshots/${snapshotId}`, {
      method: 'DELETE',
    }),
  negotiationPosition: (offerId: number) =>
    request<NegotiationPositionOut>(`/api/decision/offers/${offerId}/negotiation`),
  // Shown before accepting. Never blocks, never declines anything for you.
  competingOffers: (offerId: number) =>
    request<CompetingOffersResponse>(`/api/decision/offers/${offerId}/competing`),

  // --- 求职任务控制台 (M1) ---
  // Configuration + grouping only: creating a task never searches or
  // captures anything, and associating a candidate never creates a Job or
  // calls a model - it attaches a job that was already captured through an
  // existing intake path (Quick Capture, browser capture, the extension).
  createTask: (payload: TaskCreatePayload) =>
    request<TaskOut>('/api/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  listTasks: () => request<TaskListResponse>('/api/tasks'),
  getTask: (id: number) => request<TaskOut>(`/api/tasks/${id}`),
  addTaskCandidate: (taskId: number, jobId: number) =>
    request<TaskCandidateListResponse['items'][number]>(`/api/tasks/${taskId}/candidates`, {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId }),
    }),
  listTaskCandidates: (taskId: number) =>
    request<TaskCandidateListResponse>(`/api/tasks/${taskId}/candidates`),
  getConsoleAttention: () => request<ConsoleAttentionOut>('/api/console/attention'),
  listTaskEvents: (taskId: number) =>
    request<OrchestrationEventListResponse>(`/api/tasks/${taskId}/events`),
  addTaskEvent: (taskId: number, payload: OrchestrationEventCreatePayload) =>
    request<OrchestrationEventOut>(`/api/tasks/${taskId}/events`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // --- M5a: explicit, task-scoped candidate matching + human review ---
  // Planning is a pure read (never calls a model, never writes a row);
  // running is an explicit, confirmed action that shows the exact pending
  // call count first. Never triggered on load or on task completion.
  getTaskMatchPlan: (taskId: number) =>
    request<TaskMatchPlanOut>(`/api/tasks/${taskId}/match-plan`),
  getAutoMatchReview: (taskId: number) =>
    request<AutoMatchReview>(`/api/tasks/${taskId}/auto-match/review`),
  runTaskMatch: (taskId: number, confirmed: boolean) =>
    request<TaskMatchRunResponse>(`/api/tasks/${taskId}/match-run`, {
      method: 'POST',
      body: JSON.stringify({ confirmed }),
    }),

  // --- M5b: one exact, finite set of completed SearchPlan tasks ---
  // The plan is read-only. The run sends back the exact fingerprint and
  // exact whole-batch new-call count shown to the human (never above 3).
  getCrossTaskMatchPlan: (taskIds: number[]) =>
    request<CrossTaskMatchPlanOut>('/api/task-match-batches/plan', {
      method: 'POST',
      body: JSON.stringify({ task_ids: taskIds }),
    }),
  runCrossTaskMatch: (plan: CrossTaskMatchPlanOut) =>
    request<CrossTaskMatchRunResponse>('/api/task-match-batches/run', {
      method: 'POST',
      body: JSON.stringify({
        task_ids: plan.task_ids,
        confirmed: true,
        fingerprint: plan.fingerprint,
        max_new_calls: plan.max_new_calls,
      }),
    }),

  dismissProposal: (signature: string, note?: string) =>
    request<DismissProposalResponse>(
      `/api/analytics/strategy-recommendations/${encodeURIComponent(signature)}/dismiss`,
      { method: 'POST', body: JSON.stringify({ confirmed: false, note: note || null }) },
    ),
}
