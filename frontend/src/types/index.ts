// Mirrors the Pydantic schemas in backend/app/schemas.
// Keep this file in sync when a backend response shape changes.

export type Verdict = 'strong_apply' | 'apply' | 'maybe' | 'skip'

// --------------------------------------------------------------------------
// 求职任务控制台 (M1) - mirrors backend/app/schemas/task.py
// --------------------------------------------------------------------------

/** Only one mode exists until a future, separately-authorized milestone. */
export type TaskMode = 'manual_review_only'

export interface TaskCreatePayload {
  name: string
  keywords?: string | null
  city?: string | null
  experience_text?: string | null
  education_text?: string | null
  salary_min?: number | null
  salary_max?: number | null
  /** `undefined`/omitted = never set. `[]` = explicitly "no exclusions". */
  exclusions?: string[] | null
  resume_id?: number | null
  max_candidates?: number | null
  min_score?: number | null
  mode?: TaskMode
  notes?: string | null
}

export interface TaskOut {
  id: number
  name: string
  keywords: string | null
  city: string | null
  experience_text: string | null
  education_text: string | null
  salary_min: number | null
  salary_max: number | null
  /** `null` = never set. `[]` = explicitly "no exclusions". */
  exclusions: string[] | null
  resume_id: number | null
  max_candidates: number | null
  min_score: number | null
  mode: TaskMode
  notes: string | null
  created_at: string
  updated_at: string
}

export interface TaskListResponse {
  items: TaskOut[]
  total: number
}

export interface TaskCandidateAnalysis {
  overall_score: number
  verdict: Verdict
}

export interface TaskCandidateOut {
  job_id: number
  title: string
  company: string
  city: string | null
  salary_text: string | null
  status: JobStatus
  added_at: string
  /** The job's current analysis, or absent if it has never been analyzed. */
  analysis: TaskCandidateAnalysis | null
}

export interface TaskCandidateListResponse {
  items: TaskCandidateOut[]
  total: number
}

// --- M5a: explicit, task-scoped candidate matching + human review ---

export interface TaskMatchCandidateOut {
  job_id: number
  title: string
  company: string
  cached: boolean
  overall_score: number | null
  verdict: Verdict | null
}

export interface TaskMatchPlanOut {
  task_id: number
  min_score: number | null
  active_resume_id: number
  active_resume_name: string
  model: string
  candidates: TaskMatchCandidateOut[]
  total_candidates: number
  /** Already capped at `cap` - how many would actually run right now. */
  pending_analyses: number
  /** The honest, uncapped pending count. */
  pending_total: number
  cap: number
}

export interface TaskMatchOutcomeOut {
  job_id: number
  cached: boolean
  overall_score: number | null
  verdict: Verdict | null
  /** A safe, actionable Chinese message - never a raw exception/response body. */
  error: string | null
  /** Populated only for a classified upstream/model failure. */
  category: string | null
  http_status: number | null
  error_code: string | null
  request_id: string | null
}

export interface TaskMatchRunResponse {
  task_id: number
  results: TaskMatchOutcomeOut[]
  analyzed: number
  failed: number
}

export type JobStatus =
  | 'new'
  | 'reviewed'
  | 'saved'
  | 'skipped'
  | 'applied'
  | 'replied'
  | 'interview'
  | 'offer'
  | 'rejected'

export interface AnalysisSummary {
  analysis_id: number
  overall_score: number
  verdict: Verdict
  model: string
  created_at: string
  reasoning_summary: string
}

export interface JobListItem {
  id: number
  source: string
  source_url: string | null
  company: string
  title: string
  city: string | null
  salary_text: string | null
  experience_text: string | null
  education_text: string | null
  status: JobStatus
  created_at: string
  updated_at: string
  description_preview: string
  latest_analysis: AnalysisSummary | null
}

export interface JobEvent {
  id: number
  event_type: string
  notes: string | null
  created_at: string
}

export interface JobDetail extends JobListItem {
  raw_description: string
  normalized_description: string
  content_hash: string
  external_id: string | null
  events: JobEvent[]
}

export interface JobListResponse {
  total: number
  limit: number
  offset: number
  items: JobListItem[]
  facets: {
    cities?: Record<string, number>
    statuses?: Record<string, number>
  }
}

export interface JobCreatePayload {
  title: string
  company: string
  raw_description: string
  city?: string | null
  salary_text?: string | null
  experience_text?: string | null
  education_text?: string | null
  source_url?: string | null
}

export interface JobMatchResult {
  overall_score: number
  verdict: Verdict
  role_fit_score: number
  skill_fit_score: number
  experience_fit_score: number
  location_fit_score: number
  salary_fit_score: number | null
  matched_skills: string[]
  missing_skills: string[]
  strengths: string[]
  gaps: string[]
  risk_flags: string[]
  experience_gap: string
  role_summary: string
  reasoning_summary: string
  greeting_message: string
}

export interface AnalysisMeta {
  analysis_id: number
  job_id: number
  resume_id: number
  model: string
  prompt_version: string
  cache_key: string
  cached: boolean
  created_at: string
}

export interface PreAnalysis {
  city: string | null
  city_match: boolean
  city_reason: string
  matched_roles: string[]
  title_family_match: boolean
  matched_skills: string[]
  missing_skills: string[]
  skill_overlap_count: number
  skill_overlap_ratio: number
  resume_skill_overlap: string[]
  excluded_hits: string[]
  excluded_softened: boolean
  experience_required: string
  experience_min_years: number | null
  experience_max_years: number | null
  experience_is_hard: boolean
  experience_within_preference: boolean | null
  salary_min: number | null
  salary_max: number | null
  salary_meets_minimum: boolean | null
  heuristic_score: number
}

export interface AnalysisResponse {
  meta: AnalysisMeta
  result: JobMatchResult
  pre_analysis: PreAnalysis | null
}

export interface ResumeListItem {
  id: number
  filename: string
  file_type: string
  /**
   * The **analysis** resume flag - what newly captured jobs are matched
   * against. It says nothing about which resume any past application used;
   * that lives on the application cycle. See CLAUDE.md.
   */
  is_active: boolean
  created_at: string
  updated_at: string
  text_length: number
  skills: string[]

  // --- variant metadata (v0.7) ---
  variant_name: string | null
  variant_group: string | null
  parent_resume_id: number | null
  notes: string | null
  archived_at: string | null
  archived: boolean
  /** variant_name, falling back to the filename. */
  label: string
  applications: number
  mature_applications: number
  replies: number
  interviews: number
  offers: number
  analyzed_jobs: number
}

export interface ResumeProfile {
  summary?: string
  work_experience?: string[]
  projects?: string[]
  skills?: string[]
  highlighted_skills?: string[]
  certifications?: string[]
  education?: string[]
  languages?: string[]
  years_of_experience?: number | null
  contact?: { emails?: string[]; phones?: string[] }
  sections_detected?: string[]
  text_length?: number
}

export interface ResumeDetail extends ResumeListItem {
  content_hash: string
  raw_text_preview: string
  parsed_profile: ResumeProfile
}

export interface ResumeUploadResponse {
  resume: ResumeDetail
  reused_existing: boolean
  message: string
}

export interface ScoreBucket {
  label: string
  count: number
}

export interface TopJob {
  job_id: number
  company: string
  title: string
  city: string | null
  salary_text: string | null
  overall_score: number
  verdict: Verdict
}

export interface DashboardSummary {
  total_jobs: number
  analyzed_jobs: number
  unanalyzed_jobs: number
  recommended: number
  strong_apply: number
  apply: number
  maybe: number
  skip: number
  average_score: number | null
  by_status: Record<string, number>
  by_city: Record<string, number>
  score_buckets: ScoreBucket[]
  top_jobs: TopJob[]
  active_resume_id: number | null
  openai_configured: boolean
  funnel: Partial<FunnelCounts>
  rates: Partial<FunnelRates>
  daily_target: number
  applied_today: number
}

export interface ExperiencePolicy {
  preferred_min_years: number
  preferred_max_years: number
  hard_reject_above_years: number
  flexibility: string
  notes: string
}

export interface SalaryPolicy {
  min_monthly_cny: number
  ideal_monthly_cny: number
  hard_filter: boolean
}

export interface CareerStrategy {
  version: number
  early_career_policy: 'exclude' | 'include' | 'only'
  target_cities: string[]
  remote_ok: boolean
  preferred_roles: string[]
  relevant_skills: string[]
  skill_aliases: Record<string, string[]>
  excluded_keywords: string[]
  excluded_soft_override_skills: string[]
  experience_policy: ExperiencePolicy
  salary: SalaryPolicy
  scoring: Record<string, unknown>
  preferences: Record<string, unknown>
}

export interface CareerStrategyResponse {
  strategy: CareerStrategy
  path: string
  hash: string
}

export interface AppSettings {
  openai_configured: boolean
  model_fast: string
  model_smart: string
  database_url: string
  max_analyses_per_run: number
  auto_apply: boolean
  prompt_version: string
  strategy_path: string
  version: string
}

export interface HealthResponse {
  status: string
  version: string
  database: string
  openai_configured: boolean
  auto_apply: boolean
  models: { fast: string; smart: string }
}

export interface BatchAnalyzePlan {
  selected: number
  limit: number
  in_batch: number
  deferred: number
  cached: number
  pending: number
  model: string
  resume_id: number
  resume_name: string
  missing_job_ids: number[]
}

export interface BatchAnalyzeItem {
  job_id: number
  ok: boolean
  cached: boolean
  overall_score: number | null
  verdict: Verdict | null
  error: string | null
}

export interface BatchAnalyzeResponse {
  requested: number
  analyzed: number
  cached: number
  failed: number
  limit: number
  items: BatchAnalyzeItem[]
}

// --- v0.2 browser capture -------------------------------------------------

export interface BrowserStatus {
  running: boolean
  site: string | null
  current_url: string | null
  current_title: string | null
  page_count: number
  supported_hosts: string[]
  profile_dir: string | null
  channel: string | null
  message: string
}

export interface BrowserStartResponse extends BrowserStatus {
  already_running: boolean
}

export interface CurrentPage {
  site: string | null
  url: string | null
  title: string | null
  is_job_page: boolean
  external_id: string | null
  message: string
}

export interface CapturedFields {
  title: string
  company: string | null
  city: string | null
  salary_text: string | null
  experience_text: string | null
  education_text: string | null
  external_id: string | null
  description_chars: number
  fields_found: string[]
  fields_missing: string[]
  extra: Record<string, string>
}

export interface CaptureResponse {
  job_id: number
  duplicate: boolean
  site: string
  selected_url: string | null
  page_title: string | null
  job: JobDetail
  fields: CapturedFields
  analysis: AnalysisResponse | null
  message: string
}

// --- v0.3 quick capture ---------------------------------------------------

export type Confidence = 'high' | 'medium' | 'low'

export type ExtractionMethod = 'deterministic' | 'ai_text' | 'ai_vision' | 'hybrid'

export interface FieldConfidence {
  overall: Confidence
  company: Confidence
  title: Confidence
  city: Confidence
  salary: Confidence
  experience: Confidence
  description: Confidence
}

export interface JobImportCandidate {
  source: string
  source_url: string | null
  company: string | null
  title: string | null
  city: string | null
  salary_text: string | null
  experience_text: string | null
  education_text: string | null
  raw_description: string
  confidence: FieldConfidence
  warnings: string[]
  extraction_method: ExtractionMethod
}

export interface ParseResponse {
  candidate: JobImportCandidate
  ai_used: boolean
  ai_available: boolean
  ai_error: string | null
  message: string
}

export interface ConfirmResponse {
  job_id: number
  duplicate: boolean
  job: JobDetail
  message: string
}

export interface QuickCaptureSettings {
  ai_extraction_enabled: boolean
  openai_configured: boolean
  max_image_mb: number
  accepted_image_types: string[]
}

// --- v0.4 application queue ------------------------------------------------

export type ProposalState = 'pending' | 'ready' | 'later' | 'dismissed' | 'completed'
export type ResponseType = 'positive' | 'neutral' | 'negative'
export type InterviewRound = 'HR' | '一面' | '二面' | '技术面' | '终面' | '其他'
export type LaterPreset = 'today' | 'tomorrow' | 'custom'

export interface ApplicationEventOut {
  id: number
  event_type: string
  notes: string | null
  metadata_json: Record<string, unknown>
  created_at: string
}

export interface ApplicationProposal {
  job_id: number
  company: string
  title: string
  city: string | null
  salary_text: string | null
  source: string
  source_url: string | null
  early_career: boolean
  overall_score: number
  verdict: Verdict
  matched_skills: string[]
  missing_skills: string[]
  reasoning_summary: string
  greeting_message: string
  job_status: JobStatus
  proposal_state: ProposalState
  review_after: string | null
  latest_application_event: ApplicationEventOut | null
  created_at: string
  analyzed_at: string | null
}

export interface QueueSummary {
  pending: number
  strong_apply: number
  apply: number
  later: number
  early_career_hidden: number
  early_career_policy: string
  applied_today: number
  replied_today: number
  interview_today: number
  skipped_today: number
  daily_target: number
  timezone: string
}

export interface QueueResponse {
  items: ApplicationProposal[]
  total: number
  summary: QueueSummary
  facets: {
    cities?: Record<string, number>
    sources?: Record<string, number>
    skip_reasons?: string[]
    sorts?: string[]
  }
}

export interface ApplicationApprovalOut {
  id: number
  job_id: number
  company: string
  title: string
  canonical_url: string
  external_id: string
  resume_id: number
  resume_hash: string
  answers_text: string
  answers_hash: string
  answers_source: 'boss_dynamic_unverified' | string
  state: 'pending' | 'executing' | 'consumed' | 'invalidated'
  invalidated_reason: string | null
  outcome: 'applied' | 'unknown' | 'failed' | null
  outcome_detail: string | null
  consumed_at: string | null
  attempt_started_at: string | null
  applied_event_id: number | null
  created_at: string
  updated_at: string
}

export interface WorkflowResponse {
  job_id: number
  status: JobStatus
  previous_status: JobStatus
  event: ApplicationEventOut
  message: string
}

export interface FunnelCounts {
  total_jobs: number
  analyzed_jobs: number
  recommended_jobs: number
  applied_jobs: number
  replied_jobs: number
  interview_jobs: number
  offer_jobs: number
  rejected_jobs: number
}

export interface FunnelRates {
  application_response_rate: number | null
  application_interview_rate: number | null
  response_interview_rate: number | null
}

export interface ApplicationMetrics {
  counts: FunnelCounts
  rates: FunnelRates
  by_city: Record<string, Record<string, number>>
  by_role_family: Record<string, Record<string, number>>
  by_source: Record<string, Record<string, number>>
}

// --- v0.5 recruiter conversations ------------------------------------------

export type Sentiment = 'positive' | 'neutral' | 'negative' | 'unclear'

export type ConversationStage =
  | 'initial_contact'
  | 'screening'
  | 'interview_scheduling'
  | 'document_request'
  | 'salary_discussion'
  | 'offer_discussion'
  | 'rejection'
  | 'follow_up'
  | 'other'

export type Urgency = 'low' | 'normal' | 'high'
export type ReplyLanguage = 'zh' | 'ja' | 'en' | 'other'
export type LanguagePreference = 'auto' | 'zh' | 'ja' | 'en'
export type MessageDirection = 'recruiter' | 'user'
export type InputMode = 'auto' | 'single_message' | 'conversation'
export type FollowUpPreset = 'tomorrow' | 'in_3_days' | 'in_1_week' | 'custom'

export type ConversationStatus =
  | 'needs_reply'
  | 'waiting_recruiter'
  | 'follow_up_due'
  | 'closed'
  | 'no_action'

export type RecruiterSourceName =
  | 'boss'
  | 'liepin'
  | 'zhaopin'
  | 'job51'
  | 'linkedin'
  | 'wechat'
  | 'email'
  | 'phone'
  | 'other'

export interface RecruiterRequest {
  type: string
  summary: string
  required: boolean
  answer_found_in_profile: boolean
  suggested_answer: string | null
}

export interface ActionItem {
  summary: string
  blocking: boolean
}

export interface DateTimeMention {
  raw_text: string
  normalized_at: string | null
  normalized_date: string | null
  is_ambiguous: boolean
  context: string
}

export interface RecruiterMessageAnalysisResult {
  sentiment: Sentiment
  conversation_stage: ConversationStage
  needs_reply: boolean
  urgency: Urgency
  summary: string
  recruiter_requests: RecruiterRequest[]
  action_items: ActionItem[]
  dates_times: DateTimeMention[]
  missing_information: string[]
  risk_flags: string[]
  suggested_reply: string | null
  suggested_reply_language: ReplyLanguage
  confidence: number
}

export interface RecruiterMessageOut {
  id: number
  conversation_id: number
  direction: MessageDirection
  raw_text: string
  source_message_id: string | null
  source_message_time_text: string | null
  captured_at: string
  created_at: string
  analysis: RecruiterMessageAnalysisResult | null
  analyzed_at: string | null
  analysis_model: string | null
}

export interface ConversationSummaryOut {
  id: number
  job_id: number | null
  job_title: string | null
  job_company: string | null
  source: RecruiterSourceName
  recruiter_name: string | null
  company: string | null
  title: string | null
  status: ConversationStatus
  close_reason: string | null
  last_message_at: string | null
  next_action_at: string | null
  created_at: string
  message_count: number
  latest_preview: string
  latest_direction: MessageDirection | null
  sentiment: Sentiment | null
  stage: ConversationStage | null
  needs_reply: boolean
  action_item_count: number
  summary: string
}

export interface ConversationDetailOut extends ConversationSummaryOut {
  messages: RecruiterMessageOut[]
  latest_analysis: RecruiterMessageAnalysisResult | null
  job_events: ApplicationEventOut[]
  suggests_recruiter_reply_event: boolean
}

export interface InboxSummary {
  needs_reply: number
  received_today: number
  replied_today: number
  interview_scheduling: number
  follow_up_due: number
  waiting_recruiter: number
  closed: number
  timezone: string
}

export interface InboxResponse {
  items: ConversationSummaryOut[]
  total: number
  summary: InboxSummary
}

export interface MessageCreatedOut {
  message: RecruiterMessageOut
  duplicate: boolean
  parsed_message_count: number
  warnings: string[]
  ai_used: boolean
  ai_available: boolean
  ai_error: string | null
  conversation: ConversationDetailOut
}

export interface RecruiterAnalysisOut {
  message_id: number
  result: RecruiterMessageAnalysisResult
  model: string
  prompt_version: string
  cached: boolean
  created_at: string
  deterministic_signals: Record<string, unknown>
}

export interface ConversationActionOut {
  conversation: ConversationDetailOut
  message: string
}

// --------------------------------------------------------------------------
// career strategy analytics (v0.6)
// --------------------------------------------------------------------------
//
// Mirrors backend/app/schemas/analytics.py. Every rate arrives with its
// numerator, denominator and confidence interval - the UI must never render a
// bare percentage, because "100% (1/1)" and "100% (12/12)" mean very different
// things.

export type TimeWindow = '7d' | '30d' | '90d' | 'all'

/** How much a *sample* supports a conclusion. Unrelated to the v0.3
 *  extraction `Confidence`, which describes how sure a parser is. */
export type SampleConfidence = 'insufficient' | 'low' | 'moderate' | 'strong'

export interface RateStat {
  rate: number | null
  numerator: number
  denominator: number
  ci_low: number | null
  ci_high: number | null
  ranking_score: number
  confidence: SampleConfidence
}

export interface LatencyStat {
  sample: number
  median_hours: number | null
  p25_hours: number | null
  p75_hours: number | null
}

export interface CohortStat {
  key: string
  label: string
  jobs: number
  applications: number
  mature_applications: number
  mature_interview_applications: number
  replies: number
  interviews: number
  offers: number
  rejections: number
  mature_no_response: number
  raw_reply_rate: RateStat
  mature_reply_rate: RateStat
  interview_rate: RateStat
  offer_rate: RateStat
  reply_latency: LatencyStat
}

export interface ScoreBandStat extends CohortStat {
  analyzed_jobs: number
}

export interface SummaryStat {
  applications: number
  mature_applications: number
  replies: number
  interviews: number
  offers: number
  rejections: number
  mature_no_response: number
  raw_reply_rate: RateStat
  mature_reply_rate: RateStat
  interview_rate: RateStat
  offer_rate: RateStat
  reply_latency: LatencyStat
  interview_latency: LatencyStat
}

export interface CountItem {
  key: string
  label: string
  count: number
}

export interface CoverageStat {
  covered: number
  total: number
  ratio: number | null
}

export interface DataQuality {
  applications: number
  mature_applications: number
  salary_coverage: CoverageStat
  conversation_coverage: CoverageStat
  recruiter_analysis_coverage: CoverageStat
  city_coverage: CoverageStat
  role_family_coverage: CoverageStat
  notes: string[]
}

export interface SkillOutcome {
  skill: string
  replied: number
  interviewed: number
  offered: number
  applied: number
}

export interface SkillOutcomes {
  matched_in_interviews: SkillOutcome[]
  matched_in_replies: SkillOutcome[]
  missing_in_high_score_jobs: CountItem[]
}

export interface RecruiterInsights {
  requests: CountItem[]
  sentiment: CountItem[]
  stage: CountItem[]
  analysis_coverage: CoverageStat
}

export type ObservationKind =
  | 'outperforming'
  | 'underperforming'
  | 'insufficient_data'
  | 'descriptive'

export interface StrategyObservation {
  kind: ObservationKind
  dimension: string
  target: string
  text: string
  metric: string
  numerator: number
  denominator: number
  comparison_rate: number | null
  confidence: SampleConfidence
}

export type ProposalType =
  | 'increase_city_priority'
  | 'decrease_city_priority'
  | 'increase_role_priority'
  | 'decrease_role_priority'
  | 'prioritize_source'
  | 'deprioritize_source'
  | 'consider_score_floor_change'
  | 'skill_learning_candidate'
  | 'collect_more_data'

export interface ProposalEvidence {
  metric: string
  numerator: number
  denominator: number
  rate: number | null
  ci_low: number | null
  ci_high: number | null
  comparison_rate: number | null
  window: TimeWindow
}

export interface StrategyAdjustmentProposal {
  signature: string
  type: ProposalType
  target: string
  current_value: unknown
  suggested_value: unknown
  reason: string
  evidence: ProposalEvidence
  sample_size: number
  confidence: SampleConfidence
  impact_description: string
  /** Only true for proposals that map onto a concrete strategy edit. */
  applicable: boolean
  decision: 'accepted' | 'dismissed' | null
}

export interface StrategyDiff {
  signature: string
  field: string
  before: unknown
  after: unknown
  description: string
}

export interface CareerAnalyticsResult {
  window: TimeWindow
  generated_at: string
  timezone: string
  response_maturity_days: number
  interview_maturity_days: number
  min_sample: number
  recommend_sample: number
  filters: Record<string, string | null>
  summary: SummaryStat
  by_city: CohortStat[]
  by_role_family: CohortStat[]
  by_source: CohortStat[]
  by_score_band: ScoreBandStat[]
  by_verdict: CohortStat[]
  by_salary_band: CohortStat[]
  by_city_role: CohortStat[]
  latency_by_source: CohortStat[]
  recruiter: RecruiterInsights
  skills: SkillOutcomes
  data_quality: DataQuality
  observations: StrategyObservation[]
  salary_parse_coverage: CoverageStat
}

export interface RecommendationsResponse {
  window: TimeWindow
  proposals: StrategyAdjustmentProposal[]
  suppressed: number
  message: string
}

export interface ApplyProposalResponse {
  applied: boolean
  signature: string
  diff: StrategyDiff[]
  strategy: CareerStrategy
  message: string
}

export interface DismissProposalResponse {
  signature: string
  decision: string
  message: string
}

export interface DashboardAnalytics {
  window: TimeWindow
  has_signal: boolean
  best_direction: string | null
  mature_reply_rate: RateStat | null
  interview_rate: RateStat | null
  applications: number
  message: string
}

// --------------------------------------------------------------------------
// resume variants (v0.7)
// --------------------------------------------------------------------------
//
// Two separate ideas, never conflated:
//   * 当前AI分析简历 - ResumeListItem.is_active
//   * 本次实际投递简历 - ApplicationCycle / MarkAppliedPayload.resume_id

export type ResumeUsage = 'used' | 'no_resume' | 'unknown'

export interface MarkAppliedPayload {
  confirmed: true
  note?: string | null
  /** The resume the user says they actually submitted. */
  resume_id?: number | null
  resume_usage?: ResumeUsage
}

export interface ResumeCohortStat extends CohortStat {
  resume_id: number | null
  variant_group: string | null
  archived: boolean
  is_active_analysis_resume: boolean
}

export interface ResumeBreakdownRow {
  dimension: string
  dimension_key: string
  dimension_label: string
  resumes: ResumeCohortStat[]
}

export interface ResumeFitCell {
  resume_id: number
  score: number | null
  verdict: Verdict | null
}

export interface ResumeFitRow {
  job_id: number
  company: string
  title: string
  city: string | null
  cells: ResumeFitCell[]
}

export interface ResumeFitSummary {
  resume_id: number
  label: string
  average_score: number | null
  jobs_scored: number
  best_on_jobs: number
}

/** AI-judged fit. Deliberately separate from real recruiter outcomes. */
export interface ResumeFitComparison {
  comparable_jobs: number
  resumes: ResumeFitSummary[]
  matrix: ResumeFitRow[]
  columns: CountItem[]
}

export interface ResumeAnalyticsResult {
  window: TimeWindow
  generated_at: string
  timezone: string
  response_maturity_days: number
  interview_maturity_days: number
  min_sample: number
  recommend_sample: number
  filters: Record<string, string | number | null>
  summary: SummaryStat
  by_resume: ResumeCohortStat[]
  unattributed: ResumeCohortStat | null
  attribution_coverage: CoverageStat
  by_resume_role: ResumeBreakdownRow[]
  by_resume_city: ResumeBreakdownRow[]
  fit_comparison: ResumeFitComparison
  observations: StrategyObservation[]
  observational_warning: string
}

export interface UnattributedApplication {
  job_id: number
  applied_event_id: number | null
  company: string
  title: string
  city: string | null
  applied_at: string
}

export interface UnattributedResponse {
  items: UnattributedApplication[]
  total: number
  message: string
}

export interface ResumeAnalysisCell {
  resume_id: number
  label: string
  archived: boolean
  overall_score: number | null
  verdict: Verdict | null
  analysis_id: number | null
  created_at: string | null
  /** False means the score came from cache and cost nothing. */
  newly_analyzed: boolean
}

export interface JobResumeAnalyses {
  job_id: number
  cells: ResumeAnalysisCell[]
  applied_resume_id: number | null
  active_analysis_resume_id: number | null
}

export interface ResumeComparisonResponse {
  job_id: number
  company: string
  title: string
  cells: ResumeAnalysisCell[]
  pending_analyses: number
  api_calls_made: number
  message: string
}

// --------------------------------------------------------------------------
// interview pipeline (v0.8)
// --------------------------------------------------------------------------
//
// Mirrors backend/app/schemas/interview.py.
//
// An InterviewProcess belongs to ONE application cycle (`applied_event_id`),
// so `resume_label` is the resume used at application time - not whichever
// resume happens to be active today.

export type InterviewProcessStatus =
  | 'ongoing'
  | 'completed'
  | 'rejected'
  | 'withdrawn'
  | 'offer'

export type InterviewRoundType =
  | 'hr'
  | 'screening'
  | 'technical'
  | 'coding'
  | 'system_design'
  | 'manager'
  | 'culture'
  | 'final'
  | 'other'

export type InterviewRoundStatus = 'planned' | 'scheduled' | 'completed' | 'cancelled'

export type InterviewOutcome = 'pending' | 'passed' | 'failed' | 'unknown'

export type InterviewLocationType = 'online' | 'onsite' | 'phone' | 'unknown'

export type InterviewFailureReason =
  | 'technical_depth'
  | 'experience_years'
  | 'language'
  | 'role_fit'
  | 'salary'
  | 'visa'
  | 'culture_fit'
  | 'position_cancelled'
  | 'unknown'
  | 'other'

export type WithdrawReason =
  | 'accepted_other_offer'
  | 'salary'
  | 'company'
  | 'location'
  | 'role_content'
  | 'personal'
  | 'other'

export type FeedbackTag =
  | 'technical_depth'
  | 'communication'
  | 'language'
  | 'experience'
  | 'cloud'
  | 'coding'
  | 'system_design'
  | 'culture'
  | 'salary'
  | 'other'

export interface PreparationNotes {
  questions_asked: string[]
  weak_points: string[]
  follow_up_topics: string[]
}

export interface InterviewRoundOut {
  id: number
  interview_process_id: number
  round_index: number
  round_type: InterviewRoundType
  round_type_label: string
  custom_round_name: string | null
  display_name: string
  scheduled_at: string | null
  duration_minutes: number | null
  completed_at: string | null
  status: InterviewRoundStatus
  outcome: InterviewOutcome
  failure_reason: InterviewFailureReason | null
  interviewer_name: string | null
  interviewer_role: string | null
  location_type: InterviewLocationType
  /** Round detail only - never present in analytics payloads. */
  meeting_url: string | null
  /** What the interviewer actually said. */
  feedback_text: string | null
  /** The candidate's own recollection. Kept separate on purpose. */
  notes: string | null
  feedback_tags: string[]
  preparation: PreparationNotes
  created_at: string
  updated_at: string
}

export interface InterviewProcessOut {
  id: number
  job_id: number
  applied_event_id: number
  status: InterviewProcessStatus
  ended_after_round_type: InterviewRoundType | null
  failure_reason: InterviewFailureReason | null
  withdraw_reason: WithdrawReason | null
  closed_at: string | null
  notes: string | null
  created_at: string
  updated_at: string
  rounds: InterviewRoundOut[]
  company: string
  title: string
  city: string | null
  applied_at: string | null
  /** The resume used for THIS cycle, frozen at application time. */
  resume_id: number | null
  resume_label: string | null
  resume_archived: boolean
  current_round: InterviewRoundOut | null
  next_scheduled_at: string | null
  rounds_passed: number
}

export interface UpcomingGroup {
  key: string
  label: string
  items: InterviewProcessOut[]
}

export interface LegacyInterviewMilestone {
  job_id: number
  event_id: number
  company: string
  title: string
  occurred_at: string
  note: string | null
  legacy_round_label: string | null
}

export interface InterviewBoardResponse {
  timezone: string
  generated_at: string
  upcoming: UpcomingGroup[]
  awaiting_result: InterviewProcessOut[]
  completed: InterviewProcessOut[]
  ongoing_without_schedule: InterviewProcessOut[]
  legacy_milestones: LegacyInterviewMilestone[]
}

export interface UpcomingInterviewItem {
  process_id: number
  job_id: number
  round_id: number
  company: string
  title: string
  round_label: string
  scheduled_at: string
  location_type: InterviewLocationType
  day_key: string
}

export interface UpcomingInterviewsResponse {
  timezone: string
  items: UpcomingInterviewItem[]
  total: number
  message: string
}

export interface InterviewProcessListResponse {
  items: InterviewProcessOut[]
  total: number
}

export interface ProcessActionResponse {
  process: InterviewProcessOut
  message: string
  job_status: string | null
}

export interface RoundActionResponse {
  process: InterviewProcessOut
  round: InterviewRoundOut
  message: string
  job_status: string | null
}

export interface RoundCreatePayload {
  round_type: InterviewRoundType
  custom_round_name?: string | null
  round_index?: number | null
  scheduled_at?: string | null
  duration_minutes?: number | null
  location_type?: InterviewLocationType
  meeting_url?: string | null
  interviewer_name?: string | null
  interviewer_role?: string | null
  notes?: string | null
}

export interface RoundCompletePayload {
  outcome: InterviewOutcome
  confirmed: true
  feedback_text?: string | null
  notes?: string | null
  feedback_tags?: FeedbackTag[]
  failure_reason?: InterviewFailureReason | null
  also_record_rejection?: boolean
  correction?: boolean
}

/** A time detected in a recruiter message. Reading it creates nothing. */
export interface InterviewSuggestion {
  conversation_id: number
  message_id: number
  job_id: number | null
  scheduled_at: string | null
  raw_text: string
  is_ambiguous: boolean
  suggested_round_type: InterviewRoundType | null
  can_add: boolean
  reason: string
}

export interface SuggestionListResponse {
  items: InterviewSuggestion[]
  total: number
  message: string
}

// --- interview analytics ---

export interface StageStat {
  key: string
  label: string
  reached: number
  eligible: number
  rate: RateStat
}

export interface RoundConversionStat {
  round_type: string
  label: string
  entered: number
  passed: number
  failed: number
  pending: number
  cancelled: number
  pass_rate: RateStat
}

export interface DropOffStat {
  round_type: string
  label: string
  rejected_after: number
  /** Counted apart from rejections - your own decision is not a failure. */
  withdrawn_after: number
  share: RateStat
}

export interface InterviewLatency {
  application_to_first_interview: LatencyStat
  first_to_second_round: LatencyStat
  last_round_to_decision: LatencyStat
  scheduled_to_completed: LatencyStat
}

export interface InterviewCohortStat extends CohortStat {
  processes: number
  reached_any_interview: number
  reached_technical: number
  reached_final: number
  interview_offers: number
  interview_reach_rate: RateStat
  final_reach_rate: RateStat
  interview_offer_rate: RateStat
  resume_id: number | null
  resume_archived: boolean
}

export interface InterviewFunnel {
  applications: number
  processes: number
  reached_any_interview: number
  reached_technical: number
  reached_final: number
  offers: number
  rejected: number
  withdrawn: number
  ongoing: number
  application_to_interview: RateStat
  first_to_next_round: RateStat
  technical_to_final: RateStat
  final_to_offer: RateStat
  stages: StageStat[]
}

export interface InterviewAnalyticsResult {
  window: TimeWindow
  generated_at: string
  timezone: string
  response_maturity_days: number
  interview_maturity_days: number
  min_sample: number
  recommend_sample: number
  filters: Record<string, string | number | null>
  funnel: InterviewFunnel
  round_conversion: RoundConversionStat[]
  drop_off: DropOffStat[]
  latency: InterviewLatency
  by_resume: InterviewCohortStat[]
  by_city: InterviewCohortStat[]
  by_role_family: InterviewCohortStat[]
  by_source: InterviewCohortStat[]
  feedback_tags: CountItem[]
  failure_reasons: CountItem[]
  withdraw_reasons: CountItem[]
  resume_attribution_coverage: CoverageStat
  legacy_interview_events: number
  observations: StrategyObservation[]
  notes: string[]
}

// --------------------------------------------------------------------------
// offers (v0.9)
// --------------------------------------------------------------------------
//
// Mirrors backend/app/schemas/offer.py.
//
// Two distinctions the UI must never blur:
//   * `current_company_summary` is what the company is offering;
//     `latest_counter_summary` is what YOU asked for. They are not the same.
//   * `accepted_summary` comes from a frozen revision id, so a later edit
//     cannot rewrite what a past decision was made on.

export type OfferStatus =
  | 'draft'
  | 'received'
  | 'negotiating'
  | 'accepted'
  | 'declined'
  | 'withdrawn'
  | 'expired'

export type RevisionType =
  | 'initial'
  | 'company_revision'
  | 'candidate_counter'
  | 'final'
  | 'other'

export type RevisionSource = 'company' | 'candidate' | 'manual'

export type OfferCurrency =
  | 'CNY'
  | 'JPY'
  | 'USD'
  | 'EUR'
  | 'HKD'
  | 'SGD'
  | 'GBP'
  | 'other'

export type EquityType = 'rsu' | 'options' | 'restricted_stock' | 'unknown'

export type EmploymentType =
  | 'full_time'
  | 'contract'
  | 'dispatch'
  | 'part_time'
  | 'internship'
  | 'other'

export type OfferRemotePolicy = 'onsite' | 'hybrid' | 'remote' | 'unknown'

export type DeclineReason =
  | 'salary'
  | 'role_content'
  | 'location'
  | 'remote_policy'
  | 'company'
  | 'growth'
  | 'accepted_other_offer'
  | 'visa'
  | 'start_date'
  | 'personal'
  | 'other'

/** none | today | tomorrow | soon | later | past */
export type DeadlineState = 'none' | 'today' | 'tomorrow' | 'soon' | 'later' | 'past'

export interface CompensationSummary {
  currency: OfferCurrency
  base_annual: number | null
  /** Money you can count on in year one. */
  first_year_guaranteed_cash: number | null
  /** Guaranteed plus the TARGET bonus - not money you have. */
  first_year_target_cash: number | null
  steady_state_guaranteed_cash: number | null
  steady_state_target_cash: number | null
  equity_annualized: number | null
  estimated_first_year_total_comp: number | null
  /** A grant exists but could not be valued - show 未计入可比较总包. */
  equity_excluded: boolean
  notes: string[]
}

export interface CompensationFields {
  base_salary_annual?: number | null
  base_salary_monthly?: number | null
  months_per_year?: number | null
  bonus_guaranteed?: number | null
  bonus_target?: number | null
  signing_bonus?: number | null
  stock_value?: number | null
  stock_type?: EquityType | null
  stock_vesting_years?: number | null
  stock_vesting_text?: string | null
  allowances_annual?: number | null
  overtime_pay_text?: string | null
  housing_value?: number | null
  transport_value?: number | null
  other_cash_annual?: number | null
  salary_text_original?: string | null
}

export interface OfferRevisionOut extends CompensationFields {
  id: number
  offer_id: number
  revision_index: number
  revision_type: RevisionType
  revision_type_label: string
  source: RevisionSource
  /** False for candidate counters - what you asked for is not an offer. */
  is_company_offer: boolean
  currency: OfferCurrency
  requested_start_date: string | null
  requested_remote_policy: OfferRemotePolicy | null
  other_request: string | null
  effective_at: string | null
  notes: string | null
  corrects_revision_id: number | null
  created_at: string
  summary: CompensationSummary
}

export interface UpliftOut {
  absolute: number | null
  percentage: number | null
  initial: number | null
  final: number | null
}

export interface NegotiationSummary {
  rounds: number
  candidate_counters: number
  company_revisions: number
  base: UpliftOut
  first_year_guaranteed: UpliftOut
  first_year_target: UpliftOut
  signing_bonus_gained: number | null
  remote_changed: boolean
  start_date_changed: boolean
  /** True only with a counter AND a later company revision. */
  has_negotiation_sequence: boolean
}

export interface OfferOut {
  id: number
  job_id: number
  applied_event_id: number
  interview_process_id: number | null
  status: OfferStatus
  status_label: string
  currency: OfferCurrency
  received_at: string | null
  decision_deadline: string | null
  proposed_start_date: string | null
  employment_type: EmploymentType | null
  work_location: string | null
  remote_policy: OfferRemotePolicy
  probation_text: string | null
  benefits: Record<string, unknown>
  accepted_revision_id: number | null
  declined_revision_id: number | null
  decline_reason: DeclineReason | null
  decline_reason_label: string | null
  decided_at: string | null
  notes: string | null
  created_at: string
  updated_at: string
  revisions: OfferRevisionOut[]
  company: string
  title: string
  city: string | null
  applied_at: string | null
  /** The resume used for THIS cycle, frozen at application time. */
  resume_id: number | null
  resume_label: string | null
  resume_archived: boolean
  /** What the COMPANY is offering - never the latest revision blindly. */
  current_company_revision_id: number | null
  current_company_summary: CompensationSummary | null
  /** The most recent thing YOU asked for. */
  latest_counter_revision_id: number | null
  latest_counter_summary: CompensationSummary | null
  /** Frozen at acceptance; later revisions cannot move it. */
  accepted_summary: CompensationSummary | null
  negotiation: NegotiationSummary
  days_to_deadline: number | null
  deadline_state: DeadlineState
}

export interface LegacyOfferMilestone {
  job_id: number
  event_id: number
  company: string
  title: string
  occurred_at: string
  note: string | null
  legacy_salary_text: string | null
}

export interface OfferBoardResponse {
  timezone: string
  generated_at: string
  pending: OfferOut[]
  negotiating: OfferOut[]
  accepted: OfferOut[]
  closed: OfferOut[]
  legacy_offer_events: LegacyOfferMilestone[]
}

export interface OfferListResponse {
  items: OfferOut[]
  total: number
}

export interface OfferActionResponse {
  offer: OfferOut
  message: string
  job_status: string | null
}

export interface OfferComparisonRow {
  offer_id: number
  job_id: number
  company: string
  title: string
  city: string | null
  status: OfferStatus
  currency: OfferCurrency
  base_annual: number | null
  first_year_guaranteed_cash: number | null
  first_year_target_cash: number | null
  estimated_first_year_total_comp: number | null
  equity_excluded: boolean
  bonus_target: number | null
  signing_bonus: number | null
  stock_value: number | null
  stock_type: EquityType | null
  remote_policy: OfferRemotePolicy
  work_location: string | null
  proposed_start_date: string | null
  decision_deadline: string | null
  deadline_state: DeadlineState
  benefits: Record<string, unknown>
  resume_label: string | null
}

/** Side by side, deliberately with no overall winner and no FX conversion. */
export interface OfferComparisonResponse {
  rows: OfferComparisonRow[]
  currencies: OfferCurrency[]
  mixed_currency: boolean
  message: string
  notes: string[]
}

export interface ParsedSalaryOut {
  base_salary_annual: number | null
  base_salary_monthly: number | null
  months_per_year: number | null
  currency: OfferCurrency | null
  confidence: string
  matched_text: string
  notes: string[]
  message: string
}

export interface OfferCreatePayload {
  confirmed: true
  applied_event_id?: number | null
  interview_process_id?: number | null
  currency?: OfferCurrency
  received_at?: string | null
  decision_deadline?: string | null
  proposed_start_date?: string | null
  employment_type?: EmploymentType | null
  work_location?: string | null
  remote_policy?: OfferRemotePolicy
  probation_text?: string | null
  benefits?: Record<string, unknown>
  notes?: string | null
  initial?: CompensationFields | null
}

export interface RevisionCreatePayload extends CompensationFields {
  revision_type: RevisionType
  source: RevisionSource
  effective_at?: string | null
  notes?: string | null
  requested_start_date?: string | null
  requested_remote_policy?: OfferRemotePolicy | null
  other_request?: string | null
  corrects_revision_id?: number | null
}

export interface CounterPayload extends CompensationFields {
  requested_start_date?: string | null
  requested_remote_policy?: OfferRemotePolicy | null
  other_request?: string | null
  notes?: string | null
}

// --- offer analytics ---

export interface MoneyStat {
  currency: string
  sample: number
  median: number | null
  p25: number | null
  p75: number | null
  minimum: number | null
  maximum: number | null
}

export interface CurrencyCompensation {
  currency: string
  offers: number
  base_annual: MoneyStat | null
  first_year_guaranteed: MoneyStat | null
  first_year_target: MoneyStat | null
  estimated_total_comp: MoneyStat | null
}

export interface NegotiationUpliftStat {
  currency: string
  sample: number
  median_base_uplift: number | null
  median_base_uplift_pct: number | null
  median_guaranteed_uplift: number | null
  offers_with_signing_gained: number
  with_full_sequence: number
}

export interface OfferCohortStat extends CohortStat {
  offers_recorded: number
  accepted: number
  declined: number
  offer_reach_rate: RateStat
  interview_to_offer_rate: RateStat
  resume_id: number | null
  resume_archived: boolean
}

export interface OfferFunnel {
  applications: number
  reached_any_interview: number
  offers: number
  accepted: number
  declined: number
  withdrawn: number
  expired: number
  pending: number
  application_to_offer: RateStat
  interview_to_offer: RateStat
  offer_acceptance_rate: RateStat
}

export interface OfferAnalyticsResult {
  window: TimeWindow
  generated_at: string
  timezone: string
  min_sample: number
  recommend_sample: number
  filters: Record<string, string | number | null>
  funnel: OfferFunnel
  /** One entry per currency. Never merged - there is no FX model. */
  compensation: CurrencyCompensation[]
  accepted_compensation: CurrencyCompensation[]
  negotiation: NegotiationUpliftStat[]
  by_resume: OfferCohortStat[]
  by_city: OfferCohortStat[]
  by_role_family: OfferCohortStat[]
  by_source: OfferCohortStat[]
  decline_reasons: CountItem[]
  resume_attribution_coverage: CoverageStat
  legacy_offer_events: number
  observations: StrategyObservation[]
  notes: string[]
}

// --- decision support (v1.0) ---

export type DecisionDimension =
  | 'compensation'
  | 'career_growth'
  | 'role_fit'
  | 'remote_work'
  | 'location'
  | 'work_life_balance'
  | 'company_stability'
  | 'technology_fit'
  | 'language_environment'
  | 'visa_support'
  | 'brand_value'

export type DealBreakerKind =
  | 'minimum_guaranteed_cash'
  | 'requires_visa_support'
  | 'requires_remote_or_hybrid'
  | 'required_location'
  | 'latest_start_date'

/** pass / fail / unknown. `unknown` means the fact was never recorded. */
export type DealBreakerResult = 'passed' | 'failed' | 'unknown'

export interface DealBreakerItem {
  kind: DealBreakerKind
  value: string | number | boolean | null
}

export interface DecisionProfileOut {
  id: number
  name: string
  /** Raw, as entered. */
  weights: Partial<Record<DecisionDimension, number>>
  /** The same weights scaled to sum to 1 - what the score actually used. */
  normalized_weights: Partial<Record<DecisionDimension, number>>
  deal_breakers: DealBreakerItem[]
  /** User-entered only. v1.0 looks nothing up. */
  fx_rates: Record<string, number>
  base_currency: OfferCurrency
  notes: string | null
  created_at: string
  updated_at: string
}

export interface DecisionProfileUpdate {
  name?: string
  weights?: Partial<Record<DecisionDimension, number>>
  deal_breakers?: DealBreakerItem[]
  fx_rates?: Record<string, number>
  base_currency?: OfferCurrency
  notes?: string | null
}

export interface AssessmentOut {
  offer_id: number
  ratings: Partial<Record<DecisionDimension, number>>
  notes: string | null
  target_total_cash: number | null
  ideal_total_cash: number | null
  minimum_total_cash: number | null
  updated_at: string | null
}

export interface AssessmentUpdate {
  /** `null` clears a rating - "no view" is not a 1. */
  ratings?: Partial<Record<DecisionDimension, number | null>>
  notes?: string | null
  target_total_cash?: number | null
  ideal_total_cash?: number | null
  minimum_total_cash?: number | null
  clear_targets?: boolean
}

export interface DimensionScoreOut {
  dimension: DecisionDimension
  label: string
  /** 0-1, or null when there was nothing to score. Never 0 for "unknown". */
  score: number | null
  weight: number
  /** weight x score. Shown so the total is never a black box. */
  contribution: number | null
  known: boolean
  source: 'derived' | 'rating' | 'missing'
  detail: string
}

export interface DealBreakerCheckOut {
  kind: DealBreakerKind
  label: string
  result: DealBreakerResult
  detail: string
}

export interface OfferScoreOut {
  offer_id: number
  company: string
  title: string
  total_score: number | null
  /** Share of total weight that had data behind it. */
  coverage: number
  dimension_scores: DimensionScoreOut[]
  weighted_contributions: Partial<Record<DecisionDimension, number>>
  missing_dimensions: DecisionDimension[]
  deal_breakers: DealBreakerCheckOut[]
  warnings: string[]
  strengths: string[]
  trade_offs: string[]
  /** Display only. Urgency never enters the score. */
  days_to_deadline: number | null
  deadline_state: DeadlineState
  compensation_comparable: boolean
  currency: OfferCurrency
  revision_id: number | null
  fx_rate_used: number | null
  guaranteed_cash: number | null
  target_cash: number | null
}

export interface DecisionComparisonOut {
  offers: OfferScoreOut[]
  normalized_weights: Partial<Record<DecisionDimension, number>>
  base_currency: OfferCurrency
  winner_offer_id: number | null
  winner_blocked_reason: string
  min_coverage: number
  mixed_currency: boolean
  fx_rates_used: Record<string, number>
  notes: string[]
  message: string
}

export interface SnapshotSummary {
  id: number
  name: string
  offer_ids: number[]
  created_at: string
  notes: string | null
  winner_offer_id: number | null
  companies: string[]
}

export interface SnapshotDetail extends SnapshotSummary {
  /** The frozen document. Later edits never change it. */
  payload: Record<string, unknown>
}

export interface SnapshotListResponse {
  items: SnapshotSummary[]
  total: number
}

export interface NegotiationPositionOut {
  currency: OfferCurrency
  current_company_cash: number | null
  latest_candidate_ask: number | null
  /** ask - current. Null when either side is missing. */
  gap: number | null
  target_total_cash: number | null
  ideal_total_cash: number | null
  minimum_total_cash: number | null
  /** Below-minimum warns. It never declines anything. */
  warnings: string[]
}

export interface CompetingOfferOut {
  offer_id: number
  company: string
  title: string
  status: string
  decision_deadline: string | null
  days_to_deadline: number | null
}

export interface CompetingOffersResponse {
  items: CompetingOfferOut[]
  total: number
  message: string
}

// --- console attention subview (M2) ---

export interface ConsoleAttentionOut {
  generated_at: string
  analysis_pending: { count: number; items: JobListItem[] }
  queue: { summary: QueueSummary; items: ApplicationProposal[] }
  recruiter: { summary: InboxSummary; items: ConversationSummaryOut[] }
  interviews: UpcomingInterviewsResponse
  offers: { pending_count: number; negotiating_count: number; items: OfferOut[] }
}

// --- console audit trail (M3) ---

export type OrchestrationEventType = 'opened' | 'reviewed' | 'dismissed'

export interface OrchestrationEventOut {
  id: number
  task_id: number
  job_id: number | null
  event_type: OrchestrationEventType
  note: string | null
  created_at: string
}

export interface OrchestrationEventListResponse {
  items: OrchestrationEventOut[]
  total: number
}

export interface OrchestrationEventCreatePayload {
  event_type: OrchestrationEventType
  job_id?: number | null
  note?: string | null
}
// Opt-in bounded search-to-match review. Scores never represent human actions.
export interface AutoMatchReview {
  task_id: number; enabled: boolean; state: string | null
  cap: number; used: number; completed: number; failed: number; uncertain: number
  resume_id: number | null; model: string | null
  items: { job_id: number; title: string; company: string; score: number | null;
    verdict: string | null; state: string; cached: boolean; error: string | null;
    bucket: string; review_reasons: string[]; summary: string }[]
}

// --- M5b: bounded cross-task matching + unified human review ---

export interface CrossTaskSourceOut {
  task_id: number
  name: string
  city: string | null
  keyword: string | null
}

export interface CrossTaskReviewItemOut {
  job_id: number
  title: string
  company: string
  city: string | null
  salary_text: string | null
  experience_text: string | null
  education_text: string | null
  sources: CrossTaskSourceOut[]
  cached: boolean
  score: number | null
  verdict: Verdict | null
  bucket: string
  review_reasons: string[]
  summary: string
}

export interface CrossTaskMatchPlanOut {
  task_ids: number[]
  task_count: number
  active_resume_id: number
  active_resume_name: string
  model: string
  fingerprint: string
  unique_jobs: number
  cached_jobs: number
  pending_jobs: number
  max_new_calls: number
  items: CrossTaskReviewItemOut[]
}

export interface CrossTaskMatchOutcomeOut {
  job_id: number
  cached: boolean
  analyzed: boolean
  error: string | null
  category: string | null
  http_status: number | null
}

export interface CrossTaskMatchRunResponse {
  plan: CrossTaskMatchPlanOut
  results: CrossTaskMatchOutcomeOut[]
  calls_used: number
  analyzed: number
  failed: number
}
// Existing SearchPlan API; counts are rendered DOM observations, not viewport pixels.
export interface SearchPlanTask {
  id: number
  city: string | null
  keywords: string | null
  early_career_policy: 'exclude' | 'include' | 'only'
  state: string | null
  max_candidates: number | null
  current_url: string | null
  scroll_round: number
  visible_jobs: number
  observed_jobs: number
  new_jobs: number
  duplicate_jobs: number
  imported_jobs: number
  no_new_rounds: number
  current_candidate: string | null
  last_action: string | null
  last_error: string | null
  paused_reason: string | null
  updated_at: string
}

export interface QuickSearchPrepareResponse {
  tasks: SearchPlanTask[]
  active_resume_name: string
  keyword_source: 'career_strategy'
}

export interface SearchPlanOptions {
  supported_cities: string[]
  max_selected_cities: number
  max_batch_tasks: number
}
export interface SalaryBackfillPlanItem {
  job_id: number
  company: string
  title: string
  source_url: string
}

export interface SalaryBackfillPlan {
  total_jobs: number
  salary_present: number
  salary_missing: number
  eligible_jobs: number
  ineligible_jobs: number
  fingerprint: string
  items: SalaryBackfillPlanItem[]
}

export interface SalaryBackfillRun {
  id: number
  state: 'pending' | 'running' | 'paused' | 'completed' | 'cancelled'
  total_jobs: number
  processed_jobs: number
  updated_jobs: number
  unavailable_jobs: number
  failed_jobs: number
  session_processed: number
  session_cap: number
  current_job_id: number | null
  paused_reason: string | null
  last_action: string | null
  last_error: string | null
  created_at: string
  updated_at: string
  items: (SalaryBackfillPlanItem & { position: number; state: string; reason: string | null })[]
}
