'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const page = fs.readFileSync(path.join(__dirname, '../src/pages/ApplicationQueuePage.tsx'), 'utf8')
const client = fs.readFileSync(path.join(__dirname, '../src/api/client.ts'), 'utf8')

test('M6 accepts an unknown dynamic greeting per job and never asks for or prefills text', () => {
  assert.doesNotMatch(page, /m6Message|setM6Message|id="m6-message"|预计首次招呼语（必须由你手工填写）/)
  assert.match(page, /未知（由 BOSS 动态生成，JobAgent 无法预览或控制）/)
  assert.match(page, /我接受 BOSS 为这个岗位动态生成未知的首次招呼语/)
  assert.match(page, /AI 也不能代替你勾选确认/)
  assert.match(page, /生成最终确认（不执行）/)
  assert.match(client, /answers_source:\s*'boss_dynamic_unverified'/)
  assert.doesNotMatch(client, /answers_text:/)
})

test('the final user click sends only one approval id to the extension', () => {
  assert.match(page, /consoleExtension\(\s*'execute-application'[\s\S]{0,180}m6Approval\.id/)
  assert.doesNotMatch(page, /consoleExtension\(\s*'execute-application'[\s\S]{0,220}(?:selected|jobIds|greeting_message)/)
  assert.match(page, /勿直接重试/)
  assert.match(page, /setM6Attempted\(true\)/)
  assert.match(page, /m6Attempted \? '本确认已发出，不可重试'/)
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
