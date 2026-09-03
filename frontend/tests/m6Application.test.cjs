'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const page = fs.readFileSync(path.join(__dirname, '../src/pages/ApplicationQueuePage.tsx'), 'utf8')
const client = fs.readFileSync(path.join(__dirname, '../src/api/client.ts'), 'utf8')

test('M6 accepts an unknown dynamic greeting per job and never asks for or prefills text', () => {
  // The forbidden shape is unchanged: nothing here asks the human to *predict*
  // what BOSS will send. That mode was disproved live, and its field is gone.
  assert.doesNotMatch(page, /m6Message|setM6Message|id="m6-message"|预计首次招呼语（必须由你手工填写）/)
  // Unknown-greeting mode still exists and still says exactly what it cannot do.
  assert.match(page, /由 BOSS 决定是否发送以及发送什么/)
  assert.match(page, /我接受 BOSS 为这个岗位动态生成未知的首次招呼语/)
  assert.match(page, /AI 也不能代替你勾选确认/)
  // Typed-greeting mode (authorized 2026-09-03): the text JobAgent will type is
  // shown, editable, and refuses to send into a box that is not empty.
  assert.match(page, /id="m6-greeting"/)
  assert.match(page, /onChange=\{\(e\) => setM6Greeting\(e\.target\.value\)\}/)
  assert.match(page, /这就是会被逐字打出去的内容/)
  assert.match(page, /仅在聊天框为空时/)
  // And it cannot be confirmed with nothing to send.
  assert.match(page, /m6SendGreeting && !m6Greeting\.trim\(\)/)
  // One confirmation now both binds and executes (CLAUDE.md M6,
  // single-confirmation amendment). What it must still do is show the exact
  // job, its URL and the chosen resume before the human accepts.
  assert.match(page, /确认并执行一次投递/)
  assert.doesNotMatch(page, /生成最终确认/)
  assert.match(page, /岗位链接/)
  assert.match(page, /本次简历/)
  // The confirmation stays impossible without an explicit resume AND the
  // acceptance checkbox - merging the screens must not merge away either.
  // Now also refuses when the typed mode has nothing to send. Matched across
  // the line break the extra clause introduced.
  assert.match(page, /disabled=\{m6Busy \|\| !m6ResumeId \|\| !m6DynamicAccepted \|\| m6Attempted[\s\S]{0,80}\}/)
  assert.match(client, /greeting \? 'boss_typed_greeting' : 'boss_dynamic_unverified'/)
  assert.match(client, /answers_text: greeting/)
})

test('the final user click sends only one approval id to the extension', () => {
  assert.match(page, /consoleExtension\(\s*'execute-application'[\s\S]{0,180}approval\.id/)
  assert.doesNotMatch(page, /consoleExtension\(\s*'execute-application'[\s\S]{0,220}(?:selected|jobIds|greeting_message)/)
  assert.match(page, /勿直接重试/)
  assert.match(page, /setM6Attempted\(true\)/)
  // Tolerates the formatter breaking the ternary across lines - what is
  // asserted is that a consumed confirmation says so and cannot be reused.
  assert.match(page, /m6Attempted[\s\S]{0,60}'本确认已发出，不可重试'/)
  // Binding and executing share one click, so a failure to bind - which
  // dispatched nothing - must release the one-attempt latch rather than
  // leaving the job permanently unconfirmable.
  assert.match(page, /setM6Attempted\(false\)[\s\S]{0,120}Nothing was dispatched/)
})

test('an unknown M6 click result immediately offers the existing human mark-applied path', () => {
  assert.match(page, /setConfirmApplyFromM6\(true\)/)
  assert.match(page, /setConfirmApply\(attemptedJob\)/)
  assert.match(page, /api\.markApplied\(proposal\.job_id, applyNote, appliedResume\)/)
  assert.match(page, /复用现有 mark_applied 唯一路径进入已投递列表/)
  assert.match(page, /取消会保留“结果待确认”的审计记录/)
  assert.doesNotMatch(page, /reply\.ok[\s\S]{0,500}api\.markApplied/)
})

test('a lost worker result has an explicit human-only unknown closeout, never a retry', () => {
  assert.match(client, /applicationApproval:/)
  assert.match(client, /abandonApplicationAttempt:/)
  assert.match(client, /confirmed:\s*true/)
  assert.match(page, /我已人工核对，结束为结果未知/)
  assert.match(page, /只把本地记录[\s\S]{0,80}不会点击网页或重试投递/)
  assert.match(page, /刷新尝试状态/)
  assert.match(page, /为避免重复发送[\s\S]{0,100}不会允许再次执行或关闭/)
})
