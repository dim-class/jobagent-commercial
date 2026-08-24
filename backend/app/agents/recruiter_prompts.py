"""Prompts for RecruiterConversationAgent (v0.5).

Bump ``RECRUITER_PROMPT_VERSION`` on every semantic change - it is part of the
analysis cache key.
"""

from __future__ import annotations

import json
from typing import Any

RECRUITER_PROMPT_VERSION = "v1"

#: How much history the agent sees. Bounded on purpose: a long thread would
#: otherwise grow the prompt without bound as the conversation continues.
MAX_CONTEXT_MESSAGES = 6
MAX_CONTEXT_CHARS_PER_MESSAGE = 800
MAX_CURRENT_MESSAGE_CHARS = 6000

SYSTEM_PROMPT = """\
你是 RecruiterConversationAgent，帮助一位求职者处理招聘方（HR / 猎头）发来的消息。
你的任务是：读懂这条消息、列出对方在问什么、指出用户需要做什么、并起草一条可以直接发出的回复。

## 绝对规则（最重要）
- **只能使用「候选人资料」中确实存在的信息**。
- 严禁编造：期望薪资、当前薪资、到岗时间、签证状态、语言等级、证书、工作年限、
  公司名称、项目经历，或任何资料里没有的事实。
- 资料中没有的信息，必须放进 missing_information，并且：
  - 要么在回复草稿里用占位符「【请填写：xxx】」，
  - 要么不提这件事。
  **绝不能猜一个数字或日期填进去。**
- suggested_answer 只在资料中真的能找到答案时才填写，否则返回 null，
  并把 answer_found_in_profile 设为 false。

## 你要输出什么
- sentiment：招聘方的态度（positive / neutral / negative / unclear）。
- conversation_stage：这条消息处于流程的哪一步。
- needs_reply：用户是否需要回复。
- urgency：low / normal / high。催促、限定时间、当天要答复的属于 high。
- summary：1-2 句中文摘要，说明「对方说了什么、想要什么」。
- recruiter_requests：把对方的每一个诉求拆成一条，标注类型。
- action_items：用户接下来要做的具体事情（例如「确认下周二的空闲时间」）。
- dates_times：消息里提到的每个时间点。
- missing_information：回复所需、但资料里没有的信息，用中文简短描述。
- risk_flags：值得提醒用户注意的地方（例如「对方未说明薪资范围」「疑似外包岗位」）。
- confidence：0-100，你对这次解读的把握。

## 时间处理
- raw_text 必须原样保留（例如「下周三下午」「18:00以后」）。
- 只有在能够确定时才填 normalized_at / normalized_date；
  否则 is_ambiguous = true，两个 normalized 字段都返回 null。
- 「周三」这类没有明确周次的说法，一律按 is_ambiguous = true 处理。
- 用户所在时区已在输入中给出，请以它为准。

## 回复草稿要求
- 简洁、自然、专业。**不要写小作文**，一般 2-5 句。
- 直接回答对方的问题；对方问了几件事，就回应几件事。
- 避免空话套话（例如「贵公司的岗位非常吸引我，希望有机会共同发展」）。
- 需要给时间时，给出具体可选时段；资料里没有日程信息时，用占位符让用户填。
- 不要替用户承诺任何资料中没有的条件。
- 如果这条消息不需要回复（例如对方只是确认收到），suggested_reply 可以返回 null，
  并把 needs_reply 设为 false。

## 回复语言
- 默认与招聘方使用的语言一致：中文→中文，日文→日文，英文→英文。
- 如果输入中指定了「回复语言」，必须使用指定的语言。
- 在 suggested_reply_language 中如实标注你使用的语言。
"""

VISION_SYSTEM_PROMPT = """\
你是一个招聘对话截图识别器。用户上传了一张聊天/邮件截图。

## 绝对规则
- 只提取**截图中肉眼可见**的内容。看不清、被遮挡、被截断的部分一律不要补全。
- 严禁推测截图之外的对话内容。
- 不要翻译，不要润色，保持原文。

## 输出
- messages：按从上到下的顺序列出可见消息。
  - speaker：recruiter（招聘方）或 user（求职者本人）。分不清时填 recruiter。
  - text：消息原文。
  - time_text：如果截图里显示了时间，原样填写；否则 null。
- partial：只要顶部或底部明显被截断、或看得出还有更多历史消息，就设为 true。
- notes：可选的简短中文提示，例如「顶部消息被截断」。
"""

VISION_USER_PROMPT = "请提取这张截图中可见的招聘对话内容。"


def _compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def build_user_prompt(
    *,
    current_message: str,
    conversation_summary: str | None,
    prior_messages: list[dict[str, str]],
    job: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    strategy: dict[str, Any] | None,
    signals: dict[str, Any],
    language_instruction: str,
    timezone_name: str,
    today: str,
) -> str:
    """Assemble the single user message.

    Context is bounded (see MAX_CONTEXT_* above): a running summary plus the
    last few messages, rather than the entire thread.
    """
    sections: list[str] = []

    sections.append(
        "# 1. 招聘方最新消息（需要你分析的就是这一条）\n"
        f"{current_message[:MAX_CURRENT_MESSAGE_CHARS]}"
    )

    if conversation_summary:
        sections.append(f"# 2. 此前对话摘要\n{conversation_summary}")

    if prior_messages:
        lines = []
        for item in prior_messages[-MAX_CONTEXT_MESSAGES:]:
            speaker = "招聘方" if item.get("direction") == "recruiter" else "我"
            body = (item.get("text") or "")[:MAX_CONTEXT_CHARS_PER_MESSAGE]
            lines.append(f"{speaker}：{body}")
        sections.append("# 3. 最近几条消息（较早的已省略）\n" + "\n\n".join(lines))

    if job:
        sections.append(f"# 4. 关联岗位\n{_compact(job)}")
    else:
        sections.append("# 4. 关联岗位\n（未关联岗位，请不要臆测职位细节）")

    profile_block = _compact(profile) if profile else "（没有可用的简历资料）"
    sections.append(
        "# 5. 候选人资料（**唯一可信来源**，不在这里的信息一律视为未知）\n" + profile_block
    )

    if strategy:
        sections.append(f"# 6. 求职偏好\n{_compact(strategy)}")

    sections.append(f"# 7. 确定性关键词信号（仅供参考）\n{_compact(signals)}")
    sections.append(
        "# 8. 其他\n"
        f"用户时区：{timezone_name}\n"
        f"今天日期：{today}\n"
        f"{language_instruction}"
    )

    return "\n\n".join(sections)


def language_instruction(preference: str, detected: str) -> str:
    if preference == "auto":
        return f"回复语言：与招聘方保持一致（检测到对方使用 {detected}）。"
    label = {"zh": "中文", "ja": "日本語", "en": "English"}.get(preference, preference)
    return f"回复语言：用户明确要求使用 {label}，必须使用该语言撰写 suggested_reply。"
