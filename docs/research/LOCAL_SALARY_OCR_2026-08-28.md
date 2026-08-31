# Local salary OCR — implementation and acceptance

## Outcome

DOM-first fallback and Windows-local OCR are code-accepted. **Live extension screenshot/OCR
verification is pending.** No Claude, paid AI, browser driver or real-job import ran this turn.

## Mechanism and boundaries

1. Try rendered usable salary alternatives within the same card/detail root. Broken first
   matches no longer mask fallbacks; hidden or conflicting values are not trusted.
2. An approved runner candidate or explicit standalone detail detection can request an OCR
   region. Prove identity through the card's unique canonical URL or actual standalone URL.
   A pane is eligible only if its own link proves the same URL; same title alone is insufficient.
3. Check foreground tab/window, viewport, visibility, clipping and occlusion sample points.
   Chrome captures the visible viewport; the full image stays transiently in worker memory.
   Crop there, then send only the salary PNG to localhost /api/extension/salary-ocr.
4. Endpoint rejects non-loopback peers, page origins, unexpected fields, oversized requests
   and invalid/large PNG dimensions. One subprocess at a time, maximum 20 seconds.
   Windows PowerShell 5.1 invokes the installed Windows OCR engine entirely in memory.
   A process-scoped execution-policy override was approved for this repository script;
   no machine/user execution policy was changed. No new dependency/model installed.
5. OCR reads at two scales using installed English recognition. Only agreeing strict
   range/unit/month-suffix parses are accepted, with DOM unit/suffix hints checked again.
   **Agreement is not a confidence score or guarantee.** No O-to-0 or glyph mapping guesses.
6. Recheck identity, node/region, viewport, foreground state and cancellation before transmitting
   the crop and before using OCR. Tab activation/navigation/focus events invalidate work,
   including switching away and back. No screenshot is persisted or logged.
7. Fill missing salary only; canonical intake remains the sole persistence/dedup path.
   Its existing creation note marks OCR provenance for human review. No old jobs overwritten.

## Limits

- One foreground request; <=800x160 pixels / 256 KiB PNG; no capture retry loop.
- No extra scroll to reveal salary. Offscreen, hidden, ambiguous and unsupported regions stay missing.
- Search-card popup previews do not bulk-screenshot. Only already-reserved runner candidates or
  one standalone detail detection are eligible. Existing 3-default/20-candidate/5-scroll caps remain.
- Strict parser supports numeric K/万 ranges and optional readable 薪 suffix. Recognition may
  fail on Chinese suffixes or other formats; daily/yearly/unknown units are not guessed.
- Windows English OCR must be installed. This machine has en-US, Japanese and Simplified Chinese.
  Unsupported environments return unknown. No API fees or external image upload.
- No font decoding, private BOSS API, credentials, stealth, verification bypass or controller plugin.
- Existing JD noise/experience problems are outside this salary-only change.

## Verification

- Build PASS; 196 extension tests PASS (20 new screenshot/identity/cancellation tests).
- 46 new backend/DOM tests PASS; orange, grey and black local 8-13K screenshots recognized by
  the real Windows engine. Fixture HTTP routes fulfill all page content locally, never live BOSS.
- Full backend: 1575 collected, 1574 PASS, one prior unrelated dated analytics failure:
  test_analytics_api.py::test_window_and_filters_are_query_parameters.
- Existing secret scan and extension safety contracts included. Initial fixture failures fixed:
  explicit UTF-8 response, short test IDs and adequate JD length; no safety test weakened.
- User's historical salary crop -> local OCR -> 8-13K, and after backend restart the same crop
  -> localhost OCR endpoint -> 8-13K. These prove local processing, not live Chrome capture.
- Backend restart gated on no running/paused task or active extension session; health=ok.
  Existing dirty work preserved; no commit/reset, new DB schema or second intake.

## Minimal live check

Reload JobAgent in chrome://extensions, refresh logged-in BOSS, start one task at cap=3,
and keep Chrome foreground without manual scrolling/clicking. Alternatively Detect a single
standalone job detail. Compare a formerly missing salary to the visible page and canonical
job identity; inspect the OCR creation note. Uncertain values must remain missing, and
verification/cancellation must stop actions without exceeding approved budgets.

## Primary references

## Live follow-up: popup focus handoff

Tasks #39 and #18 stopped with `salary_not_foreground`, each with 15 observed/new and zero
imports. User confirmed starting in the extension and keeping Chrome foreground. Inspection
found the runner popup remained open after successful start/resume. Chromium's window controller
source explicitly notes that an extension popup can leave the browser window not focused;
this explains a plausible conflict, not proof of the precise OS focus history in these runs.

Fix: close only our popup after a successful start/resume acknowledgement, before any status
refresh; keep failures and pause/cancel controls visible. Do not focus any window programmatically,
change OCR guards, or add retries. Seven regression cases added (four assertions failed against
the previous bundle); build and 203/203 extension tests pass. Backend unchanged. Actual Chrome
handoff and live OCR remain pending. Reopening the popup during capture may still safely stop it.

- [Chromium WindowControllerList focus handling](https://chromium.googlesource.com/experimental/chromium/src/+/refs/heads/main/chrome/browser/extensions/window_controller_list.cc)

## OCR API references

- [Chrome captureVisibleTab](https://developer.chrome.com/docs/extensions/reference/api/tabs#method-captureVisibleTab)
- [Microsoft Windows OcrEngine](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine)
