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
  ] as string[],
  CARD_SALARY: ['.salary', '.job-salary', '.job-info .salary', 'span.salary'] as string[],
  CARD_AREA: ['.job-area', '.job-info .job-area', '.company-location'] as string[],
  /** Experience and education arrive as sibling <li>s or a tag list. */
  CARD_TAGS: ['.job-info .tag-list li', '.tag-list li', '.job-info ul li'] as string[],
  CARD_LINK: ['a.job-card-left', 'a.job-name', 'a[href*="/job_detail/"]', 'a'] as string[],
  /** Cards sometimes carry a one-line teaser. It is not a job description. */
  CARD_TEASER: ['.job-card-footer .info-desc', '.info-desc'] as string[],

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
