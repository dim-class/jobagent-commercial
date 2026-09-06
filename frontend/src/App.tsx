import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'

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
import OnboardingPage from '@/pages/OnboardingPage'
import ResumeAnalyticsPage from '@/pages/ResumeAnalyticsPage'
import ResumePage from '@/pages/ResumePage'
import SettingsPage from '@/pages/SettingsPage'
import StrategyPage from '@/pages/StrategyPage'
import type { HealthResponse } from '@/types'

//: The four steps of an actual working day: search, decide, look things up,
//: keep the résumé current. Everything else is real and still reachable - it
//: just does not deserve a permanent slot while it has nothing in it. Nothing
//: was deleted; every route below still exists.
const PRIMARY_NAV = [
  { to: '/console', label: '搜索岗位', icon: '🔎' },
  { to: '/queue', label: '投递队列', icon: '🎯' },
  { to: '/jobs', label: '岗位库', icon: '💼' },
  { to: '/resume', label: '简历', icon: '📄' },
]

//: 面试 / Offer / HR沟通 are the back half of the funnel and are empty until
//: something comes back from a recruiter; the analytics pages need those same
//: outcomes before they can say anything. 快速采集 (/quick-capture) and
//: 浏览器采集 (/capture) are gone from the menu entirely - the extension
//: replaced both - but their routes still work if one is ever needed.
const SECONDARY_NAV = [
  { to: '/interviews', label: '面试', icon: '🗓️' },
  { to: '/offers', label: 'Offer', icon: '📨' },
  { to: '/recruiter', label: 'HR沟通记录', icon: '💬' },
  { to: '/setup', label: '个人设置', icon: '👤' },
  { to: '/strategy', label: '求职策略', icon: '🎯' },
  { to: '/dashboard', label: '数据概览', icon: '📊' },
  { to: '/analytics', label: '策略分析', icon: '📈' },
  { to: '/resume-analytics', label: '简历表现', icon: '🧪' },
  { to: '/settings', label: '设置', icon: '⚙️' },
]

export default function App() {
  const location = useLocation()
  const secondaryRouteActive = SECONDARY_NAV.some((item) => location.pathname.startsWith(item.to))
  const [showMoreNav, setShowMoreNav] = useState(secondaryRouteActive)
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

  useEffect(() => {
    if (secondaryRouteActive) setShowMoreNav(true)
  }, [secondaryRouteActive])

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-title">AI 求职助手</span>
          <span className="brand-sub">本地运行 · v{health?.version ?? '0.1.0'}</span>
        </div>

        <nav className="nav">
          {PRIMARY_NAV.map((item) => (
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
          <details
            className="nav-more"
            open={showMoreNav}
            onToggle={(event) => setShowMoreNav(event.currentTarget.open)}
          >
            <summary>更多工具</summary>
            <div className="nav-more-links">
              {SECONDARY_NAV.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
                >
                  <span className="nav-icon" aria-hidden>{item.icon}</span>
                  {item.label}
                </NavLink>
              ))}
            </div>
          </details>
        </nav>

        <div className="sidebar-footer">
          <div>{health?.openai_configured ? '🟢 OpenAI 已配置' : '🟡 未配置 OpenAI'}</div>
        </div>
      </aside>

      <main className="content">
        {offline ? (
          <Alert tone="error">
            无法连接后端服务。请双击项目根目录的 <code className="mono">Start-JobAgent.cmd</code>
            重新启动控制台；如仍失败，再查看 <code className="mono">.tmp\launcher</code> 中的日志。
          </Alert>
        ) : null}

        {health && !health.openai_configured ? (
          <Alert tone="warn">
            尚未配置 <code className="mono">OPENAI_API_KEY</code>，AI 匹配分析不可用。
            请在项目根目录的 <code className="mono">.env</code> 中填写后重启后端；其余功能可正常使用。
          </Alert>
        ) : null}

        <Routes>
          <Route path="/" element={<Navigate to="/console" replace />} />
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
          <Route path="/setup" element={<OnboardingPage />} />
          <Route path="/resume-analytics" element={<ResumeAnalyticsPage />} />
          <Route path="/analytics" element={<CareerAnalyticsPage />} />
          <Route path="/strategy" element={<StrategyPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/console" replace />} />
        </Routes>
      </main>
    </div>
  )
}
