/**
 * Every BOSS 直聘 selector lives here - and nowhere else.
 *
 * Mirrors the rule the Python collector already follows
 * (`backend/app/job_sources/boss/selectors.py`): when the site changes its
 * markup, this is the only file that should need editing.
 *
 * Each field is a *list of candidates tried in order*, so one markup tweak
 * degrades a single field instead of breaking the whole detection. Candidates
 * stay short and semantic - long generated paths like
 * `div > div:nth-child(3) > span` are deliberately avoided because they break
 * on any layout change, and they tell you nothing when they stop matching.
 *
 * Declared with `var` on purpose: content-script files share one global scope
 * in the extension's isolated world, and `var` makes a second injection
 * harmless instead of a "Identifier has already been declared" crash.
 */

// eslint-disable-next-line no-var
var BossSelectors = {
  /** The only host this extension looks at. */
  HOSTS: ['www.zhipin.com', 'zhipin.com'] as string[],

  /** Job-detail URL shapes. The id lives in the path, never in the query. */
  JOB_URL_PATTERNS: [
    /\/job_detail\/([A-Za-z0-9_~-]+)\.html/,
    /\/jobs\/detail\/([A-Za-z0-9_~-]+)/,
  ] as RegExp[],

  /** Paths that mean "a list of jobs", not one posting. */
  SEARCH_PATH_HINTS: [
    '/web/geek/job',
    '/web/geek/jobs',
    '/web/geek/recommend',
    '/job_detail/', // BOSS also renders a list beside a detail pane
    '/c', // company page: has a job list
  ] as string[],

  /**
   * Paths that are always a job LISTING, never one posting - even when BOSS
   * also renders a selected-card detail pane beside the list. Checked before
   * any detail-pane markup, so a listing page is never reported as "found 1
   * job": live evidence on `/web/geek/jobs` showed exactly that failure, with
   * salary/city/experience/education/URL missing because they depended on
   * correlating the pane back to one specific card. Deliberately narrower
   * than `SEARCH_PATH_HINTS` above - that list also carries `/job_detail/`
   * and `/c` for unrelated reasons, and including either here would
   * misclassify a genuine dedicated detail page as a listing.
   */
  SEARCH_LISTING_PATH_HINTS: ['/web/geek/job', '/web/geek/jobs', '/web/geek/recommend'] as string[],

  /** Pages this extension must never treat as a job source. */
  BLOCKED_PATH_HINTS: [
    '/web/geek/chat', // recruiter conversations - private, never read
    '/web/user',
    '/web/common/security-check',
    '/login',
    '/wapi/',
  ] as string[],

  /** Narrow, visible logged-out affordances. Read only their label text;
   * never inspect username/password fields or any stored session data. */
  LOGIN_ROOT: [
    '.nav-login',
    '.btn-login',
    '.login-register',
    'a[ka="header-login"]',
    'a[href^="/login"]',
  ] as string[],

  LOGIN_HINTS: ['登录', '登录/注册', '立即登录', '扫码登录'] as string[],

  // ---------------------------------------------------------------- detail

  DETAIL_ROOT: [
    '.job-detail-box',
    '.job-banner',
    '.job-primary',
    '.job-detail',
  ] as string[],

  TITLE: [
    '.job-banner .name h1',
    '.job-primary .name h1',
    '.job-detail-box .job-name',
    '.job-banner h1',
    'h1.name',
    '.job-title .name',
  ] as string[],

  SALARY: [
    '.job-banner .salary',
    '.job-primary .salary',
    '.job-detail-box .job-salary',
    '.salary-text',
    'span.salary',
  ] as string[],

  // Narrow same-job ROI only. Never screenshot a whole card/pane as a fallback.
  SALARY_NODE: ['.job-salary', '.salary', '.salary-text'] as string[],
  DETAIL_JOB_LINK: ['a[href*="/job_detail/"]'] as string[],

  /**
   * The M6 application control.
   *
   * **This selector IS clicked** - once, by `executeConfirmedApplication` in
   * `extract.ts`, and only after a claimed one-time approval. It is the single
   * most consequential selector in this repository.
   *
   * `.btn-startchat` was **verified read-only against the user's own logged-in
   * BOSS pages on 2026-08-31**, in both states:
   *
   *   not yet chatted:  <a class="btn btn-startchat" data-isfriend="false">立即沟通</a>
   *   already chatted:  <a class="btn btn-startchat" data-isfriend="true">继续沟通</a>
   *                     wrapped in <div class="btn btn-startchat-wrap">
   *
   * In both, the strict selector matches 2 nodes with exactly 1 visible (BOSS
   * renders a responsive hidden duplicate), which is why uniqueness is judged
   * on visible nodes only. `ka` / `data-url` / `redirect-url` carry ~380-400
   * character session tokens on the live page; only their *presence* is ever
   * read, never their values.
   *
   * Still not verified: that clicking actually submits. Nothing here has ever
   * been clicked on the live site. Every failure mode is fail-closed - a
   * missing, ambiguous, disabled or wrong-state control refuses rather than
   * guessing.
   */
  APPLICATION_CONTROL: ['.btn-startchat'] as string[],

  /**
   * The chat composer BOSS opens after 立即沟通 (authorized 2026-09-03).
   *
   * Structural and text-based on purpose. The panel's class names are not
   * documented and were not observed, so inventing one here would be a guess
   * that could type into the wrong box - and unlike a missed field, that is a
   * message sent to a real person. A candidate must be a *visible, empty*
   * textarea sharing a container with exactly one send control; anything
   * ambiguous fails closed and nothing is typed.
   *
   * `GREETING_SEND_TEXT` matches the control's own label rather than its
   * class, which survives a restyle and cannot silently point at a different
   * button after one.
   */
  /** BOSS's own words for a posting that is no longer open.
   *
   * A *positive* marker, never inferred from a missing 立即沟通 button: that
   * button is also absent when the page has not finished loading or the layout
   * moved, and acting on those would bury a live job. The text is matched
   * exactly, on a node of its own.
   */
  CLOSED_POSTING_TEXT: ['职位已关闭', '该职位已关闭', '职位已下线', '已下线'] as string[],

  GREETING_INPUT: ['textarea'] as string[],
  // A live run found the textarea and confirmed it empty, then failed with
  // `no_send_control`: BOSS's 发送 is not a <button>. Widened to the elements a
  // site actually uses for one. This stays safe because the match is still
  // "exactly one visible element whose own text is 发送" - more candidates can
  // only produce ambiguity, which refuses, never a wrong click.
  GREETING_SEND: ['button', '[role="button"]', 'a', 'div', 'span'] as string[],
  GREETING_SEND_TEXT: ['发送'] as string[],

  COMPANY: [
    '.job-boss-info .boss-info-attr',
    '.job-banner .company-info .name',
    '.sider-company .company-info .name',
    '.job-sider .company-info a.name',
    '.company-info .company-name',
    '.job-detail-company .company-name',
    'a.company-name',
  ] as string[],

  /** The strip holding city / experience / education: "北京 朝阳区 · 3-5年 · 本科". */
  INFO_TAGS: [
    '.job-banner .job-primary .text-desc',
    '.job-banner .info-primary p',
    '.job-detail-box .job-tags span',
    '.job-primary .info-primary p',
    '.job-banner p',
  ] as string[],

  /** Live standalone-detail fields inside `.job-banner .info-primary > p`. */
  DETAIL_CITY: ['.job-banner .info-primary .text-city'] as string[],
  /** BOSS currently spells this live class `experiece`; preserve that exact evidence. */
  DETAIL_EXPERIENCE: ['.job-banner .info-primary .text-experiece'] as string[],
  DETAIL_EDUCATION: ['.job-banner .info-primary .text-degree'] as string[],

  /** The description body. First match wins, so the tightest selector is first. */
  DESCRIPTION: [
    '.job-detail-box p.desc',
    '.job-detail-section .job-sec-text',
    '.job-sec .job-sec-text',
    '.job-sec-text',
    '.job-detail .text',
    '.detail-content .job-sec .text',
    '.job-detail-box .job-detail-desc',
  ] as string[],

  /** Removed from the description before it leaves the page. */
  NOISE_WITHIN_DESCRIPTION: [
    'script',
    'style',
    'noscript',
    '.job-similar',
    '.similar-job',
    '.recommend-job',
    '.job-recommend',
  ] as string[],

  // ---------------------------------------------------------------- search

  /** One rendered result card. Only what is already on screen is read. */
  CARD: [
    '.job-card-wrapper',
    '.job-list-box .job-card-box',
    'li.job-card-wrapper',
    '.job-list-wrapper li',
    'ul.job-list-box > li',
  ] as string[],

  CARD_TITLE: ['.job-name', '.job-title .job-name', 'span.job-name', '.name .job-name'] as string[],
  CARD_COMPANY: [
    '.company-name a',
    '.company-name',
    '.company-info .company-name',
    'h3.company-name',
    // Live-observed narrower card shape (real logged-in Chrome, 2026-08):
    // `<a class="boss-info"><span class="boss-name">纳新电子</span></a>` -
    // no `.company-name` anywhere on that card at all.
    'a.boss-info span.boss-name',
    '.boss-info .boss-name',
    'span.boss-name',
  ] as string[],
  CARD_SALARY: ['.salary', '.job-salary', '.job-info .salary', 'span.salary'] as string[],
  CARD_AREA: ['.job-area', '.job-info .job-area', '.company-location'] as string[],
  /** Experience and education arrive as sibling <li>s or a tag list. */
  CARD_TAGS: ['.job-info .tag-list li', '.tag-list li', '.job-info ul li'] as string[],
  CARD_LINK: ['a.job-card-left', 'a.job-name', 'a[href*="/job_detail/"]', 'a'] as string[],
  /** Cards sometimes carry a one-line teaser. It is not a job description. */
  CARD_TEASER: ['.job-card-footer .info-desc', '.info-desc'] as string[],

  // --------------------------------------------------------- M4c scroll/page
  //
  // Explicitly authorized (CLAUDE.md "Chrome extension - M4 supervised
  // navigation policy", M4c). Not yet live-verified against a real
  // `/web/geek/jobs` page - conventional/documented BOSS markup, the same
  // provisional status every other not-yet-confirmed selector list here
  // carries until a human checks it in their own logged-in Chrome (see
  // `extension/README.md`). A short, ordered fallback list, same discipline
  // as every other selector group: never a single brittle guess.

  /** The results list's own scrollable container, tried before falling back
   * to the page/viewport itself (`scrollResultsContainer` in extract.ts). */
  SCROLL_CONTAINER: ['.job-list-box', '.search-job-result', '.job-list-wrapper'] as string[],

  /** The "next page" control on a BOSS results page. Must resolve to exactly
   * one element - an ambiguous or missing match is a hard stop, never a
   * guess (`activateNextPage` in extract.ts). */
  NEXT_PAGE: [
    '.options-pages a.ui-icon-arrow-right:not(.disabled)',
    '.options-pages a.next:not(.disabled)',
    'a.next-page:not(.disabled)',
    '.pagination-next:not(.disabled)',
  ] as string[],

  /** Class-name fragments that mark a next-page control as unusable even if
   * a selector above matched it (BOSS marks the last page this way instead
   * of removing the control) - checked in addition to `:not(.disabled)`,
   * which only guards the exact literal class `disabled`. */
  NEXT_PAGE_DISABLED_CLASS_HINTS: ['disabled', 'dis-next', 'is-disabled'] as string[],

  // ------------------------------------------------------------ text parse

  /** "3-5年" / "1年以内" / "经验不限" / "应届生" */
  EXPERIENCE_RE:
    /(经验不限|应届生?|在校[生\/]?|\d{1,2}\s*-\s*\d{1,2}\s*年|\d{1,2}\s*年以[上内]|\d{1,2}\s*年)/,

  /** Longest first, so "大专" wins over a bare "专". */
  EDUCATION_TOKENS: [
    '学历不限',
    '初中及以下',
    '中专/中技',
    '高中',
    '大专',
    '本科',
    '硕士',
    '博士',
  ] as string[],

  /** Separators used inside a tag strip. */
  TAG_SPLIT_RE: /[·|｜]|\s{2,}/,

  /**
   * Salary text with no digits that is still a real, human-meaningful value -
   * never a loading placeholder or a font-obfuscation artefact. Anything else
   * without a digit (e.g. a bare unit like "-K", or a run of PUA glyphs) is
   * treated as unusable rather than imported as-is.
   */
  NEGOTIABLE_SALARY_TOKENS: ['面议', '薪资面议'] as string[],

  /**
   * Exact, live-observed watermark insertions: BOSS's anti-copy protection
   * splices a stray "来自" into the middle of a word in the description
   * (e.g. "经验" -> "经来自验"). The word it targets and the position move
   * over time, so only literal, evidence-backed pairs are listed here - a
   * generic "strip 来自 between two Han characters" regex would also corrupt
   * legitimate prose that uses the word (e.g. "候选人来自全国各地").
   */
  DESCRIPTION_WATERMARK_INSERTIONS: [
    ['经来自验', '经验'],
    ['熟来自悉', '熟悉'],
    ['熟悉来自云计算', '熟悉云计算'],
  ] as [string, string][],

  /**
   * Query parameters BOSS uses to carry per-visit session and security state.
   *
   * Named so the reason is legible in developer mode. The cleaner drops the
   * whole query string regardless - the job id lives in the path, so nothing
   * of value is lost and no session-scoped token can leak into the backend.
   */
  TRACKING_PARAMS: [
    'lid',
    'securityId',
    'sessionId',
    'ka',
    'bg',
    '_c',
    'traceId',
    'expectId',
    'searchId',
  ] as string[],

  /**
   * Anti-bot interstitials. Detected only so the human can be told to deal
   * with it themselves - this extension never attempts to solve one.
   */
  // A challenge can appear after the JD/list, outside the bounded page-text prefix.
  // Read only these small verification containers; never authentication form values.
  VERIFICATION_ROOT: ['.verify-wrapper', '.verify-wrap'] as string[],

  VERIFICATION_HINTS: [
    'security-check',
    '请完成安全验证',
    '安全验证',
    '验证码',
    '滑块验证',
    '点击验证',
    '访问过于频繁',
  ] as string[],

  // ------------------------------------------------------- dev-mode diagnostic

  /**
   * Tags a structural diagnostic must never step into, however short the
   * bounded text sample would be. Not job content by definition.
   */
  DIAGNOSTIC_EXCLUDE_TAGS: [
    'script',
    'style',
    'noscript',
    'iframe',
    'svg',
    'input',
    'form',
    'button',
  ] as string[],

  /**
   * Class-name keywords that mark a subtree as off-limits for the structural
   * diagnostic - nav, chat, account and auth areas, even one level away from
   * a confirmed anchor, are never dumped to developer mode.
   */
  DIAGNOSTIC_EXCLUDE_CLASS_HINTS: [
    'nav',
    'header',
    'footer',
    'chat',
    'message',
    'account',
    'login',
    'sign-in',
    'signin',
    'auth',
    'user-menu',
    'im-',
  ] as string[],

  /**
   * Live detail evidence puts the recruiter/company identity block here.
   * `.job-detail-company` is only the business-info/address section on the
   * current standalone page, so keep it solely as an older-layout fallback.
   */
  DIAGNOSTIC_COMPANY_ROOT: ['.job-boss-info', '.job-detail-company'] as string[],

}
