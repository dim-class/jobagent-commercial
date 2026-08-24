// 简历表现 (v0.7)
//
// "Which resume actually performed better?" - answered from recorded outcomes,
// and left unanswered when the data cannot support an answer.
//
// The page keeps two things visibly apart:
//   * 真实转化 - what happened after applications that used each variant;
//   * AI 匹配分 - how the model scored each variant on the same JDs.
// A variant can read better to a model and still get fewer replies.
//
// Everything here is deterministic backend data: refreshing costs nothing.

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { Alert, Card, EmptyState, Loading, Stat, formatDateTime } from '@/components/ui'
import type {
  RateStat,
  ResumeAnalyticsResult,
  ResumeBreakdownRow,
  ResumeCohortStat,
  ResumeListItem,
  SampleConfidence,
  TimeWindow,
  UnattributedResponse,
} from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

const WINDOWS: { value: TimeWindow; label: string }[] = [
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: '90d', label: '近 90 天' },
  { value: 'all', label: '全部' },
]

const CONFIDENCE_LABEL: Record<SampleConfidence, string> = {
  insufficient: '样本不足',
  low: '参考',
  moderate: '中等',
  strong: '较可靠',
}

const CONFIDENCE_CLASS: Record<SampleConfidence, string> = {
  insufficient: 'chip',
  low: 'chip',
  moderate: 'chip chip-good',
  strong: 'chip chip-good',
}

/** Below this, too much is unattributed for a ranking to mean anything. */
const MIN_ATTRIBUTION_COVERAGE = 0.6

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 100)}%`
}

/** A rate never appears without its fraction and interval. */
function RateCell({ stat }: { stat: RateStat }) {
  if (stat.denominator === 0) {
    return (
      <div>
        <div className="cell-title">—</div>
        <div className="cell-sub">暂无成熟样本</div>
      </div>
    )
  }
  return (
    <div>
      <div className="cell-title">
        {pct(stat.rate)}{' '}
        <span className="faint small">
          （{stat.numerator}/{stat.denominator}）
        </span>
      </div>
      <div className="cell-sub">
        95% 区间 {pct(stat.ci_low)}–{pct(stat.ci_high)}
      </div>
    </div>
  )
}

function ConfidenceChip({ value }: { value: SampleConfidence }) {
  return <span className={CONFIDENCE_CLASS[value]}>{CONFIDENCE_LABEL[value]}</span>
}

function VariantTable({ rows, empty }: { rows: ResumeCohortStat[]; empty: string }) {
  if (!rows.length) return <p className="faint small mt-0">{empty}</p>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>简历版本</th>
            <th>投递</th>
            <th>成熟样本</th>
            <th>回复</th>
            <th>成熟回复率</th>
            <th>面试</th>
            <th>面试率</th>
            <th>置信度</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>
                <div className="cell-title">{row.label}</div>
                <div className="cell-sub">
                  {row.is_active_analysis_resume ? '当前AI分析简历 · ' : ''}
                  {row.archived ? '已归档 · ' : ''}
                  {row.variant_group ?? ''}
                </div>
              </td>
              <td>{row.applications}</td>
              <td>{row.mature_applications}</td>
              <td>{row.replies}</td>
              <td>
                <RateCell stat={row.mature_reply_rate} />
              </td>
              <td>{row.interviews}</td>
              <td>
                <RateCell stat={row.interview_rate} />
              </td>
              <td>
                <ConfidenceChip value={row.mature_reply_rate.confidence} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Breakdown({ rows, title, sub }: { rows: ResumeBreakdownRow[]; title: string; sub: string }) {
  return (
    <Card title={title} sub={sub}>
      {!rows.length ? (
        <p className="faint small mt-0">
          还没有任何一类岗位同时用两份以上简历投递过足够多次。
          只有在同一类岗位内比较，结果才有参考价值。
        </p>
      ) : (
        rows.map((row) => (
          <div key={`${row.dimension}-${row.dimension_key}`} className="mt-2">
            <strong className="small">{row.dimension_label}</strong>
            <VariantTable rows={row.resumes} empty="暂无数据" />
          </div>
        ))
      )}
    </Card>
  )
}

export default function ResumeAnalyticsPage() {
  const [data, setData] = useState<ResumeAnalyticsResult | null>(null)
  const [resumes, setResumes] = useState<ResumeListItem[]>([])
  const [unattributed, setUnattributed] = useState<UnattributedResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [busyJob, setBusyJob] = useState<number | null>(null)

  const [timeWindow, setTimeWindow] = useState<TimeWindow>('all')
  const [city, setCity] = useState('')
  const [roleFamily, setRoleFamily] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [result, list, missing] = await Promise.all([
        api.resumeAnalytics({ window: timeWindow, city, roleFamily }),
        api.listResumes(true),
        api.unattributedApplications(),
      ])
      setData(result)
      setResumes(list)
      setUnattributed(missing)
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载简历分析失败' })
    } finally {
      setLoading(false)
    }
  }, [timeWindow, city, roleFamily])

  useEffect(() => {
    void load()
  }, [load])

  const cityOptions = useMemo(
    () => Array.from(new Set(unattributed?.items.map((i) => i.city).filter(Boolean))) as string[],
    [unattributed],
  )

  const coverage = data?.attribution_coverage
  const coverageRatio = coverage?.ratio ?? null
  const lowCoverage = coverageRatio !== null && coverageRatio < MIN_ATTRIBUTION_COVERAGE
  // A "winner" needs both decent coverage and two adequately-sampled variants.
  const comparable =
    (data?.by_resume ?? []).filter((r) => r.mature_reply_rate.confidence !== 'insufficient')
      .length >= 2
  const showWinner = !lowCoverage && comparable

  async function attribute(jobId: number, resumeId: number | null, appliedEventId: number | null) {
    setBusyJob(jobId)
    try {
      await api.attributeResume(
        jobId,
        resumeId,
        resumeId === null ? 'unknown' : 'used',
        appliedEventId,
      )
      setFeedback({ tone: 'success', text: '已记录该次投递使用的简历' })
      await load()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '保存失败' })
    } finally {
      setBusyJob(null)
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>简历表现</h1>
          <p>
            用真实投递结果比较简历版本。归属以<strong>投递当时记录的简历</strong>为准，
            换掉「当前AI分析简历」不会改变任何历史归属。
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" to="/resume">
            管理简历版本
          </Link>
          <button type="button" className="btn-ghost" onClick={() => void load()}>
            刷新
          </button>
        </div>
      </div>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      {data ? <Alert tone="warn">{data.observational_warning}</Alert> : null}

      <Card title="筛选">
        <div className="filters">
          <div className="field">
            <label htmlFor="window">时间窗</label>
            <select
              id="window"
              value={timeWindow}
              onChange={(event) => setTimeWindow(event.target.value as TimeWindow)}
            >
              {WINDOWS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="city">城市</label>
            <input
              id="city"
              value={city}
              placeholder="全部"
              onChange={(event) => setCity(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="role">方向</label>
            <input
              id="role"
              value={roleFamily}
              placeholder="全部，如 DevOps"
              onChange={(event) => setRoleFamily(event.target.value)}
            />
          </div>
        </div>
        {cityOptions.length ? (
          <p className="field-hint mt-1">未记录简历的投递涉及城市：{cityOptions.join('、')}</p>
        ) : null}
      </Card>

      {loading ? <Loading text="正在统计…" /> : null}

      {!loading && data && data.summary.applications === 0 ? (
        <EmptyState
          icon="📄"
          title="还没有可比较的投递记录"
          text="从下一次投递开始，在「确认已投递」时选择你实际使用的简历，这里就会开始积累数据。"
        />
      ) : null}

      {!loading && data && data.summary.applications > 0 ? (
        <>
          <div className="grid grid-stats">
            <Stat label="投递总数" value={data.summary.applications} hint="含未记录简历的" />
            <Stat
              label="已记录简历"
              value={coverage ? `${coverage.covered}/${coverage.total}` : '—'}
              hint={`归属覆盖率 ${pct(coverageRatio)}`}
            />
            <Stat label="简历版本" value={data.by_resume.length} hint="有投递记录的版本" />
            <Stat
              label="整体成熟回复率"
              value={pct(data.summary.mature_reply_rate.rate)}
              hint={`${data.summary.mature_reply_rate.numerator}/${data.summary.mature_reply_rate.denominator}`}
            />
          </div>

          {lowCoverage ? (
            <Alert tone="warn">
              只有 {pct(coverageRatio)} 的投递记录了实际使用的简历，
              目前<strong>不足以比较各个版本的优劣</strong>。
              可以在下方「补充历史简历」里补齐，或从今后的投递开始记录。
            </Alert>
          ) : null}

          {!showWinner && !lowCoverage ? (
            <Alert tone="info">
              目前还没有两个样本量足够的简历版本可以互相比较，先继续积累投递记录。
            </Alert>
          ) : null}

          <Card
            title="各版本真实转化"
            sub="按样本充足度排序，再按置信区间下界；小样本不会因为运气好排到前面"
          >
            <VariantTable rows={data.by_resume} empty="还没有任何投递记录了所用简历" />
            {data.unattributed ? (
              <p className="field-hint mt-1">
                另有 {data.unattributed.applications} 次投递未记录简历
                （其中 {data.unattributed.replies} 次收到回复）。
                这些记录计入整体数据，但<strong>不会归到任何版本名下</strong>。
              </p>
            ) : null}
          </Card>

          {data.observations.length ? (
            <Card title="观察到的结果" sub="模板生成，可复算；描述现象，不解释原因">
              <ul className="bullet-list">
                {data.observations.map((observation, index) => (
                  <li key={`${observation.dimension}-${observation.target}-${index}`}>
                    {observation.text}
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          <Breakdown
            rows={data.by_resume_role}
            title="同类岗位内比较 · 按方向"
            sub="不同版本常被用在不同岗位上，同类岗位内的比较才有意义"
          />
          <Breakdown
            rows={data.by_resume_city}
            title="同类岗位内比较 · 按城市"
            sub="至少两个版本、各有足够样本时才会出现"
          />

          <Card
            title="AI 匹配分对比"
            sub="这是模型对「简历与 JD 的匹配度」的判断，不是 HR 的真实反馈"
          >
            {data.fit_comparison.comparable_jobs === 0 ? (
              <p className="faint small mt-0">
                还没有岗位用两份以上简历分析过。在岗位详情页用「按其他简历分析」或「比较简历」即可积累。
              </p>
            ) : (
              <>
                <div className="chip-list">
                  {data.fit_comparison.resumes.map((item) => (
                    <span key={item.resume_id} className="chip">
                      {item.label}：平均 {item.average_score ?? '—'} 分 · 最高分{' '}
                      {item.best_on_jobs}/{data.fit_comparison.comparable_jobs} 次
                    </span>
                  ))}
                </div>
                <p className="field-hint">
                  基于 {data.fit_comparison.comparable_jobs} 个用多份简历分析过的岗位。
                  空白单元格表示还没分析过，系统不会自动补齐（那会产生费用）。
                </p>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>岗位</th>
                        {data.fit_comparison.columns.map((column) => (
                          <th key={column.key}>{column.label}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.fit_comparison.matrix.map((row) => (
                        <tr key={row.job_id}>
                          <td>
                            <Link className="cell-title" to={`/jobs/${row.job_id}`}>
                              {row.company}
                            </Link>
                            <div className="cell-sub">
                              {row.title}
                              {row.city ? ` · ${row.city}` : ''}
                            </div>
                          </td>
                          {row.cells.map((cell) => (
                            <td key={cell.resume_id}>{cell.score ?? '—'}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </Card>

          {unattributed && unattributed.total > 0 ? (
            <Card
              title="补充历史简历"
              sub="只有你知道当时投的是哪一份；系统不会替你猜"
            >
              <p className="faint small mt-0">{unattributed.message}</p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>公司 / 岗位</th>
                      <th>投递时间</th>
                      <th>当时使用的简历</th>
                    </tr>
                  </thead>
                  <tbody>
                    {unattributed.items.map((item) => (
                      <tr key={`${item.job_id}-${item.applied_event_id}`}>
                        <td>
                          <Link className="cell-title" to={`/jobs/${item.job_id}`}>
                            {item.company}
                          </Link>
                          <div className="cell-sub">{item.title}</div>
                        </td>
                        <td className="nowrap">{formatDateTime(item.applied_at)}</td>
                        <td>
                          <select
                            disabled={busyJob === item.job_id}
                            defaultValue=""
                            onChange={(event) => {
                              const value = event.target.value
                              if (!value) return
                              void attribute(
                                item.job_id,
                                value === 'unknown' ? null : Number(value),
                                item.applied_event_id,
                              )
                            }}
                          >
                            <option value="">请选择…</option>
                            {resumes.map((resume) => (
                              <option key={resume.id} value={resume.id}>
                                {resume.label}
                                {resume.archived ? '（已归档）' : ''}
                              </option>
                            ))}
                            <option value="unknown">不记得</option>
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="field-hint mt-1">
                补充会追加一条「补充投递简历」记录，原始的投递事件不会被改写。
              </p>
            </Card>
          ) : null}
        </>
      ) : null}
    </>
  )
}
