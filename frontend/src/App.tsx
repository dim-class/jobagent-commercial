import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { api } from '@/api/client'
import { Alert } from '@/components/ui'
import ApplicationQueuePage from '@/pages/ApplicationQueuePage'
import BrowserCapturePage from '@/pages/BrowserCapturePage'
import CareerAnalyticsPage from '@/pages/CareerAnalyticsPage'
import ConsolePage from '@/pages/ConsolePage'
import DashboardPage from '@/pages/DashboardPage'
import DecisionPage from '@/pages/DecisionPage'
import InterviewAnalyticsPage from '@/pages/InterviewAnalyticsPage'
import InterviewPipelinePage from '@/pages/InterviewPipelinePage'
import InterviewProcessPage from '@/pages/InterviewProcessPage'
import QuickCapturePage from '@/pages/QuickCapturePage'
import RecruiterConversationPage from '@/pages/RecruiterConversationPage'
import RecruiterInboxPage from '@/pages/RecruiterInboxPage'
import JobDetailPage from '@/pages/JobDetailPage'
import JobsPage from '@/pages/JobsPage'
import OfferAnalyticsPage from '@/pages/OfferAnalyticsPage'
import OfferBoardPage from '@/pages/OfferBoardPage'
import OfferComparisonPage from '@/pages/OfferComparisonPage'
import OfferDetailPage from '@/pages/OfferDetailPage'
import ResumeAnalyticsPage from '@/pages/ResumeAnalyticsPage'
import ResumePage from '@/pages/ResumePage'
import SettingsPage from '@/pages/SettingsPage'
import StrategyPage from '@/pages/StrategyPage'
import type { HealthResponse } from '@/types'

const NAV = [
  { to: '/dashboard', label: '仪表盘', icon: '📊' },
  { to: '/console', label: '任务控制台', icon: '🗃️' },
  { to: '/queue', label: '投递队列', icon: '🎯' },
  { to: '/jobs', label: '岗位库', icon: '💼' },
  { to: '/interviews', label: '面试', icon: '🗓️' },
  { to: '/offers', label: 'Offer', icon: '📨' },
  { to: '/recruiter', label: 'HR沟通', icon: '💬' },
  { to: '/quick-capture', label: '快速采集', icon: '⚡' },
  { to: '/capture', label: '浏览器采集', icon: '🧭' },
  { to: '/resume', label: '简历', icon: '📄' },
  { to: '/resume-analytics', label: '简历表现', icon: '🧪' },
  { to: '/analytics', label: '策略分析', icon: '📈' },
  { to: '/strategy', label: '求职策略', icon: '🎯' },
  { to: '/settings', label: '设置', icon: '⚙️' },
]

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .health()
      .then((data) => {
        if (!cancelled) {
          setHealth(data)
          setOffline(false)
        }
      })
      .catch(() => {
        if (!cancelled) setOffline(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-title">AI 求职助手</span>
          <span className="brand-sub">本地运行 · v{health?.version ?? '0.1.0'}</span>
        </div>

        <nav className="nav">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
            >
              <span className="nav-icon" aria-hidden>
                {item.icon}
              </span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div>{health?.openai_configured ? '🟢 OpenAI 已配置' : '🟡 未配置 OpenAI'}</div>
          <div>自动投递：关闭</div>
        </div>
      </aside>

      <main className="content">
        {offline ? (
          <Alert tone="error">
            无法连接后端服务。请在项目根目录运行 <code className="mono">.\scripts\dev.ps1 backend</code>
            ，确认 <code className="mono">http://127.0.0.1:8000/health</code> 可访问后刷新页面。
          </Alert>
        ) : null}

        {health && !health.openai_configured ? (
          <Alert tone="warn">
            尚未配置 <code className="mono">OPENAI_API_KEY</code>，AI 匹配分析不可用。
            请在项目根目录的 <code className="mono">.env</code> 中填写后重启后端；其余功能可正常使用。
          </Alert>
        ) : null}

        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/console" element={<ConsolePage />} />
          <Route path="/queue" element={<ApplicationQueuePage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/interviews" element={<InterviewPipelinePage />} />
          <Route path="/interviews/:processId" element={<InterviewProcessPage />} />
          <Route path="/analytics-interviews" element={<InterviewAnalyticsPage />} />
          <Route path="/offers" element={<OfferBoardPage />} />
          <Route path="/offers/compare" element={<OfferComparisonPage />} />
          <Route path="/offers/decision" element={<DecisionPage />} />
          <Route path="/offers/:offerId" element={<OfferDetailPage />} />
          <Route path="/analytics-offers" element={<OfferAnalyticsPage />} />
          <Route path="/recruiter" element={<RecruiterInboxPage />} />
          <Route
            path="/recruiter/:conversationId"
            element={<RecruiterConversationPage />}
          />
          <Route path="/quick-capture" element={<QuickCapturePage />} />
          <Route path="/capture" element={<BrowserCapturePage />} />
          <Route path="/resume" element={<ResumePage />} />
          <Route path="/resume-analytics" element={<ResumeAnalyticsPage />} />
          <Route path="/analytics" element={<CareerAnalyticsPage />} />
          <Route path="/strategy" element={<StrategyPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </div>
  )
}
