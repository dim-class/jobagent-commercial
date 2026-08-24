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
    /** True when the shared node budget ran out before every anchor finished. */
    truncated: boolean
    warnings: string[]
    errors: string[]
  }

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

  /** All nodes matching any known selector, deduped by DOM identity. */
  function pickAllKnown(root: ParentNode, selectors: string[]): Element[] {
    const nodes: Element[] = []
    for (const selector of selectors) {
      let matches: Element[] = []
      try {
        matches = Array.prototype.slice.call(root.querySelectorAll(selector))
      } catch {
        continue
      }
      for (const node of matches) {
        if (nodes.indexOf(node) === -1) nodes.push(node)
      }
    }
    return nodes
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
    const haystack = (url + ' ' + text(doc.querySelector('body')).slice(0, 400)).toLowerCase()
    return BossSelectors.VERIFICATION_HINTS.some(
      (hint) => haystack.indexOf(hint.toLowerCase()) !== -1,
    )
  }

  function detectPageType(doc: Document, url: string): PageType {
    if (!isSupportedHost(url)) return 'unsupported'

    const path = pathOf(url)
    if (BossSelectors.BLOCKED_PATH_HINTS.some((hint) => path.indexOf(hint) === 0)) {
      return 'unsupported'
    }

    // A split-pane search page is a detail page for this POC: the human chose
    // one card and the extension reads only the detail already rendered on
    // the right. It never clicks or iterates through the list itself.
    const selectedDetail = pickNode(doc, BossSelectors.SELECTED_DETAIL_ROOT)
    const detail = pick(doc, BossSelectors.TITLE)
    if (selectedDetail.node && detail.value) return 'detail'

    const cards = pickAll(doc, BossSelectors.CARD)

    if (cards.nodes.length > 1) return 'search'
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
    // Live split-pane details may render "公司 · 招聘者职位" in the same
    // attribute node. The left side is the company used for card correlation.
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
    const rawSalary = pick(doc, BossSelectors.SALARY)
    const explicitSalaryUsable = isUsableSalary(rawSalary.value)
    let salaryUnusableSeen = !!rawSalary.value && !explicitSalaryUsable
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

    const selectedCard = findUniqueSelectedCard(doc, candidate.title, candidate.company)
    if (selectedCard) {
      const selectedSalary = selectedCard.salary_text
      if (isUsableSalary(selectedSalary)) {
        if (!candidate.salary_text) {
          candidate.salary_text = selectedSalary
          copyMatchedSelector(candidate, selectedCard, 'salary_text')
        }
      } else if (selectedSalary) {
        salaryUnusableSeen = true
      }
      if (!candidate.city) {
        candidate.city = selectedCard.city
        copyMatchedSelector(candidate, selectedCard, 'city')
      }
      if (!candidate.experience_text) {
        candidate.experience_text = selectedCard.experience_text
        copyMatchedSelector(candidate, selectedCard, 'experience_text', 'tags')
      }
      if (!candidate.education_text) {
        candidate.education_text = selectedCard.education_text
        copyMatchedSelector(candidate, selectedCard, 'education_text', 'tags')
      }
    }

    const pageUrl = cleanUrl(url)
    candidate.external_id = externalIdOf(pageUrl)
    candidate.source_url = candidate.external_id ? pageUrl : null
    if (!candidate.source_url && selectedCard?.source_url) {
      candidate.source_url = selectedCard.source_url
      candidate.external_id = selectedCard.external_id
      copyMatchedSelector(candidate, selectedCard, 'source_url')
    }
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

  function copyMatchedSelector(
    target: JobCandidate,
    source: JobCandidate,
    field: string,
    fallbackField?: string,
  ): void {
    const selector = source.matched_selectors[field]
      || (fallbackField ? source.matched_selectors[fallbackField] : null)
    if (selector) target.matched_selectors[field] = selector
  }

  /**
   * Correlate the already-selected right detail with one unique left card.
   * No click or navigation: ambiguity returns null rather than guessing.
   */
  function findUniqueSelectedCard(
    doc: Document,
    title: string | null,
    company: string | null,
  ): JobCandidate | null {
    if (!title) return null
    const candidateRoots = pickAllKnown(doc, BossSelectors.CARD)
    const selectedDetailSelector = BossSelectors.SELECTED_DETAIL_ROOT.join(',')
    const selectedDetailRoot = pickNode(doc, BossSelectors.SELECTED_DETAIL_ROOT).node
    for (const titleNode of pickAllKnown(doc, BossSelectors.CARD_TITLE)) {
      if (text(titleNode) !== title || titleNode.closest(selectedDetailSelector)) continue
      let current: Element | null = titleNode
      for (let level = 0; current && level < 7; level++) {
        // An ancestor that already wraps more than one title-bearing node
        // spans multiple cards (e.g. the whole `<ul>` result list), not one.
        // Adding it would let its fields - picked by "first descendant match
        // anywhere in the subtree" - mix pieces of different cards into a
        // single Frankenstein candidate, and that candidate can collide with
        // a real card's URL and silently overwrite its correct extraction.
        // Climbing must stop here rather than add this ancestor or go higher.
        if (level > 0 && pickAllKnown(current, BossSelectors.CARD_TITLE).length > 1) break
        if (candidateRoots.indexOf(current) === -1) candidateRoots.push(current)
        current = current.parentElement
      }
    }

    const byUrl: Record<string, JobCandidate> = {}
    for (const root of candidateRoots) {
      // A broad card selector can match a split-pane wrapper that also owns
      // the selected detail. Treating that wrapper as a left result card can
      // pair the detail title with an unrelated descendant link and make an
      // otherwise unique selection look ambiguous.
      if (
        selectedDetailRoot
        && (root === selectedDetailRoot
          || root.contains(selectedDetailRoot)
          || selectedDetailRoot.contains(root))
      ) {
        continue
      }
      const card = extractCard(root, 0)
      if (card.title !== title || !card.source_url) continue
      if (
        company
        && cleanDetailCompany({ value: card.company, selector: null }).value !== company
      ) {
        continue
      }
      byUrl[card.source_url] = card
    }
    const urls = Object.keys(byUrl)
    return urls.length === 1 ? byUrl[urls[0]] : null
  }

  function extractCard(card: Element, index: number): JobCandidate {
    const candidate = emptyCandidate()

    candidate.title = record(candidate, 'title', pick(card, BossSelectors.CARD_TITLE))
    candidate.company = record(candidate, 'company', pick(card, BossSelectors.CARD_COMPANY))
    candidate.salary_text = record(candidate, 'salary_text', pick(card, BossSelectors.CARD_SALARY))

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
    if (!candidate.salary_text) candidate.warnings.push('这张卡片上没有可见的薪资。')
    if (!candidate.title) candidate.warnings.push(`第 ${index + 1} 张卡片没能识别出职位名称。`)
    return candidate
  }

  function extractSearch(doc: Document): { candidates: JobCandidate[]; warnings: string[] } {
    const warnings: string[] = []
    const found = pickAll(doc, BossSelectors.CARD)

    // Only what is already rendered. No scrolling, no pagination, no clicking.
    let nodes = found.nodes
    if (nodes.length > MAX_CARDS) {
      warnings.push(`当前页面渲染了 ${nodes.length} 张卡片，只返回前 ${MAX_CARDS} 张。`)
      nodes = nodes.slice(0, MAX_CARDS)
    }

    const candidates = nodes.map((card, index) => extractCard(card, index))
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

  function openCandidateLink(doc: Document, index: number): OpenCandidateResult {
    const cards = pickAll(doc, BossSelectors.CARD).nodes
    if (index < 0 || index >= cards.length) {
      return { ok: false, error: 'out_of_range' }
    }

    const link = pickAll(cards[index], BossSelectors.CARD_LINK)
    if (link.nodes.length === 0) {
      return { ok: false, error: 'no_link' }
    }
    // Exactly one match, whichever selector produced it - anything else is
    // an ambiguous resolution and must not be guessed at.
    if (link.nodes.length !== 1) {
      return { ok: false, error: 'selector_ambiguous' }
    }

    const anchor = link.nodes[0] as HTMLElement
    if (typeof anchor.click !== 'function') {
      return { ok: false, error: 'not_clickable' }
    }
    anchor.click()
    return { ok: true }
  }

  // ------------------------------------------------------ dev-mode diagnostic
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

  // ------------------------------------------------------------------- api

  function detect(doc: Document, url: string): DetectionResult {
    const result: DetectionResult = {
      page_type: 'unsupported',
      url: cleanUrl(url) || '',
      verification: false,
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
    MAX_DESCRIPTION_CHARS,
    MAX_CARDS,
    MAX_DIAGNOSTIC_NODES,
  }
})()
