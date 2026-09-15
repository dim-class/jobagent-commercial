//: The sidebar status and the page banner read the AI status once, on load. A
//: save made anywhere - the banner or 设置 - announces itself so both refresh
//: without a reload.
export const AI_SETTINGS_SAVED = 'jobagent:ai-settings-saved'

export function announceAiSettingsSaved(): void {
  window.dispatchEvent(new Event(AI_SETTINGS_SAVED))
}
