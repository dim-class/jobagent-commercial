/**
 * Reading the page the human already has open.
 *
 * Pure DOM functions: given a `Document` and its URL, say what kind of page it
 * is and pull out whatever job fields are *already visible*. Nothing here
 * scrolls, clicks, navigates, waits for lazy content, or issues a request.
 *
 * What it deliberately never touches: cookies, `localStorage`,
 * `sessionStorage`, form values, auth headers, or `document.body` as a whole.
 * Only the fields named in `selectors.ts` leave the page.
 *
 * Every extracted field records *which* selector matched it, so developer mode
 * can show why a field is missing instead of leaving you guessing.
 */

// eslint-disable-next-line no-var
var BossExtract = (function () {
  const MAX_DESCRIPTION_CHARS = 20000
  const MAX_CARDS = 60

  /**
   * M4c's stricter bar for a URL this extension is about to click into
   * (`activateNextPage`) - the exact scheme+host `extension/manifest.json`'s
   * `content_scripts.matches` declares and the backend's
   * `REQUIRED_TAB_ORIGIN` requires, never the broader `BossSelectors.HOSTS`
   * used elsewhere for merely *reading* whatever page a human already has
   * open (that list also accepts the bare `zhipin.com` host).
   */
  const REQUIRED_NAV_ORIGIN = 'https://www.zhipin.com'

  function isRequiredNavOrigin(url: string | null): boolean {
    if (!url) return false
    return url === REQUIRED_NAV_ORIGIN || url.indexOf(REQUIRED_NAV_ORIGIN + '/') === 0
  }

  /**
   * Bounds for the developer-mode structural diagnostic (see "dev-mode
   * diagnostic" below). Deliberately small: this is a look near a handful of
   * already-confirmed anchors, never a page-wide dump.
   */
  const MAX_DIAGNOSTIC_NODES = 80
  const MAX_DIAGNOSTIC_ANCESTORS = 5
  const MAX_DIAGNOSTIC_SIBLINGS = 6
  const MAX_DIAGNOSTIC_SUBTREE_DEPTH = 3
  const MAX_DIAGNOSTIC_SAMPLE_CHARS = 40
  /** A node's own subtree must be this small before it earns a text sample. */
  const MAX_DIAGNOSTIC_SAMPLE_SUBTREE = 12

  type PageType = 'search' | 'detail' | 'unsupported'

  interface FieldHit {
    value: string | null
    /** The selector that produced the value, for developer mode. */
    selector: string | null
  }

  interface JobCandidate {
    title: string | null
    company: string | null
    salary_text: string | null
    city: string | null
    experience_text: string | null
    education_text: string | null
    source_url: string | null
    external_id: string | null
    description: string | null
    /** field -> selector that matched. Developer mode only; no secrets. */
    matched_selectors: Record<string, string>
    missing_fields: string[]
    warnings: string[]
  }

  interface DetectionResult {
    page_type: PageType
    /** Query-stripped page URL. */
    url: string
    /** True when BOSS is showing a verification interstitial. */
    verification: boolean
    /** True only from the login URL or a visible, narrowly selected login affordance. */
    login_required: boolean
    candidates: JobCandidate[]
    warnings: string[]
    errors: string[]
  }

  /** One node's bounded, sanitized structure - never a full HTML dump. */
  interface DiagnosticNode {
    /** Where this node sits relative to the anchor, e.g. "parent(2)". */
    relation: string
    tag: string
    /** Class list only - never id, href, data-*, or any other attribute. */
    classes: string[]
    /** Short sanitized text, present only when the node's own subtree is small. */
    sample: string | null
  }

  interface AnchorDiagnostic {
    anchor: 'title' | 'salary' | 'company_root' | 'detail_root'
    /** The selector that found the anchor, or null if none matched. */
    anchor_selector: string | null
    found: boolean
    nodes: DiagnosticNode[]
  }

  interface DiagnosticResult {
    page_type: PageType
    url: string
    anchors: AnchorDiagnostic[]
    application_control: ApplicationControlDiagnostic
    /** True when the shared node budget ran out before every anchor finished. */
    truncated: boolean
    warnings: string[]
    errors: string[]
  }

  /** Bounded read-only evidence for M6. No href/data values or message body. */
  interface ApplicationControlDiagnostic {
    selector: string | null
    count: number
    /** Responsive markup may contain hidden copies; execution requires exactly one usable visible control. */
    visible_usable_count: number
    unique_visible_usable_control: boolean
    controls: {
      tag: string
      classes: string[]
      text: string | null
      disabled: boolean
      visible: boolean
      redirect_url_present: boolean
      data_url_present: boolean
      is_friend: boolean | null
    }[]
    confirmed_message_text: null
    blocker: string
  }

  interface ConfirmedApplicationIdentity {
    canonical_url: string
    external_id: string
    company: string
    title: string
  }

  type ApplicationPreflight =
    | { status: 'ok'; observed_url: string; observed_external_id: string }
    | { status: 'login_required' | 'verification' | 'wrong_page' | 'identity_mismatch'
      | 'control_missing' | 'control_ambiguous' | 'control_disabled' | 'control_wrong_state' }

  // ------------------------------------------------------------------ utils

  function text(node: Element | null): string {
    if (!node) return ''
    return (node.textContent || '').replace(/\s+/g, ' ').trim()
  }

  /** First selector that yields a non-empty string. Records which one won. */
  function pick(root: ParentNode, selectors: string[]): FieldHit {
    for (const selector of selectors) {
      let node: Element | null = null
      try {
        node = root.querySelector(selector)
      } catch {
        continue // a malformed selector must not break the whole extraction
      }
      const value = text(node)
      if (value) return { value, selector }
    }
    return { value: null, selector: null }
  }

  function salaryRendered(node: Element): boolean {
    const view = node.ownerDocument?.defaultView
    if (!view || typeof node.getBoundingClientRect !== 'function') return false
    const rect = node.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return false
    for (let el: Element | null = node; el; el = el.parentElement) {
      const css = view.getComputedStyle(el)
      if (css.display === 'none' || css.visibility !== 'visible' || Number(css.opacity) === 0) return false
    }
    return true
  }

  /** A broken first node must not mask a usable fallback in the SAME job. */
  function pickSalary(root: ParentNode | null, selectors: string[]): FieldHit {
    if (!root) return { value: null, selector: null }
    let rejected: FieldHit = { value: null, selector: null }
    let usable: FieldHit | null = null
    for (const selector of selectors) {
      for (const node of Array.from(root.querySelectorAll(selector))) {
        if (!salaryRendered(node)) continue
        const value = text(node)
        if (!isUsableSalary(value)) {
          if (!rejected.value && value) rejected = { value, selector }
          continue
        }
        // Conflicting valid values are not a reason to guess which is current.
        if (usable && usable.value !== value) return { value: null, selector: null }
        usable = usable || { value, selector }
      }
    }
    return usable || rejected
  }

  function salaryDetailRoot(doc: Document): Element | null {
    const title = pickNode(doc, BossSelectors.TITLE).node
    return title?.closest(BossSelectors.DETAIL_ROOT.join(',')) || null
  }

  /** Like `pick`, but hands back the matched element itself, not its text. */
  function pickNode(
    root: ParentNode,
    selectors: string[],
  ): { node: Element | null; selector: string | null } {
    for (const selector of selectors) {
      let node: Element | null = null
      try {
        node = root.querySelector(selector)
      } catch {
        continue
      }
      if (node) return { node, selector }
    }
    return { node: null, selector: null }
  }

  /** One match per known selector, deduped, for diagnostic coverage only. */
  function pickNodes(
    root: ParentNode,
    selectors: string[],
  ): { node: Element; selector: string }[] {
    const found: { node: Element; selector: string }[] = []
    const seen: Element[] = []
    for (const selector of selectors) {
      let node: Element | null = null
      try {
        node = root.querySelector(selector)
      } catch {
        continue
      }
      if (node && seen.indexOf(node) === -1) {
        seen.push(node)
        found.push({ node, selector })
      }
    }
    return found
  }

  /** Every match for the first selector that matches anything at all. */
  function pickAll(root: ParentNode, selectors: string[]): { nodes: Element[]; selector: string | null } {
    for (const selector of selectors) {
      let nodes: Element[] = []
      try {
        nodes = Array.prototype.slice.call(root.querySelectorAll(selector))
      } catch {
        continue
      }
      if (nodes.length) return { nodes, selector }
    }
    return { nodes: [], selector: null }
  }

  /**
   * Scheme + host + path. The entire query string is dropped, not just the
   * parameters in `TRACKING_PARAMS`: BOSS puts `lid` / `securityId` there, the
   * job id lives in the path, and a URL that cannot carry a session token is
   * the only kind worth storing. Matches `canonical_url()` on the backend.
   */
  function cleanUrl(raw: string | null | undefined): string | null {
    if (!raw) return null
    try {
      const parsed = new URL(raw, document.baseURI)
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
      return parsed.origin + parsed.pathname
    } catch {
      return null
    }
  }

  /** Which known tracking parameters were present, for the dev-mode report. */
  function strippedParams(raw: string | null | undefined): string[] {
    if (!raw) return []
    try {
      const parsed = new URL(raw, document.baseURI)
      return BossSelectors.TRACKING_PARAMS.filter((name) => parsed.searchParams.has(name))
    } catch {
      return []
    }
  }

  function externalIdOf(url: string | null): string | null {
    if (!url) return null
    for (const pattern of BossSelectors.JOB_URL_PATTERNS) {
      const match = pattern.exec(url)
      if (match) return match[1]
    }
    return null
  }

  /** Split a "北京 朝阳区 · 3-5年 · 本科" strip into its three parts. */
  function parseInfoTags(parts: string[]): {
    city: string | null
    experience: string | null
    education: string | null
  } {
    let city: string | null = null
    let experience: string | null = null
    let education: string | null = null

    const tokens: string[] = []
    for (const part of parts) {
      for (const piece of part.split(BossSelectors.TAG_SPLIT_RE)) {
        const cleaned = piece.replace(/\s+/g, ' ').trim()
        if (cleaned) tokens.push(cleaned)
      }
    }

    for (const token of tokens) {
      if (!education) {
        const level = BossSelectors.EDUCATION_TOKENS.filter((t) => token.indexOf(t) !== -1)[0]
        if (level) {
          education = level
          continue
        }
      }
      if (!experience) {
        const match = BossSelectors.EXPERIENCE_RE.exec(token)
        if (match) {
          experience = match[1].replace(/\s+/g, '')
          continue
        }
      }
      // Whatever is left over and looks like a place is the city. The first
      // such token wins; BOSS puts the city before the district.
      if (!city && token.length <= 12 && !/\d/.test(token)) city = token.split(' ')[0]
    }

    return { city, experience, education }
  }

  // -------------------------------------------------------------- detection

  function isSupportedHost(url: string): boolean {
    try {
      const host = new URL(url).hostname.toLowerCase()
      return BossSelectors.HOSTS.indexOf(host) !== -1
    } catch {
      return false
    }
  }

  function pathOf(url: string): string {
    try {
      return new URL(url).pathname
    } catch {
      return ''
    }
  }

  function looksLikeVerification(doc: Document, url: string): boolean {
    for (const node of Array.from(doc.querySelectorAll(BossSelectors.VERIFICATION_ROOT.join(',')))) {
      if (!salaryRendered(node)) continue // ignore hidden, stale challenge templates
      const notice = text(node).slice(0, 400).toLowerCase()
      if (BossSelectors.VERIFICATION_HINTS.some((hint) => notice.includes(hint.toLowerCase()))) return true
    }
    const haystack = (url + ' ' + text(doc.querySelector('body')).slice(0, 400)).toLowerCase()
    return BossSelectors.VERIFICATION_HINTS.some(
      (hint) => haystack.indexOf(hint.toLowerCase()) !== -1,
    )
  }

  function looksLikeLoginRequired(doc: Document, url: string): boolean {
    const path = pathOf(url)
    if (path === '/login' || path.indexOf('/login/') === 0) return true
    for (const node of Array.from(doc.querySelectorAll(BossSelectors.LOGIN_ROOT.join(',')))) {
      if (!salaryRendered(node)) continue
      const label = text(node).slice(0, 40)
      if (BossSelectors.LOGIN_HINTS.some((hint) => label.indexOf(hint) !== -1)) return true
    }
    return false
  }

  function detectPageType(doc: Document, url: string): PageType {
    if (!isSupportedHost(url)) return 'unsupported'

    const path = pathOf(url)
    if (BossSelectors.BLOCKED_PATH_HINTS.some((hint) => path.indexOf(hint) === 0)) {
      return 'unsupported'
    }

    // A listing path or several rendered cards always mean a search page,
    // even when BOSS also renders a selected-card detail pane beside the
    // list. Both checks come before any detail-pane markup: correlating a
    // pane back to one specific card is fragile (BOSS gives no explicit
    // link between the two), and live evidence on `/web/geek/jobs` showed it
    // failing outright, reporting "found 1 job" with salary, city,
    // experience, education and URL all missing. Reading each already-
    // rendered card directly, the same way a plain search-results page
    // already works, is the reliable path.
    const isSearchListingPath = BossSelectors.SEARCH_LISTING_PATH_HINTS.some(
      (hint) => path.indexOf(hint) === 0,
    )
    const cards = pickAll(doc, BossSelectors.CARD)
    if (isSearchListingPath || cards.nodes.length > 1) return 'search'

    const detail = pick(doc, BossSelectors.TITLE)
    if (detail.value) return 'detail'
    if (cards.nodes.length === 1) return 'search'
    return 'unsupported'
  }

  // ------------------------------------------------------------- extraction

  function emptyCandidate(): JobCandidate {
    return {
      title: null,
      company: null,
      salary_text: null,
      city: null,
      experience_text: null,
      education_text: null,
      source_url: null,
      external_id: null,
      description: null,
      matched_selectors: {},
      missing_fields: [],
      warnings: [],
    }
  }

  function record(candidate: JobCandidate, field: string, hit: FieldHit): string | null {
    if (hit.value && hit.selector) candidate.matched_selectors[field] = hit.selector
    else candidate.missing_fields.push(field)
    return hit.value
  }

  /**
   * The live `.boss-info-attr` text joins company and recruiter function,
   * e.g. "纳新电子 人力". Remove only a separated, terminal function token;
   * never trim an unseparated suffix that may be part of a company name.
   */
  function cleanDetailCompany(hit: FieldHit): FieldHit {
    if (!hit.value) return hit
    // Live detail pages may render "公司 · 招聘者职位" in the same attribute
    // node. The left side is the company; the right side is not.
    const companyPart = hit.value.split(/\s*[·•|｜]\s*/, 1)[0]
    const value = companyPart
      .replace(
        /(?:\s+|\s*[·•|｜]\s*)(?:人力(?:资源)?|人事|HR(?:BP)?|招聘(?:专员|经理)?)\s*$/i,
        '',
      )
      .trim()
    return { value: value || hit.value, selector: hit.selector }
  }

  /**
   * Live BOSS detail text can contain its anti-copy brand watermark inside a
   * word (for example `专BOSS直聘业`) plus a leading `直聘` fragment. Only
   * remove the observed leading fragment when the full watermark is present,
   * so ordinary prose that happens to start with `直聘` is left untouched.
   * The same protection also splices a literal "来自" into other words (see
   * `BossSelectors.DESCRIPTION_WATERMARK_INSERTIONS`); each pair there is an
   * exact, observed literal - never a general pattern - for the same reason.
   */
  function cleanDescriptionWatermark(value: string): string {
    let withoutObservedInsertion = value
    for (const [corrupted, clean] of BossSelectors.DESCRIPTION_WATERMARK_INSERTIONS) {
      withoutObservedInsertion = withoutObservedInsertion.split(corrupted).join(clean)
    }
    if (withoutObservedInsertion.indexOf('BOSS直聘') === -1) {
      return withoutObservedInsertion
    }
    return withoutObservedInsertion.replace(/BOSS直聘/g, '').replace(/^直聘(?=\S)/, '')
  }

  /**
   * BOSS renders some salaries through a private-use-area glyph font as an
   * anti-scraping measure; the underlying text is unusable garbage, not real
   * digits. Checked via character codes (0xE000-0xF8FF), not a `\u` regex
   * literal, so the range boundary is never mangled by escape processing.
   */
  function containsPuaGlyphs(value: string | null): boolean {
    if (!value) return false
    for (let i = 0; i < value.length; i++) {
      const code = value.charCodeAt(i)
      if (code >= 0xe000 && code <= 0xf8ff) return true
    }
    return false
  }

  /**
   * A salary string is trusted only if it plausibly carries a real figure: a
   * digit, or one of the known no-digit-but-real values ("面议"). Anything
   * else - an empty placeholder like "-K", or text corrupted by the PUA glyph
   * font - must not be imported as if it were a real salary.
   */
  function isUsableSalary(value: string | null): boolean {
    if (!value || containsPuaGlyphs(value)) return false
    if (/\d/.test(value)) return true
    return BossSelectors.NEGOTIABLE_SALARY_TOKENS.indexOf(value) !== -1
  }

  /** Remove only the exact, live-observed watermark tokens from a clone. */
  function removeDescriptionWatermarkNodes(clone: Element): void {
    const tokens = ['kanzhun', '直聘', 'boss']
    for (const span of Array.prototype.slice.call(clone.querySelectorAll('span')) as Element[]) {
      const value = text(span).toLowerCase()
      if (tokens.indexOf(value) !== -1) span.remove()
    }
  }

  /** The description body, with page furniture removed and length capped. */
  function extractDescription(doc: Document, candidate: JobCandidate): string | null {
    for (const selector of BossSelectors.DESCRIPTION) {
      let node: Element | null = null
      try {
        node = doc.querySelector(selector)
      } catch {
        continue
      }
      if (!node) continue

      // Work on a clone so the page the user is looking at is never modified.
      const clone = node.cloneNode(true) as Element
      removeDescriptionWatermarkNodes(clone)
      for (const noise of BossSelectors.NOISE_WITHIN_DESCRIPTION) {
        try {
          Array.prototype.forEach.call(clone.querySelectorAll(noise), (el: Element) =>
            el.remove(),
          )
        } catch {
          /* a bad noise selector must not lose us the description */
        }
      }

      const body = cleanDescriptionWatermark(clone.textContent || '')
        .replace(/\r\n/g, '\n')
        .replace(/[ \t ]+/g, ' ')
        .replace(/\n{3,}/g, '\n\n')
        .trim()

      if (body) {
        candidate.matched_selectors.description = selector
        if (body.length > MAX_DESCRIPTION_CHARS) {
          candidate.warnings.push('职位描述过长，已截断后再发送。')
          return body.slice(0, MAX_DESCRIPTION_CHARS)
        }
        return body
      }
    }
    candidate.missing_fields.push('description')
    return null
  }

  function extractDetail(doc: Document, url: string): JobCandidate {
    const candidate = emptyCandidate()

    candidate.title = record(candidate, 'title', pick(doc, BossSelectors.TITLE))
    candidate.company = record(
      candidate,
      'company',
      cleanDetailCompany(pick(doc, BossSelectors.COMPANY)),
    )
    const rawSalary = pickSalary(salaryDetailRoot(doc), BossSelectors.SALARY)
    const explicitSalaryUsable = isUsableSalary(rawSalary.value)
    const salaryUnusableSeen = !!rawSalary.value && !explicitSalaryUsable
    candidate.salary_text = record(
      candidate,
      'salary_text',
      explicitSalaryUsable ? rawSalary : { value: null, selector: null },
    )
    // Keep selector diagnostics truthful even when the matched node contains
    // only unusable private-font glyphs. The value remains null and missing.
    if (rawSalary.value && rawSalary.selector && !explicitSalaryUsable) {
      candidate.matched_selectors.salary_text = rawSalary.selector
    }

    const tags = pickAll(doc, BossSelectors.INFO_TAGS)
    const parsed = parseInfoTags(tags.nodes.map((node) => text(node)))
    if (tags.selector) candidate.matched_selectors.info_tags = tags.selector

    const explicitFields: [string, FieldHit, string | null][] = [
      ['city', pick(doc, BossSelectors.DETAIL_CITY), parsed.city],
      ['experience_text', pick(doc, BossSelectors.DETAIL_EXPERIENCE), parsed.experience],
      ['education_text', pick(doc, BossSelectors.DETAIL_EDUCATION), parsed.education],
    ]
    for (const [field, hit, fallback] of explicitFields) {
      const value = hit.value || fallback
      if (field === 'city') candidate.city = value
      else if (field === 'experience_text') candidate.experience_text = value
      else candidate.education_text = value

      if (value && hit.selector) candidate.matched_selectors[field] = hit.selector
      else if (value && tags.selector) candidate.matched_selectors[field] = tags.selector
      else candidate.missing_fields.push(field)
    }

    const pageUrl = cleanUrl(url)
    candidate.external_id = externalIdOf(pageUrl)
    candidate.source_url = candidate.external_id ? pageUrl : null
    if (!candidate.source_url) candidate.missing_fields.push('source_url')
    candidate.missing_fields = candidate.missing_fields.filter((field) => {
      if (field === 'salary_text') return !candidate.salary_text
      if (field === 'city') return !candidate.city
      if (field === 'experience_text') return !candidate.experience_text
      if (field === 'education_text') return !candidate.education_text
      return true
    })
    candidate.description = extractDescription(doc, candidate)

    if (!candidate.salary_text) {
      candidate.warnings.push(
        salaryUnusableSeen
          ? '页面上的薪资显示异常（可能被遮挡或使用了特殊字体），未能可靠读出数字，需要你手动核对并补充。'
          : '页面上没有可见的薪资，需要你手动补充。',
      )
    }
    if (!candidate.description) {
      candidate.warnings.push('没有找到职位描述容器，可能是页面还没加载完或改版了。')
    }
    return candidate
  }

  function extractCard(card: Element, index: number): JobCandidate {
    const candidate = emptyCandidate()

    candidate.title = record(candidate, 'title', pick(card, BossSelectors.CARD_TITLE))
    candidate.company = record(candidate, 'company', pick(card, BossSelectors.CARD_COMPANY))
    const rawCardSalary = pickSalary(card, BossSelectors.CARD_SALARY)
    const cardSalaryUsable = isUsableSalary(rawCardSalary.value)
    candidate.salary_text = record(
      candidate,
      'salary_text',
      cardSalaryUsable ? rawCardSalary : { value: null, selector: null },
    )
    // Keep selector diagnostics truthful even when the matched node contains
    // only unusable private-font glyphs (e.g. "-K") - see `isUsableSalary`.
    if (rawCardSalary.value && rawCardSalary.selector && !cardSalaryUsable) {
      candidate.matched_selectors.salary_text = rawCardSalary.selector
    }

    const area = pick(card, BossSelectors.CARD_AREA)
    if (area.value && area.selector) candidate.matched_selectors.city = area.selector
    // "北京·朝阳区·望京" - the city is the first segment.
    candidate.city = area.value ? area.value.split(BossSelectors.TAG_SPLIT_RE)[0].trim() : null
    if (!candidate.city) candidate.missing_fields.push('city')

    const tags = pickAll(card, BossSelectors.CARD_TAGS)
    if (tags.selector) candidate.matched_selectors.tags = tags.selector
    const parsed = parseInfoTags(tags.nodes.map((node) => text(node)))
    candidate.experience_text = parsed.experience
    candidate.education_text = parsed.education
    if (!parsed.experience) candidate.missing_fields.push('experience_text')
    if (!parsed.education) candidate.missing_fields.push('education_text')

    const link = pickAll(card, BossSelectors.CARD_LINK)
    const href = link.nodes.length ? link.nodes[0].getAttribute('href') : null
    candidate.source_url = cleanUrl(href)
    if (candidate.source_url && link.selector) candidate.matched_selectors.source_url = link.selector
    else if (!candidate.source_url) candidate.missing_fields.push('source_url')
    candidate.external_id = externalIdOf(candidate.source_url)

    // A card teaser is a one-line marketing blurb, never a job description.
    const teaser = pick(card, BossSelectors.CARD_TEASER)
    if (teaser.value) candidate.matched_selectors.teaser = teaser.selector as string
    candidate.description = null
    candidate.missing_fields.push('description')
    candidate.warnings.push(
      '搜索结果卡片没有职位描述。要导入这个岗位，请先打开它的详情页再检测。',
    )
    if (!candidate.salary_text) {
      candidate.warnings.push(
        rawCardSalary.value && !cardSalaryUsable
          ? '这张卡片上的薪资显示异常（可能使用了特殊字体），未能可靠读出数字，需要你手动核对并补充。'
          : '这张卡片上没有可见的薪资。',
      )
    }
    if (!candidate.title) candidate.warnings.push(`第 ${index + 1} 张卡片没能识别出职位名称。`)
    return candidate
  }

  const MAX_FALLBACK_CARD_ANCESTOR_LEVELS = 6

  /** Every `/job_detail/` anchor on the page, excluding the selected-pane's
   * own (unrelated) link - the pane is never a result card. */
  function jobDetailAnchors(root: ParentNode): HTMLAnchorElement[] {
    const anchors = Array.prototype.slice.call(
      root.querySelectorAll('a[href*="/job_detail/"]'),
    ) as HTMLAnchorElement[]
    return anchors.filter((a) => !a.closest('.job-detail-box'))
  }

  /**
   * Fallback card discovery for when `BossSelectors.CARD` matches nothing at
   * all - confirmed live failure: fixed card-root selectors return zero
   * matches on `/web/geek/jobs`, even though the page visibly renders
   * several cards. The one thing that stays true regardless of markup drift
   * is that every real card links to its own canonical `/job_detail/<id>.html`,
   * so cards are discovered from those anchors directly.
   *
   * Each anchor's card root is climbed only as far as it stays the *unique*
   * job-detail anchor in that subtree - the same "stop before a shared
   * ancestor" rule already used to keep the primary path from mixing two
   * cards' fields together. A second anchor pointing at an already-seen
   * canonical URL (e.g. a title link and a "view details" link on the same
   * card) is deduplicated, never turned into a second candidate.
   */
  function discoverCardsFromJobDetailAnchors(
    doc: Document,
  ): { root: Element; anchor: Element; canonicalUrl: string }[] {
    const results: { root: Element; anchor: Element; canonicalUrl: string }[] = []
    const seen = new Set<string>()

    for (const anchor of jobDetailAnchors(doc)) {
      const canonicalUrl = cleanUrl(anchor.getAttribute('href'))
      if (!canonicalUrl || !externalIdOf(canonicalUrl)) continue
      if (seen.has(canonicalUrl)) continue
      if (results.length >= MAX_CARDS) break

      let root: Element = anchor
      let current: Element = anchor
      for (let level = 0; level < MAX_FALLBACK_CARD_ANCESTOR_LEVELS; level++) {
        const parent: Element | null = current.parentElement
        if (!parent) break
        const distinctInParent = new Set(
          jobDetailAnchors(parent)
            .map((a) => cleanUrl(a.getAttribute('href')))
            .filter((u): u is string => !!u),
        )
        if (distinctInParent.size > 1) break // parent already spans another card
        root = parent
        current = parent
      }

      seen.add(canonicalUrl)
      results.push({ root, anchor, canonicalUrl })
    }

    return results
  }

  interface CardRoots {
    nodes: Element[]
    anchors: (Element | null)[]
    canonicalUrls: (string | null)[]
    usingFallback: boolean
  }

  /** The one place both `extractSearch` and `openCandidateLink` get their
   * card list from, so the index one reports is always the index the other
   * clicks - fallback or not. */
  function findCardRoots(doc: Document): CardRoots {
    const found = pickAll(doc, BossSelectors.CARD)
    if (found.nodes.length) {
      return {
        nodes: found.nodes,
        anchors: found.nodes.map(() => null),
        canonicalUrls: found.nodes.map(() => null),
        usingFallback: false,
      }
    }
    const fallback = discoverCardsFromJobDetailAnchors(doc)
    return {
      nodes: fallback.map((f) => f.root),
      anchors: fallback.map((f) => f.anchor),
      canonicalUrls: fallback.map((f) => f.canonicalUrl),
      usingFallback: true,
    }
  }

  function extractSearch(doc: Document): { candidates: JobCandidate[]; warnings: string[] } {
    const warnings: string[] = []
    const found = findCardRoots(doc)

    // Only what is already rendered. No scrolling, no pagination, no clicking.
    let nodes = found.nodes
    let canonicalUrls = found.canonicalUrls
    if (nodes.length > MAX_CARDS) {
      warnings.push(`当前页面渲染了 ${nodes.length} 张卡片，只返回前 ${MAX_CARDS} 张。`)
      nodes = nodes.slice(0, MAX_CARDS)
      canonicalUrls = canonicalUrls.slice(0, MAX_CARDS)
    }
    if (found.usingFallback && nodes.length) {
      warnings.push('固定卡片选择器未命中，已根据职位详情链接回退识别候选人卡片。')
    }

    const candidates = nodes.map((card, index) => {
      const candidate = extractCard(card, index)
      // The fallback's own canonical URL is authoritative - it is exactly
      // how this card root was found, so it always wins over whatever
      // `extractCard`'s own (unscoped, best-effort) link lookup guessed.
      const canonicalUrl = canonicalUrls[index]
      if (found.usingFallback && canonicalUrl) {
        candidate.source_url = canonicalUrl
        candidate.external_id = externalIdOf(canonicalUrl)
        candidate.matched_selectors.source_url = 'a[href*="/job_detail/"]'
        candidate.missing_fields = candidate.missing_fields.filter((f) => f !== 'source_url')
      }
      return candidate
    })
    const usable = candidates.filter((c) => c.title).length
    if (usable !== candidates.length) {
      warnings.push(`${candidates.length - usable} 张卡片缺少职位名称，可能是广告位或占位卡片。`)
    }
    return { candidates, warnings }
  }

  // --------------------------------------------------------- M4b navigation
  //
  // Explicitly authorized (CLAUDE.md "Chrome extension - M4 supervised
  // navigation policy", M4b). One function, one job: click the ALREADY-
  // RENDERED link of one search-result card, by the same index `extractSearch`
  // would report it at. It never scrolls to bring an off-screen card into
  // view, never constructs a URL and navigates to it, never retries with a
  // looser selector, and never guesses when more than one link resolves for
  // a card - it reports `selector_ambiguous` and does nothing. This is the
  // *only* navigation primitive in the whole extension; everything else
  // (which card, whether to proceed, when to stop) is decided by a human
  // action relayed through `overlay.ts` / `background.ts`, capped and
  // audited by the backend before this is ever called.

  interface OpenCandidateResult {
    ok: boolean
    error?: 'out_of_range' | 'no_link' | 'selector_ambiguous' | 'not_clickable'
  }

  /**
   * The one click primitive in the whole extension - every navigation path
   * (fixed-selector cards and the anchor-discovery fallback alike) ends
   * here, and only here, so there is exactly one call site that invokes
   * `click` to audit, not two that could quietly drift apart.
   */
  function clickAnchor(candidateAnchor: Element | null | undefined): OpenCandidateResult {
    const anchor = candidateAnchor as HTMLElement | null | undefined
    if (!anchor || typeof anchor.click !== 'function') {
      return { ok: false, error: 'not_clickable' }
    }
    anchor.click()
    return { ok: true }
  }

  function openCandidateLink(doc: Document, index: number): OpenCandidateResult {
    // Same discovery `extractSearch` used to report this index, fallback or
    // not - so a click always lands on the card the popup/overlay actually
    // showed at that position.
    const found = findCardRoots(doc)
    if (index < 0 || index >= found.nodes.length) {
      return { ok: false, error: 'out_of_range' }
    }

    if (found.usingFallback) {
      // The anchor discovery already found and verified this card's one
      // canonical link - no re-search, so there is nothing left to be
      // ambiguous about.
      return clickAnchor(found.anchors[index])
    }

    const link = pickAll(found.nodes[index], BossSelectors.CARD_LINK)
    if (link.nodes.length === 0) {
      return { ok: false, error: 'no_link' }
    }
    // Exactly one match, whichever selector produced it - anything else is
    // an ambiguous resolution and must not be guessed at.
    if (link.nodes.length !== 1) {
      return { ok: false, error: 'selector_ambiguous' }
    }
    return clickAnchor(link.nodes[0])
  }

  /**
   * M4c (CLAUDE.md "Chrome extension - M4 supervised navigation policy",
   * explicitly authorized). One bounded scroll step on the results list's
   * own scrollable container, falling back to the page/viewport itself when
   * no specific container resolves - never a loop, never waiting for new
   * content, never more than the one `scrollBy` call below. The human
   * decides whether and how many times to click again; this never chains,
   * retries, or polls for anything.
   */
  interface ScrollResult {
    ok: boolean
    error?: 'not_scrollable' | 'container_ambiguous'
  }

  /**
   * Resolves the results container the same way `activateNextPage` resolves
   * its control: the first `SCROLL_CONTAINER` selector that matches anything
   * must match exactly one element - review finding fixed here - never the
   * first of several silently picked via a bare `querySelector`. No match
   * at all is not ambiguity; it falls through to the page/viewport itself.
   */
  function scrollResultsContainer(doc: Document): ScrollResult {
    const found = pickAll(doc, BossSelectors.SCROLL_CONTAINER)
    if (found.nodes.length > 1) return { ok: false, error: 'container_ambiguous' }

    const container = found.nodes[0] || doc.scrollingElement || doc.documentElement
    const view = doc.defaultView
    const step = (container && container.clientHeight) || (view ? view.innerHeight : 0)
    if (!container || !step || typeof container.scrollBy !== 'function') {
      return { ok: false, error: 'not_scrollable' }
    }
    container.scrollBy({ top: step, left: 0 })
    return { ok: true }
  }

  /**
   * M4c's other half: activate exactly one same-origin "next page" control.
   * Same discipline as `openCandidateLink` - a missing or ambiguous match is
   * a hard stop, never a guess, and a control BOSS has marked as the last
   * page (a disabled class/attribute, per `NEXT_PAGE_DISABLED_CLASS_HINTS`)
   * is refused rather than clicked. Reuses `clickAnchor`, the one click
   * primitive in the whole extension - this never adds a second one.
   */
  interface PageResult {
    ok: boolean
    error?: 'no_control' | 'control_ambiguous' | 'disabled' | 'wrong_origin' | 'not_clickable'
  }

  function isNextPageDisabled(el: Element): boolean {
    const classes = (Array.prototype.slice.call(el.classList) as string[]).join(' ').toLowerCase()
    if (BossSelectors.NEXT_PAGE_DISABLED_CLASS_HINTS.some((hint) => classes.indexOf(hint) !== -1)) {
      return true
    }
    return el.getAttribute('aria-disabled') === 'true'
  }

  function activateNextPage(doc: Document): PageResult {
    const found = pickAll(doc, BossSelectors.NEXT_PAGE)
    if (found.nodes.length === 0) return { ok: false, error: 'no_control' }
    if (found.nodes.length !== 1) return { ok: false, error: 'control_ambiguous' }

    const control = found.nodes[0]
    if (isNextPageDisabled(control)) return { ok: false, error: 'disabled' }

    if (control.tagName === 'A') {
      const href = control.getAttribute('href')
      // A same-page (`href="#"`/empty/relative-to-here) control has nothing
      // to check; one that names a different page must resolve to *exactly*
      // `https://www.zhipin.com` - review finding fixed here - not merely
      // any host `isSupportedHost` would read a page from (that list also
      // accepts the bare `zhipin.com` host, which is fine for reading a
      // page a human already opened but too loose a bar for a URL this
      // extension itself is about to click into).
      if (href && href !== '#' && !isRequiredNavOrigin(cleanUrl(href))) {
        return { ok: false, error: 'wrong_origin' }
      }
    }

    const result = clickAnchor(control)
    return result.ok ? { ok: true } : { ok: false, error: 'not_clickable' }
  }

  /**
   * M4b phase two (CLAUDE.md "Chrome extension - M4 supervised navigation
   * policy", explicitly authorized). `openCandidateLink` (phase one) only
   * clicks an already-rendered card's own link - on `/web/geek/jobs` that
   * loads the selected-card detail pane beside the list, it does not
   * navigate away. This reads that pane once the human judges it has
   * loaded, verifies it is really the same job the human just opened (never
   * correlated/guessed - `cachedCard` is the exact card `openCandidateLink`
   * was told to click), and merges the two: the card's own structured
   * fields (already known good) fill in first, the pane supplies whatever
   * the card could not (description, above all - cards never carry one).
   * Fails closed - `not_loaded` is a soft, retry-able "not yet", but
   * `identity_mismatch` and `verification` are real problems the caller
   * must end the session over, never retry automatically.
   */
  interface CachedCardLike {
    title: string | null
    company: string | null
    salary_text: string | null
    city: string | null
    experience_text: string | null
    education_text: string | null
    source_url: string | null
    external_id: string | null
    matched_selectors?: Record<string, string>
  }

  interface CaptureOutcome {
    status: 'ok' | 'not_loaded' | 'identity_mismatch' | 'verification' | 'login_required'
    candidate: JobCandidate | null
  }

  /** The identity used to compare a card and a pane: cleaned the same way a
   * detail page's own company text is, so "纳新电子" and "纳新电子 人力" agree. */
  function companyIdentity(value: string | null): string | null {
    if (!value) return null
    return cleanDetailCompany({ value, selector: null }).value
  }

  function captureAndMerge(
    doc: Document,
    currentUrl: string,
    canonicalUrl: string,
    cachedCard: CachedCardLike,
  ): CaptureOutcome {
    if (looksLikeLoginRequired(doc, currentUrl)) {
      return { status: 'login_required', candidate: null }
    }
    if (looksLikeVerification(doc, currentUrl)) {
      return { status: 'verification', candidate: null }
    }

    // `canonicalUrl` is already known-correct (it is exactly the URL phase
    // one clicked), so it - not whatever the address bar currently shows -
    // is what resolves this candidate's own source_url/external_id.
    const pane = extractDetail(doc, canonicalUrl)
    if (!pane.title) {
      // Inconclusive, not a confirmed violation - the pane may still be
      // loading. The caller must let the human simply try again.
      return { status: 'not_loaded', candidate: null }
    }
    if (pane.title !== cachedCard.title) {
      return { status: 'identity_mismatch', candidate: null }
    }
    const cachedCompany = companyIdentity(cachedCard.company)
    if (cachedCompany && pane.company && companyIdentity(pane.company) !== cachedCompany) {
      return { status: 'identity_mismatch', candidate: null }
    }

    // Never trust a caller-supplied cached salary at face value - the same
    // `isUsableSalary` rule applies here too, so stale/malformed cached
    // state (e.g. a card captured before this fix, or any other source of
    // `CachedCardLike`) can never override a usable pane value or smuggle
    // PUA-glyph text into intake.
    const cachedSalaryUsable = isUsableSalary(cachedCard.salary_text)
    const usableCachedSalary = cachedSalaryUsable ? cachedCard.salary_text : null

    const merged = emptyCandidate()
    merged.title = cachedCard.title
    merged.company = cachedCard.company || pane.company
    merged.salary_text = usableCachedSalary || pane.salary_text
    merged.city = cachedCard.city || pane.city
    merged.experience_text = cachedCard.experience_text || pane.experience_text
    merged.education_text = cachedCard.education_text || pane.education_text
    merged.source_url = cachedCard.source_url || pane.source_url
    merged.external_id = cachedCard.external_id || pane.external_id
    // Only the pane ever carries a description - cards never do.
    merged.description = pane.description
    // Later keys win: a field the card itself supplied keeps the card's own
    // selector attribution; only a field the card lacked shows the pane's.
    // An unusable cached salary must not leave behind a selector pointing at
    // the rejected card value while the actual merged value came from the
    // pane (or from neither) - so its own diagnostic selector is dropped
    // here rather than allowed to override the pane's.
    const cachedSelectors = { ...(cachedCard.matched_selectors || {}) }
    if (!cachedSalaryUsable) delete cachedSelectors.salary_text
    merged.matched_selectors = { ...pane.matched_selectors, ...cachedSelectors }
    merged.missing_fields = (
      [
        'title',
        'company',
        'salary_text',
        'city',
        'experience_text',
        'education_text',
        'source_url',
        'description',
      ] as const
    ).filter((field) => !merged[field])
    merged.warnings = pane.warnings.slice()

    return { status: 'ok', candidate: merged }
  }

  // ------------------------------------------------------ dev-mode diagnostic

  const salaryNodeIds = new WeakMap<Element, number>()
  let salaryNodeSequence = 0

  /** Read-only screenshot proof: exact canonical card URL, or standalone detail URL.
   * A pane is eligible only when its own job link proves the same identity.
   * Offscreen, ambiguous, clipped or covered regions never earn a screenshot.
   */
  function salaryFrame(doc: Document, currentUrl: string, canonicalUrl: string, expectedTitle: string) {
    const fail = (status: string) => ({ status, frame: null })
    if (looksLikeLoginRequired(doc, currentUrl)) return fail('login_required')
    if (looksLikeVerification(doc, currentUrl)) return fail('verification')
    const clean = cleanUrl(canonicalUrl)
    if (!clean || clean !== canonicalUrl || !externalIdOf(clean) || !isRequiredNavOrigin(clean)) return fail('identity_mismatch')
    const view = doc.defaultView
    if (!view || doc.visibilityState !== 'visible' || (view.visualViewport && view.visualViewport.scale !== 1)) return fail('not_visible')
    const roots: Element[] = []
    const detailRoot = salaryDetailRoot(doc)
    if (externalIdOf(cleanUrl(currentUrl))) {
      if (cleanUrl(currentUrl) !== canonicalUrl || pick(doc, BossSelectors.TITLE).value !== expectedTitle) return fail('identity_mismatch')
      if (detailRoot) roots.push(detailRoot)
    } else {
      const found = findCardRoots(doc)
      for (let i = 0; i < found.nodes.length; i++) {
        const card = found.nodes[i]
        const urls = new Set(Array.from(card.querySelectorAll(BossSelectors.DETAIL_JOB_LINK.join(',')))
          .map((el) => cleanUrl(el.getAttribute('href'))).filter(Boolean))
        if (urls.size === 1 && urls.has(canonicalUrl) && pick(card, BossSelectors.CARD_TITLE).value === expectedTitle) roots.push(card)
      }
      if (roots.length > 1) return fail('ambiguous')
      if (detailRoot && pick(doc, BossSelectors.TITLE).value === expectedTitle) {
        const urls = new Set(Array.from(detailRoot.querySelectorAll(BossSelectors.DETAIL_JOB_LINK.join(',')))
          .map((el) => cleanUrl(el.getAttribute('href'))).filter(Boolean))
        if (urls.size === 1 && urls.has(canonicalUrl)) roots.push(detailRoot)
      }
    }
    for (const root of roots) {
      const nodes = Array.from(root.querySelectorAll(BossSelectors.SALARY_NODE.join(',')))
        .filter(salaryRendered)
      if (nodes.length !== 1) continue
      const node = nodes[0]
      const raw = text(node)
      // Text hint guards units/suffixes; no glyph-to-number mapping is attempted.
      if (!raw || raw.length > 40 || !/[Kk万]/.test(raw)) continue
      const r = node.getBoundingClientRect()
      if (r.width < 6 || r.height < 4 || r.width > 400 || r.height > 80 || r.left < 2 || r.top < 2 || r.right > view.innerWidth - 2 || r.bottom > view.innerHeight - 2) continue
      if (node.scrollWidth > node.clientWidth + 1 && node.clientWidth > 0) continue
      let covered = false
      for (const [x, y] of [[r.left + 1, r.top + 1], [r.right - 1, r.top + 1], [r.left + 1, r.bottom - 1], [r.right - 1, r.bottom - 1], [r.left + r.width / 2, r.top + r.height / 2]]) {
        const hit = doc.elementFromPoint(x, y)
        if (!hit || !(hit === node || node.contains(hit))) covered = true
      }
      // Reject overflow clipping even if the center happens to be visible.
      for (let parent = node.parentElement; parent; parent = parent.parentElement) {
        const css = view.getComputedStyle(parent)
        const pr = parent.getBoundingClientRect()
        if ((css.overflowX !== 'visible' && (r.left < pr.left || r.right > pr.right)) ||
            (css.overflowY !== 'visible' && (r.top < pr.top || r.bottom > pr.bottom))) covered = true
      }
      if (covered) continue
      if (!salaryNodeIds.has(node)) salaryNodeIds.set(node, ++salaryNodeSequence)
      return { status: 'ok', frame: {
        canonicalUrl, title: expectedTitle, raw, nodeId: salaryNodeIds.get(node),
        pageUrl: cleanUrl(currentUrl), x: r.left, y: r.top, width: r.width, height: r.height,
        viewportWidth: view.innerWidth, viewportHeight: view.innerHeight,
        scrollX: view.scrollX, scrollY: view.scrollY,
      } }
    }
    return fail('no_safe_region')
  }
  //
  // A developer-mode-only, explicit-click aid for tuning `selectors.ts` on a
  // *live* page. Company, city, experience, education and description are
  // only as good as the selectors that find them; when a real BOSS page has
  // drifted, this shows *why* without ever shipping the page itself anywhere.
  //
  // It starts only from anchors this extractor already trusts (title, salary,
  // the detail root) and looks a few DOM steps away from each - never the
  // whole document. Every node it reports carries only its tag name and class
  // list (no id, href, data-*, or any other attribute), and a text sample
  // exists only when that node's own subtree is small enough to be "near the
  // anchor" rather than a slice of the whole page. Samples are scrubbed of
  // anything that looks like an email, phone number, long digit run or URL.
  // Nothing here is sent anywhere; it only ever renders in the popup.

  function isDiagnosticExcluded(el: Element): boolean {
    const tag = el.tagName.toLowerCase()
    if (BossSelectors.DIAGNOSTIC_EXCLUDE_TAGS.indexOf(tag) !== -1) return true
    const classes = (Array.prototype.slice.call(el.classList) as string[]).join(' ').toLowerCase()
    return BossSelectors.DIAGNOSTIC_EXCLUDE_CLASS_HINTS.some((hint) => {
      // The split-pane title strip is trusted and anchored inside the selected
      // detail; do not confuse its semantic class with the global page header.
      if (hint === 'header' && classes.indexOf('job-detail') !== -1) return false
      return classes.indexOf(hint) !== -1
    })
  }

  function sanitizeClasses(el: Element): string[] {
    return (Array.prototype.slice.call(el.classList) as string[])
      .filter((cls) => cls.length > 0 && cls.length <= 40)
      .slice(0, 8)
  }

  /** Only this element's own text nodes; never inherit private descendant text. */
  function directText(el: Element): string {
    const parts: string[] = []
    for (const node of Array.prototype.slice.call(el.childNodes) as ChildNode[]) {
      if (node.nodeType === 3 && node.textContent) parts.push(node.textContent)
    }
    return parts.join(' ')
  }

  /** Scrub anything that looks like an identifier, not a job description word. */
  function sanitizeSample(raw: string): string | null {
    let value = raw
      .replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, '【邮箱】')
      .replace(/\b(?:https?:\/\/|www\.)\S+/gi, '【链接】')
      .replace(/\b(?:securityid|sessionid|token|auth)\s*[:=]\s*\S+/gi, '【令牌】')
      .replace(/\b(?=[A-Za-z0-9_-]{16,}\b)(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b/g, '【标识符】')
      .replace(/1[3-9]\d{9}/g, '【电话】')
      .replace(/\d{6,}/g, '【数字】')
      .replace(/\s+/g, ' ')
      .trim()
    if (!value) return null
    if (value.length > MAX_DIAGNOSTIC_SAMPLE_CHARS) {
      value = value.slice(0, MAX_DIAGNOSTIC_SAMPLE_CHARS) + '…'
    }
    return value
  }

  function describeDiagnosticNode(el: Element, relation: string): DiagnosticNode {
    let subtreeSize = Infinity
    try {
      subtreeSize = el.getElementsByTagName('*').length
    } catch {
      /* an unreadable subtree just gets no sample */
    }
    const small = subtreeSize <= MAX_DIAGNOSTIC_SAMPLE_SUBTREE
    return {
      relation,
      tag: el.tagName.toLowerCase(),
      classes: sanitizeClasses(el),
      sample: small ? sanitizeSample(directText(el)) : null,
    }
  }

  interface DiagnosticBudget {
    remaining: number
  }

  /**
   * Each anchor's own traversal never revisits a node (a DOM tree has one
   * parent per element, so climbing ancestors/siblings cannot cycle back on
   * itself) - but two different anchors legitimately can look at overlapping
   * nodes, e.g. when the detail root *is* an ancestor of the title. That
   * overlap is left alone rather than deduped away: each anchor's report
   * must stand on its own, never silently come back empty because another
   * anchor happened to see the same node first.
   */
  function collectAnchorDiagnostic(
    anchor: 'title' | 'salary' | 'company_root' | 'detail_root',
    selector: string | null,
    root: Element | null,
    budget: DiagnosticBudget,
    climbAncestors: boolean,
  ): AnchorDiagnostic {
    const result: AnchorDiagnostic = {
      anchor,
      anchor_selector: selector,
      found: !!root,
      nodes: [],
    }
    if (!root) return result

    function add(el: Element, relation: string): boolean {
      if (budget.remaining <= 0) return false
      if (isDiagnosticExcluded(el)) return true
      result.nodes.push(describeDiagnosticNode(el, relation))
      budget.remaining -= 1
      return true
    }

    if (!add(root, 'anchor')) return result

    function addSubtree(start: Element, relation: string, maxDepth: number) {
      const queue: { node: Element; depth: number }[] = []
      for (const child of Array.prototype.slice.call(start.children) as Element[]) {
        queue.push({ node: child, depth: 1 })
      }
      while (queue.length && budget.remaining > 0) {
        const current = queue.shift() as { node: Element; depth: number }
        if (isDiagnosticExcluded(current.node)) continue
        if (!add(current.node, `${relation}.child(${current.depth})`)) return
        if (current.depth < maxDepth) {
          for (const child of Array.prototype.slice.call(current.node.children) as Element[]) {
            queue.push({ node: child, depth: current.depth + 1 })
          }
        }
      }
    }

    if (!climbAncestors) {
      // Bounded breadth-first structure below each known detail root. Direct
      // text only means a safe parent can never inherit a private descendant.
      addSubtree(root, 'root', MAX_DIAGNOSTIC_SUBTREE_DEPTH)
      return result
    }

    let current: Element = root
    let level = 0
    while (current.parentElement && level < MAX_DIAGNOSTIC_ANCESTORS && budget.remaining > 0) {
      const parent: Element = current.parentElement
      if (isDiagnosticExcluded(parent)) break
      if (!add(parent, `parent(${level + 1})`)) break

      const siblings = Array.prototype.slice.call(parent.children) as Element[]
      let count = 0
      for (const sibling of siblings) {
        if (sibling === current) continue
        if (count >= MAX_DIAGNOSTIC_SIBLINGS) break
        if (!add(sibling, `sibling(${level + 1})`)) break
        // Text/tag strips commonly use p/ul/dl. Expand those shallowly, but
        // do not spend the shared budget walking arbitrary furniture divs.
        if (['p', 'ul', 'dl'].indexOf(sibling.tagName.toLowerCase()) !== -1) {
          addSubtree(sibling, `sibling(${level + 1})`, 2)
        }
        count++
      }

      current = parent
      level++
    }

    return result
  }

  /** Detail pages only: there is no "confirmed anchor" concept for a card. */
  function diagnoseDetail(doc: Document, url: string): DiagnosticResult {
    const result: DiagnosticResult = {
      page_type: 'unsupported',
      url: cleanUrl(url) || '',
      anchors: [],
      application_control: diagnoseApplicationControl(doc),
      truncated: false,
      warnings: [],
      errors: [],
    }

    if (!isSupportedHost(url)) {
      result.errors.push('当前页面不是 BOSS 直聘（www.zhipin.com），没有可诊断的内容。')
      return result
    }

    if (looksLikeVerification(doc, url)) {
      result.warnings.push('BOSS 正在显示安全验证页面，诊断结果可能不完整。')
    }

    result.page_type = detectPageType(doc, url)
    if (result.page_type !== 'detail') {
      result.errors.push('结构诊断仅支持职位详情页，请打开一个职位详情页再试。')
      return result
    }

    const budget: DiagnosticBudget = { remaining: MAX_DIAGNOSTIC_NODES }

    const title = pickNode(doc, BossSelectors.TITLE)
    result.anchors.push(
      collectAnchorDiagnostic('title', title.selector, title.node, budget, true),
    )

    const salary = pickNode(doc, BossSelectors.SALARY)
    result.anchors.push(
      collectAnchorDiagnostic('salary', salary.selector, salary.node, budget, true),
    )

    const companyRoot = pickNode(doc, BossSelectors.DIAGNOSTIC_COMPANY_ROOT)
    if (companyRoot.node) {
      result.anchors.push(
        collectAnchorDiagnostic(
          'company_root',
          companyRoot.selector,
          companyRoot.node,
          budget,
          false,
        ),
      )
    }

    const detailRoots = pickNodes(doc, BossSelectors.DETAIL_ROOT)
    if (detailRoots.length) {
      for (const detailRoot of detailRoots) {
        result.anchors.push(
          collectAnchorDiagnostic(
            'detail_root',
            detailRoot.selector,
            detailRoot.node,
            budget,
            false,
          ),
        )
        if (budget.remaining <= 0) break
      }
    } else {
      result.anchors.push(
        collectAnchorDiagnostic('detail_root', null, null, budget, false),
      )
    }

    if (budget.remaining <= 0) {
      result.truncated = true
      result.warnings.push('已达到诊断节点数量上限，结果已截断。')
    }
    if (!result.anchors.some((a) => a.found)) {
      result.warnings.push('没有找到任何可用的锚点（标题 / 薪资 / 详情容器均未命中）。')
    }

    return result
  }

  /**
   * Read only one narrowly named control selector.  Values that may carry a
   * session/security token (`href`, `redirect-url`, `data-url`) never leave
   * the page; only their presence is reported.  The BOSS account's configured
   * greeting is not assumed from a button click and stays explicitly unknown.
   */
  function diagnoseApplicationControl(doc: Document): ApplicationControlDiagnostic {
    for (const selector of BossSelectors.APPLICATION_CONTROL) {
      let nodes: Element[] = []
      try {
        nodes = Array.prototype.slice.call(doc.querySelectorAll(selector))
      } catch {
        continue
      }
      if (!nodes.length) continue
      const states = nodes.map((node) => {
        const view = doc.defaultView
        const rect = node.getBoundingClientRect()
        const style = view ? view.getComputedStyle(node) : null
        const disabled = (node as HTMLButtonElement).disabled === true
          || node.getAttribute('aria-disabled') === 'true'
        const visible = rect.width > 0 && rect.height > 0
          && style?.display !== 'none' && style?.visibility !== 'hidden'
        return { node, disabled, visible }
      })
      const visibleUsableCount = states.filter(({ disabled, visible }) => visible && !disabled).length
      return {
        selector,
        count: nodes.length,
        visible_usable_count: visibleUsableCount,
        unique_visible_usable_control: visibleUsableCount === 1,
        controls: states.slice(0, 3).map(({ node, disabled, visible }) => {
          const label = sanitizeSample(text(node).slice(0, 40))
          const dataset = (node as HTMLElement).dataset || {}
          return {
            tag: node.tagName.toLowerCase(),
            classes: sanitizeClasses(node),
            text: label,
            disabled,
            visible,
            redirect_url_present: node.hasAttribute('redirect-url'),
            data_url_present: node.hasAttribute('data-url'),
            is_friend: dataset.isfriend === 'true' ? true
              : dataset.isfriend === 'false' ? false : null,
          }
        }),
        confirmed_message_text: null,
        blocker: 'BOSS 首次招呼语正文未在该控件中得到可核实证据；禁止据此执行。',
      }
    }
    return {
      selector: null,
      count: 0,
      visible_usable_count: 0,
      unique_visible_usable_control: false,
      controls: [],
      confirmed_message_text: null,
      blocker: '未找到唯一、可核实的立即沟通控件；禁止执行。',
    }
  }

  /** Resolve the one initial-contact control without reading its URL-bearing attributes. */
  function applicationControl(doc: Document):
    | { ok: true; node: HTMLElement }
    | { ok: false; status: 'control_missing' | 'control_ambiguous' | 'control_disabled' | 'control_wrong_state' } {
    const all: Element[] = []
    for (const selector of BossSelectors.APPLICATION_CONTROL) {
      try { all.push(...Array.from(doc.querySelectorAll(selector))) } catch { /* fail below */ }
    }
    if (!all.length) return { ok: false, status: 'control_missing' }
    const visible = all.filter((node) => salaryRendered(node))
    if (visible.length !== 1) return { ok: false, status: 'control_ambiguous' }
    const node = visible[0] as HTMLElement
    if ((node as HTMLButtonElement).disabled === true || node.getAttribute('aria-disabled') === 'true') {
      return { ok: false, status: 'control_disabled' }
    }
    // M6 is the first application/greeting only. An existing-friend/ongoing
    // chat control is a different account action and must never be clicked.
    if (text(node) !== '立即沟通' || node.dataset.isfriend !== 'false') {
      return { ok: false, status: 'control_wrong_state' }
    }
    return { ok: true, node }
  }

  /** Pure-read, exact-identity M6 preflight. */
  function preflightConfirmedApplication(
    doc: Document,
    currentUrl: string,
    expected: ConfirmedApplicationIdentity,
  ): ApplicationPreflight {
    if (looksLikeLoginRequired(doc, currentUrl)) return { status: 'login_required' }
    if (looksLikeVerification(doc, currentUrl)) return { status: 'verification' }
    if (detectPageType(doc, currentUrl) !== 'detail') return { status: 'wrong_page' }
    const observedUrl = cleanUrl(currentUrl)
    const observedExternalId = externalIdOf(observedUrl)
    if (!observedUrl || observedUrl !== expected.canonical_url
      || observedExternalId !== expected.external_id) return { status: 'identity_mismatch' }
    const observedTitle = pick(doc, BossSelectors.TITLE).value
    const observedCompany = cleanDetailCompany(pick(doc, BossSelectors.COMPANY)).value
    if (observedTitle !== expected.title || observedCompany !== expected.company) {
      return { status: 'identity_mismatch' }
    }
    const control = applicationControl(doc)
    if (!control.ok) return { status: control.status }
    return { status: 'ok', observed_url: observedUrl, observed_external_id: observedExternalId }
  }

  /** The only M6 page mutation: repeat preflight and perform exactly one click. */
  function executeConfirmedApplication(
    doc: Document,
    currentUrl: string,
    expected: ConfirmedApplicationIdentity,
  ): ApplicationPreflight | { status: 'clicked'; observed_url: string; observed_external_id: string } {
    const preflight = preflightConfirmedApplication(doc, currentUrl, expected)
    if (preflight.status !== 'ok') return preflight
    const control = applicationControl(doc)
    if (!control.ok) return { status: control.status }
    control.node.click()
    return {
      status: 'clicked',
      observed_url: preflight.observed_url,
      observed_external_id: preflight.observed_external_id,
    }
  }

  // ------------------------------------------------------------------- api

  function detect(doc: Document, url: string): DetectionResult {
    const result: DetectionResult = {
      page_type: 'unsupported',
      url: cleanUrl(url) || '',
      verification: false,
      login_required: false,
      candidates: [],
      warnings: [],
      errors: [],
    }

    const removed = strippedParams(url)
    if (removed.length) {
      result.warnings.push(`已从 URL 中移除跟踪/安全参数：${removed.join('、')}。`)
    }

    if (!isSupportedHost(url)) {
      result.errors.push('当前页面不是 BOSS 直聘（www.zhipin.com），没有可检测的内容。')
      return result
    }

    if (looksLikeVerification(doc, url)) {
      result.verification = true
      result.warnings.push(
        'BOSS 正在显示安全验证页面。请你自己在浏览器里完成验证 —— 本扩展不会、也不应该替你处理验证。',
      )
    }


    if (looksLikeLoginRequired(doc, url)) {
      result.login_required = true
      result.warnings.push(
        'BOSS 登录状态已失效。请你自己在浏览器里完成登录，本扩展不会读取或填写账号、密码、短信码。',
      )
    }

    try {
      result.page_type = detectPageType(doc, url)
      if (result.page_type === 'detail') {
        result.candidates = [extractDetail(doc, url)]
      } else if (result.page_type === 'search') {
        const search = extractSearch(doc)
        result.candidates = search.candidates
        result.warnings = result.warnings.concat(search.warnings)
      } else {
        result.errors.push(
          '这是 BOSS 的页面，但看不出是搜索结果还是职位详情。请打开一个职位详情页或搜索结果页再试。',
        )
      }
    } catch (err) {
      result.errors.push('读取页面时出错：' + String((err as Error)?.message || err))
    }

    return result
  }

  return {
    detect,
    detectPageType,
    cleanUrl,
    strippedParams,
    externalIdOf,
    parseInfoTags,
    isSupportedHost,
    looksLikeVerification,
    diagnoseDetail,
    openCandidateLink,
    scrollResultsContainer,
    activateNextPage,
    captureAndMerge,
    preflightConfirmedApplication,
    executeConfirmedApplication,
    salaryFrame,
    MAX_DESCRIPTION_CHARS,
    MAX_CARDS,
    MAX_DIAGNOSTIC_NODES,
  }
})()
