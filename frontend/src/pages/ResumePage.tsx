import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import { Alert, Card, ChipList, EmptyState, Loading, formatDateTime } from '@/components/ui'
import type { ResumeDetail, ResumeListItem } from '@/types'

type Feedback = { tone: 'success' | 'error' | 'info' | 'warn'; text: string } | null

export default function ResumePage() {
  const fileInput = useRef<HTMLInputElement>(null)

  const [active, setActive] = useState<ResumeDetail | null>(null)
  const [resumes, setResumes] = useState<ResumeListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [showArchived, setShowArchived] = useState(false)
  const [busy, setBusy] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setResumes(await api.listResumes(showArchived))
      try {
        setActive(await api.getActiveResume())
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 404)) throw err
        setActive(null)
      }
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '加载简历失败' })
    } finally {
      setLoading(false)
    }
  }, [showArchived])

  useEffect(() => {
    void load()
  }, [load])

  async function upload(file: File) {
    setUploading(true)
    setFeedback({ tone: 'info', text: `正在解析「${file.name}」…` })
    try {
      const response = await api.uploadResume(file)
      setFeedback({ tone: 'success', text: response.message })
      await load()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '上传失败' })
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  async function activate(id: number) {
    try {
      await api.activateResume(id)
      setFeedback({ tone: 'success', text: '已切换当前简历' })
      await load()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '切换失败' })
    }
  }

  async function run(id: number, action: () => Promise<unknown>, success: string) {
    setBusy(id)
    try {
      await action()
      setFeedback({ tone: 'success', text: success })
      await load()
    } catch (err) {
      setFeedback({ tone: 'error', text: err instanceof ApiError ? err.message : '操作失败' })
    } finally {
      setBusy(null)
    }
  }

  function rename(resume: ResumeListItem) {
    const next = globalThis.prompt('这份简历叫什么？', resume.label)
    if (next === null || next.trim() === resume.label) return
    void run(
      resume.id,
      () => api.updateResume(resume.id, { variant_name: next.trim() }),
      '已重命名',
    )
  }

  function clone(resume: ResumeListItem) {
    const next = globalThis.prompt('新版本的名称', `${resume.label} v2`)
    if (next === null) return
    void run(
      resume.id,
      () => api.cloneResume(resume.id, next.trim() || undefined),
      '已复制为新版本；它不会自动成为当前AI分析简历',
    )
  }

  const profile = active?.parsed_profile

  return (
    <>
      <header className="page-head">
        <div>
          <h1>简历</h1>
          <p>本地解析 PDF / DOCX，不上传到任何第三方服务；AI 分析时只发送必要摘要。</p>
        </div>
        <div className="page-actions">
          <input
            ref={fileInput}
            type="file"
            accept=".pdf,.docx,.txt"
            style={{ display: 'none' }}
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) void upload(file)
            }}
          />
          <button
            type="button"
            className="btn-primary"
            disabled={uploading}
            onClick={() => fileInput.current?.click()}
          >
            {uploading ? '解析中…' : '上传简历'}
          </button>
        </div>
      </header>

      {feedback ? (
        <Alert tone={feedback.tone} onDismiss={() => setFeedback(null)}>
          {feedback.text}
        </Alert>
      ) : null}

      {loading && !active ? <Loading text="正在加载简历…" /> : null}

      {!loading && !active ? (
        <Card>
          <EmptyState
            icon="📄"
            title="还没有简历"
            text="上传一份 PDF 或 DOCX 简历，系统会在本地提取文本、技能与证书。扫描件需要先做 OCR。"
            action={
              <button type="button" className="btn-primary" onClick={() => fileInput.current?.click()}>
                上传简历
              </button>
            }
          />
        </Card>
      ) : null}

      {active ? (
        <>
          <Card title="当前简历" sub={`${active.file_type.toUpperCase()} · 上传于 ${formatDateTime(active.created_at)}`}>
            <dl className="meta-list">
              <dt>文件名</dt>
              <dd>{active.filename}</dd>
              <dt>提取字数</dt>
              <dd>{active.text_length.toLocaleString('zh-CN')} 字</dd>
              <dt>识别经验</dt>
              <dd>{profile?.years_of_experience ? `约 ${profile.years_of_experience} 年` : '未识别'}</dd>
              <dt>识别章节</dt>
              <dd>{profile?.sections_detected?.length ? profile.sections_detected.join('、') : '未识别到标准章节'}</dd>
              <dt>内容哈希</dt>
              <dd className="mono">{active.content_hash.slice(0, 16)}…</dd>
            </dl>
          </Card>

          <div className="grid grid-2">
            <Card title="识别到的技能">
              <ChipList items={profile?.skills ?? []} tone="good" />
            </Card>
            <Card title="证书 / 认证">
              <ChipList items={profile?.certifications ?? []} />
            </Card>
          </div>

          {profile?.summary ? (
            <Card title="个人简介">
              <p className="mt-0">{profile.summary}</p>
            </Card>
          ) : null}

          <div className="grid grid-2">
            <Card title="工作经历">
              {profile?.work_experience?.length ? (
                <ul className="bullet-list">
                  {profile.work_experience.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              ) : (
                <p className="faint small mt-0">未识别到工作经历章节（不影响分析）。</p>
              )}
            </Card>
            <Card title="项目经历">
              {profile?.projects?.length ? (
                <ul className="bullet-list">
                  {profile.projects.map((item, index) => (
                    <li key={index}>{item}</li>
                  ))}
                </ul>
              ) : (
                <p className="faint small mt-0">未识别到项目经历章节（不影响分析）。</p>
              )}
            </Card>
          </div>

          <Card title="提取文本预览" sub="最多显示前 4000 字">
            <div className="jd-text">{active.raw_text_preview}</div>
          </Card>
        </>
      ) : null}

      {resumes.length > 1 ? (
        <Card
          title="简历版本"
          sub="「当前AI分析简历」只决定新岗位用哪份来分析；投递时用了哪份，由你在确认投递时单独记录"
          actions={
            <>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={showArchived}
                  onChange={(event) => setShowArchived(event.target.checked)}
                />
                <span className="small">显示已归档</span>
              </label>
              <Link className="btn btn-sm" to="/resume-analytics">
                简历表现
              </Link>
            </>
          }
        >
          <div className="grid grid-2">
            {resumes.map((resume) => (
              <section key={resume.id} className="job-card">
                <div className="job-card-head">
                  <div>
                    <div className="cell-title">{resume.label}</div>
                    <div className="cell-sub">
                      {resume.filename} · {resume.file_type.toUpperCase()} ·{' '}
                      {formatDateTime(resume.created_at)}
                    </div>
                  </div>
                  <div>
                    {resume.is_active ? (
                      <span className="badge badge-good">当前AI分析简历</span>
                    ) : null}
                    {resume.archived ? <span className="chip">已归档</span> : null}
                  </div>
                </div>

                {resume.variant_group || resume.notes ? (
                  <p className="job-card-summary">
                    {resume.variant_group ? `${resume.variant_group} · ` : ''}
                    {resume.notes ?? ''}
                  </p>
                ) : null}

                <ChipList items={resume.skills.slice(0, 8)} tone="good" />

                <div className="meta-list mt-1">
                  <span>投递 {resume.applications}</span>
                  <span>成熟 {resume.mature_applications}</span>
                  <span>回复 {resume.replies}</span>
                  <span>面试 {resume.interviews}</span>
                  <span>已分析岗位 {resume.analyzed_jobs}</span>
                </div>

                <div className="btn-row">
                  {resume.is_active ? null : resume.archived ? (
                    <button
                      type="button"
                      className="btn-sm"
                      disabled={busy === resume.id}
                      onClick={() =>
                        void run(resume.id, () => api.unarchiveResume(resume.id), '已取消归档')
                      }
                    >
                      取消归档
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn-primary btn-sm"
                      disabled={busy === resume.id}
                      onClick={() => void activate(resume.id)}
                    >
                      设为当前分析简历
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-sm"
                    disabled={busy === resume.id}
                    onClick={() => rename(resume)}
                  >
                    重命名
                  </button>
                  <button
                    type="button"
                    className="btn-sm"
                    disabled={busy === resume.id}
                    onClick={() => clone(resume)}
                  >
                    复制为新版本
                  </button>
                  {!resume.archived && !resume.is_active ? (
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      disabled={busy === resume.id}
                      onClick={() =>
                        void run(resume.id, () => api.archiveResume(resume.id), '已归档')
                      }
                    >
                      归档
                    </button>
                  ) : null}
                </div>
              </section>
            ))}
          </div>
          <p className="field-hint mt-1">
            归档只是停止在新投递里使用它，历史数据会完整保留。简历不能被删除，
            否则已投递记录会失去归属。
          </p>
        </Card>
      ) : null}
    </>
  )
}
