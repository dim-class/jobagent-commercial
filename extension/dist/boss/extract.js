"use strict";
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
    const MAX_DESCRIPTION_CHARS = 20000;
    const MAX_CARDS = 60;
    /**
     * M4c's stricter bar for a URL this extension is about to click into
     * (`activateNextPage`) - the exact scheme+host `extension/manifest.json`'s
     * `content_scripts.matches` declares and the backend's
     * `REQUIRED_TAB_ORIGIN` requires, never the broader `BossSelectors.HOSTS`
     * used elsewhere for merely *reading* whatever page a human already has
     * open (that list also accepts the bare `zhipin.com` host).
     */
    const REQUIRED_NAV_ORIGIN = 'https://www.zhipin.com';
    function isRequiredNavOrigin(url) {
        if (!url)
            return false;
        return url === REQUIRED_NAV_ORIGIN || url.indexOf(REQUIRED_NAV_ORIGIN + '/') === 0;
    }
    /**
     * Bounds for the developer-mode structural diagnostic (see "dev-mode
     * diagnostic" below). Deliberately small: this is a look near a handful of
     * already-confirmed anchors, never a page-wide dump.
     */
    const MAX_DIAGNOSTIC_NODES = 80;
    const MAX_DIAGNOSTIC_ANCESTORS = 5;
    const MAX_DIAGNOSTIC_SIBLINGS = 6;
    const MAX_DIAGNOSTIC_SUBTREE_DEPTH = 3;
    const MAX_DIAGNOSTIC_SAMPLE_CHARS = 40;
    /** A node's own subtree must be this small before it earns a text sample. */
    const MAX_DIAGNOSTIC_SAMPLE_SUBTREE = 12;
    // ------------------------------------------------------------------ utils
    function text(node) {
        if (!node)
            return '';
        return (node.textContent || '').replace(/\s+/g, ' ').trim();
    }
    /** First selector that yields a non-empty string. Records which one won. */
    function pick(root, selectors) {
        for (const selector of selectors) {
            let node = null;
            try {
                node = root.querySelector(selector);
            }
            catch {
                continue; // a malformed selector must not break the whole extraction
            }
            const value = text(node);
            if (value)
                return { value, selector };
        }
        return { value: null, selector: null };
    }
    function salaryRendered(node) {
        const view = node.ownerDocument?.defaultView;
        if (!view || typeof node.getBoundingClientRect !== 'function')
            return false;
        const rect = node.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0)
            return false;
        for (let el = node; el; el = el.parentElement) {
            const css = view.getComputedStyle(el);
            if (css.display === 'none' || css.visibility !== 'visible' || Number(css.opacity) === 0)
                return false;
        }
        return true;
    }
    function pickSalary(root, selectors) {
        if (!root)
            return { value: null, selector: null, miss: 'no_root' };
        let rejected = { value: null, selector: null };
        let usable = null;
        let seenAny = false;
        for (const selector of selectors) {
            for (const node of Array.from(root.querySelectorAll(selector))) {
                if (!salaryRendered(node))
                    continue;
                seenAny = true;
                const value = text(node);
                if (!isUsableSalary(value)) {
                    if (!rejected.value && value)
                        rejected = { value, selector };
                    continue;
                }
                // Conflicting valid values are not a reason to guess which is current.
                if (usable && usable.value !== value) {
                    return { value: null, selector: null, miss: 'conflict' };
                }
                usable = usable || { value, selector };
            }
        }
        if (usable)
            return { ...usable, miss: null };
        if (rejected.value)
            return { ...rejected, miss: 'unreadable' };
        return { value: null, selector: null, miss: seenAny ? 'unreadable' : 'no_node' };
    }
    function salaryDetailRoot(doc) {
        const title = pickNode(doc, BossSelectors.TITLE).node;
        return title?.closest(BossSelectors.DETAIL_ROOT.join(',')) || null;
    }
    /** Like `pick`, but hands back the matched element itself, not its text. */
    function pickNode(root, selectors) {
        for (const selector of selectors) {
            let node = null;
            try {
                node = root.querySelector(selector);
            }
            catch {
                continue;
            }
            if (node)
                return { node, selector };
        }
        return { node: null, selector: null };
    }
    /** One match per known selector, deduped, for diagnostic coverage only. */
    function pickNodes(root, selectors) {
        const found = [];
        const seen = [];
        for (const selector of selectors) {
            let node = null;
            try {
                node = root.querySelector(selector);
            }
            catch {
                continue;
            }
            if (node && seen.indexOf(node) === -1) {
                seen.push(node);
                found.push({ node, selector });
            }
        }
        return found;
    }
    /** Every match for the first selector that matches anything at all. */
    function pickAll(root, selectors) {
        for (const selector of selectors) {
            let nodes = [];
            try {
                nodes = Array.prototype.slice.call(root.querySelectorAll(selector));
            }
            catch {
                continue;
            }
            if (nodes.length)
                return { nodes, selector };
        }
        return { nodes: [], selector: null };
    }
    /**
     * Scheme + host + path. The entire query string is dropped, not just the
     * parameters in `TRACKING_PARAMS`: BOSS puts `lid` / `securityId` there, the
     * job id lives in the path, and a URL that cannot carry a session token is
     * the only kind worth storing. Matches `canonical_url()` on the backend.
     */
    function cleanUrl(raw) {
        if (!raw)
            return null;
        try {
            const parsed = new URL(raw, document.baseURI);
            if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
                return null;
            return parsed.origin + parsed.pathname;
        }
        catch {
            return null;
        }
    }
    /** Which known tracking parameters were present, for the dev-mode report. */
    function strippedParams(raw) {
        if (!raw)
            return [];
        try {
            const parsed = new URL(raw, document.baseURI);
            return BossSelectors.TRACKING_PARAMS.filter((name) => parsed.searchParams.has(name));
        }
        catch {
            return [];
        }
    }
    function externalIdOf(url) {
        if (!url)
            return null;
        for (const pattern of BossSelectors.JOB_URL_PATTERNS) {
            const match = pattern.exec(url);
            if (match)
                return match[1];
        }
        return null;
    }
    /** Split a "北京 朝阳区 · 3-5年 · 本科" strip into its three parts. */
    function parseInfoTags(parts) {
        let city = null;
        let experience = null;
        let education = null;
        const tokens = [];
        for (const part of parts) {
            for (const piece of part.split(BossSelectors.TAG_SPLIT_RE)) {
                const cleaned = piece.replace(/\s+/g, ' ').trim();
                if (cleaned)
                    tokens.push(cleaned);
            }
        }
        for (const token of tokens) {
            if (!education) {
                const level = BossSelectors.EDUCATION_TOKENS.filter((t) => token.indexOf(t) !== -1)[0];
                if (level) {
                    education = level;
                    continue;
                }
            }
            if (!experience) {
                const match = BossSelectors.EXPERIENCE_RE.exec(token);
                if (match) {
                    experience = match[1].replace(/\s+/g, '');
                    continue;
                }
            }
            // Whatever is left over and looks like a place is the city. The first
            // such token wins; BOSS puts the city before the district.
            if (!city && token.length <= 12 && !/\d/.test(token))
                city = token.split(' ')[0];
        }
        return { city, experience, education };
    }
    // -------------------------------------------------------------- detection
    function isSupportedHost(url) {
        try {
            const host = new URL(url).hostname.toLowerCase();
            return BossSelectors.HOSTS.indexOf(host) !== -1;
        }
        catch {
            return false;
        }
    }
    function pathOf(url) {
        try {
            return new URL(url).pathname;
        }
        catch {
            return '';
        }
    }
    function looksLikeVerification(doc, url) {
        for (const node of Array.from(doc.querySelectorAll(BossSelectors.VERIFICATION_ROOT.join(',')))) {
            if (!salaryRendered(node))
                continue; // ignore hidden, stale challenge templates
            const notice = text(node).slice(0, 400).toLowerCase();
            if (BossSelectors.VERIFICATION_HINTS.some((hint) => notice.includes(hint.toLowerCase())))
                return true;
        }
        const haystack = (url + ' ' + text(doc.querySelector('body')).slice(0, 400)).toLowerCase();
        return BossSelectors.VERIFICATION_HINTS.some((hint) => haystack.indexOf(hint.toLowerCase()) !== -1);
    }
    function looksLikeLoginRequired(doc, url) {
        const path = pathOf(url);
        if (path === '/login' || path.indexOf('/login/') === 0)
            return true;
        for (const node of Array.from(doc.querySelectorAll(BossSelectors.LOGIN_ROOT.join(',')))) {
            if (!salaryRendered(node))
                continue;
            const label = text(node).slice(0, 40);
            if (BossSelectors.LOGIN_HINTS.some((hint) => label.indexOf(hint) !== -1))
                return true;
        }
        return false;
    }
    function detectPageType(doc, url) {
        if (!isSupportedHost(url))
            return 'unsupported';
        const path = pathOf(url);
        if (BossSelectors.BLOCKED_PATH_HINTS.some((hint) => path.indexOf(hint) === 0)) {
            return 'unsupported';
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
        const isSearchListingPath = BossSelectors.SEARCH_LISTING_PATH_HINTS.some((hint) => path.indexOf(hint) === 0);
        const cards = pickAll(doc, BossSelectors.CARD);
        if (isSearchListingPath || cards.nodes.length > 1)
            return 'search';
        const detail = pick(doc, BossSelectors.TITLE);
        if (detail.value)
            return 'detail';
        if (cards.nodes.length === 1)
            return 'search';
        return 'unsupported';
    }
    // ------------------------------------------------------------- extraction
    function emptyCandidate() {
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
        };
    }
    function record(candidate, field, hit) {
        if (hit.value && hit.selector)
            candidate.matched_selectors[field] = hit.selector;
        else
            candidate.missing_fields.push(field);
        return hit.value;
    }
    /**
     * The live `.boss-info-attr` text joins company and recruiter function,
     * e.g. "纳新电子 人力". Remove only a separated, terminal function token;
     * never trim an unseparated suffix that may be part of a company name.
     */
    function cleanDetailCompany(hit) {
        if (!hit.value)
            return hit;
        // Live detail pages may render "公司 · 招聘者职位" in the same attribute
        // node. The left side is the company; the right side is not.
        const companyPart = hit.value.split(/\s*[·•|｜]\s*/, 1)[0];
        const value = companyPart
            .replace(/(?:\s+|\s*[·•|｜]\s*)(?:人力(?:资源)?|人事|HR(?:BP)?|招聘(?:专员|经理)?)\s*$/i, '')
            .trim();
        return { value: value || hit.value, selector: hit.selector };
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
    function cleanDescriptionWatermark(value) {
        let withoutObservedInsertion = value;
        for (const [corrupted, clean] of BossSelectors.DESCRIPTION_WATERMARK_INSERTIONS) {
            withoutObservedInsertion = withoutObservedInsertion.split(corrupted).join(clean);
        }
        if (withoutObservedInsertion.indexOf('BOSS直聘') === -1) {
            return withoutObservedInsertion;
        }
        return withoutObservedInsertion.replace(/BOSS直聘/g, '').replace(/^直聘(?=\S)/, '');
    }
    /**
     * BOSS renders some salaries through a private-use-area glyph font as an
     * anti-scraping measure; the underlying text is unusable garbage, not real
     * digits. Checked via character codes (0xE000-0xF8FF), not a `\u` regex
     * literal, so the range boundary is never mangled by escape processing.
     */
    function containsPuaGlyphs(value) {
        if (!value)
            return false;
        for (let i = 0; i < value.length; i++) {
            const code = value.charCodeAt(i);
            if (code >= 0xe000 && code <= 0xf8ff)
                return true;
        }
        return false;
    }
    /**
     * A salary string is trusted only if it plausibly carries a real figure: a
     * digit, or one of the known no-digit-but-real values ("面议"). Anything
     * else - an empty placeholder like "-K", or text corrupted by the PUA glyph
     * font - must not be imported as if it were a real salary.
     */
    function isUsableSalary(value) {
        if (!value || containsPuaGlyphs(value))
            return false;
        if (/\d/.test(value))
            return true;
        return BossSelectors.NEGOTIABLE_SALARY_TOKENS.indexOf(value) !== -1;
    }
    /** Remove only the exact, live-observed watermark tokens from a clone. */
    function removeDescriptionWatermarkNodes(clone) {
        const tokens = ['kanzhun', '直聘', 'boss'];
        for (const span of Array.prototype.slice.call(clone.querySelectorAll('span'))) {
            const value = text(span).toLowerCase();
            if (tokens.indexOf(value) !== -1)
                span.remove();
        }
    }
    /** The description body, with page furniture removed and length capped. */
    function extractDescription(doc, candidate) {
        for (const selector of BossSelectors.DESCRIPTION) {
            let node = null;
            try {
                node = doc.querySelector(selector);
            }
            catch {
                continue;
            }
            if (!node)
                continue;
            // Work on a clone so the page the user is looking at is never modified.
            const clone = node.cloneNode(true);
            removeDescriptionWatermarkNodes(clone);
            for (const noise of BossSelectors.NOISE_WITHIN_DESCRIPTION) {
                try {
                    Array.prototype.forEach.call(clone.querySelectorAll(noise), (el) => el.remove());
                }
                catch {
                    /* a bad noise selector must not lose us the description */
                }
            }
            const body = cleanDescriptionWatermark(clone.textContent || '')
                .replace(/\r\n/g, '\n')
                .replace(/[ \t ]+/g, ' ')
                .replace(/\n{3,}/g, '\n\n')
                .trim();
            if (body) {
                candidate.matched_selectors.description = selector;
                if (body.length > MAX_DESCRIPTION_CHARS) {
                    candidate.warnings.push('职位描述过长，已截断后再发送。');
                    return body.slice(0, MAX_DESCRIPTION_CHARS);
                }
                return body;
            }
        }
        candidate.missing_fields.push('description');
        return null;
    }
    function extractDetail(doc, url) {
        const candidate = emptyCandidate();
        candidate.title = record(candidate, 'title', pick(doc, BossSelectors.TITLE));
        candidate.company = record(candidate, 'company', cleanDetailCompany(pick(doc, BossSelectors.COMPANY)));
        const rawSalary = pickSalary(salaryDetailRoot(doc), BossSelectors.SALARY);
        const explicitSalaryUsable = isUsableSalary(rawSalary.value);
        const salaryUnusableSeen = !!rawSalary.value && !explicitSalaryUsable;
        candidate.salary_text = record(candidate, 'salary_text', explicitSalaryUsable ? rawSalary : { value: null, selector: null });
        // Keep selector diagnostics truthful even when the matched node contains
        // only unusable private-font glyphs. The value remains null and missing.
        if (rawSalary.value && rawSalary.selector && !explicitSalaryUsable) {
            candidate.matched_selectors.salary_text = rawSalary.selector;
        }
        // The category, not the figure - compensation values are never recorded in
        // a note or a log. Without this the intake note could only say that the OCR
        // fallback was refused, which explains nothing about the DOM read.
        if (rawSalary.miss) {
            candidate.warnings.push(`薪资未读到：${rawSalary.miss}`);
        }
        const tags = pickAll(doc, BossSelectors.INFO_TAGS);
        const parsed = parseInfoTags(tags.nodes.map((node) => text(node)));
        if (tags.selector)
            candidate.matched_selectors.info_tags = tags.selector;
        const explicitFields = [
            ['city', pick(doc, BossSelectors.DETAIL_CITY), parsed.city],
            ['experience_text', pick(doc, BossSelectors.DETAIL_EXPERIENCE), parsed.experience],
            ['education_text', pick(doc, BossSelectors.DETAIL_EDUCATION), parsed.education],
        ];
        for (const [field, hit, fallback] of explicitFields) {
            const value = hit.value || fallback;
            if (field === 'city')
                candidate.city = value;
            else if (field === 'experience_text')
                candidate.experience_text = value;
            else
                candidate.education_text = value;
            if (value && hit.selector)
                candidate.matched_selectors[field] = hit.selector;
            else if (value && tags.selector)
                candidate.matched_selectors[field] = tags.selector;
            else
                candidate.missing_fields.push(field);
        }
        const pageUrl = cleanUrl(url);
        candidate.external_id = externalIdOf(pageUrl);
        candidate.source_url = candidate.external_id ? pageUrl : null;
        if (!candidate.source_url)
            candidate.missing_fields.push('source_url');
        candidate.missing_fields = candidate.missing_fields.filter((field) => {
            if (field === 'salary_text')
                return !candidate.salary_text;
            if (field === 'city')
                return !candidate.city;
            if (field === 'experience_text')
                return !candidate.experience_text;
            if (field === 'education_text')
                return !candidate.education_text;
            return true;
        });
        candidate.description = extractDescription(doc, candidate);
        if (!candidate.salary_text) {
            candidate.warnings.push(salaryUnusableSeen
                ? '页面上的薪资显示异常（可能被遮挡或使用了特殊字体），未能可靠读出数字，需要你手动核对并补充。'
                : '页面上没有可见的薪资，需要你手动补充。');
        }
        if (!candidate.description) {
            candidate.warnings.push('没有找到职位描述容器，可能是页面还没加载完或改版了。');
        }
        return candidate;
    }
    function extractCard(card, index) {
        const candidate = emptyCandidate();
        candidate.title = record(candidate, 'title', pick(card, BossSelectors.CARD_TITLE));
        candidate.company = record(candidate, 'company', pick(card, BossSelectors.CARD_COMPANY));
        const rawCardSalary = pickSalary(card, BossSelectors.CARD_SALARY);
        const cardSalaryUsable = isUsableSalary(rawCardSalary.value);
        candidate.salary_text = record(candidate, 'salary_text', cardSalaryUsable ? rawCardSalary : { value: null, selector: null });
        // Keep selector diagnostics truthful even when the matched node contains
        // only unusable private-font glyphs (e.g. "-K") - see `isUsableSalary`.
        if (rawCardSalary.value && rawCardSalary.selector && !cardSalaryUsable) {
            candidate.matched_selectors.salary_text = rawCardSalary.selector;
        }
        const area = pick(card, BossSelectors.CARD_AREA);
        if (area.value && area.selector)
            candidate.matched_selectors.city = area.selector;
        // "北京·朝阳区·望京" - the city is the first segment.
        candidate.city = area.value ? area.value.split(BossSelectors.TAG_SPLIT_RE)[0].trim() : null;
        if (!candidate.city)
            candidate.missing_fields.push('city');
        const tags = pickAll(card, BossSelectors.CARD_TAGS);
        if (tags.selector)
            candidate.matched_selectors.tags = tags.selector;
        const parsed = parseInfoTags(tags.nodes.map((node) => text(node)));
        candidate.experience_text = parsed.experience;
        candidate.education_text = parsed.education;
        if (!parsed.experience)
            candidate.missing_fields.push('experience_text');
        if (!parsed.education)
            candidate.missing_fields.push('education_text');
        const link = pickAll(card, BossSelectors.CARD_LINK);
        const href = link.nodes.length ? link.nodes[0].getAttribute('href') : null;
        candidate.source_url = cleanUrl(href);
        if (candidate.source_url && link.selector)
            candidate.matched_selectors.source_url = link.selector;
        else if (!candidate.source_url)
            candidate.missing_fields.push('source_url');
        candidate.external_id = externalIdOf(candidate.source_url);
        // A card teaser is a one-line marketing blurb, never a job description.
        const teaser = pick(card, BossSelectors.CARD_TEASER);
        if (teaser.value)
            candidate.matched_selectors.teaser = teaser.selector;
        candidate.description = null;
        candidate.missing_fields.push('description');
        candidate.warnings.push('搜索结果卡片没有职位描述。要导入这个岗位，请先打开它的详情页再检测。');
        if (!candidate.salary_text) {
            candidate.warnings.push(rawCardSalary.value && !cardSalaryUsable
                ? '这张卡片上的薪资显示异常（可能使用了特殊字体），未能可靠读出数字，需要你手动核对并补充。'
                : '这张卡片上没有可见的薪资。');
        }
        if (!candidate.title)
            candidate.warnings.push(`第 ${index + 1} 张卡片没能识别出职位名称。`);
        return candidate;
    }
    const MAX_FALLBACK_CARD_ANCESTOR_LEVELS = 6;
    /** Every `/job_detail/` anchor on the page, excluding the selected-pane's
     * own (unrelated) link - the pane is never a result card. */
    function jobDetailAnchors(root) {
        const anchors = Array.prototype.slice.call(root.querySelectorAll('a[href*="/job_detail/"]'));
        return anchors.filter((a) => !a.closest('.job-detail-box'));
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
    function discoverCardsFromJobDetailAnchors(doc) {
        const results = [];
        const seen = new Set();
        for (const anchor of jobDetailAnchors(doc)) {
            const canonicalUrl = cleanUrl(anchor.getAttribute('href'));
            if (!canonicalUrl || !externalIdOf(canonicalUrl))
                continue;
            if (seen.has(canonicalUrl))
                continue;
            if (results.length >= MAX_CARDS)
                break;
            let root = anchor;
            let current = anchor;
            for (let level = 0; level < MAX_FALLBACK_CARD_ANCESTOR_LEVELS; level++) {
                const parent = current.parentElement;
                if (!parent)
                    break;
                const distinctInParent = new Set(jobDetailAnchors(parent)
                    .map((a) => cleanUrl(a.getAttribute('href')))
                    .filter((u) => !!u));
                if (distinctInParent.size > 1)
                    break; // parent already spans another card
                root = parent;
                current = parent;
            }
            seen.add(canonicalUrl);
            results.push({ root, anchor, canonicalUrl });
        }
        return results;
    }
    /** The one place both `extractSearch` and `openCandidateLink` get their
     * card list from, so the index one reports is always the index the other
     * clicks - fallback or not. */
    function findCardRoots(doc) {
        const found = pickAll(doc, BossSelectors.CARD);
        if (found.nodes.length) {
            return {
                nodes: found.nodes,
                anchors: found.nodes.map(() => null),
                canonicalUrls: found.nodes.map(() => null),
                usingFallback: false,
            };
        }
        const fallback = discoverCardsFromJobDetailAnchors(doc);
        return {
            nodes: fallback.map((f) => f.root),
            anchors: fallback.map((f) => f.anchor),
            canonicalUrls: fallback.map((f) => f.canonicalUrl),
            usingFallback: true,
        };
    }
    function extractSearch(doc) {
        const warnings = [];
        const found = findCardRoots(doc);
        // Only what is already rendered. No scrolling, no pagination, no clicking.
        let nodes = found.nodes;
        let canonicalUrls = found.canonicalUrls;
        if (nodes.length > MAX_CARDS) {
            warnings.push(`当前页面渲染了 ${nodes.length} 张卡片，只返回前 ${MAX_CARDS} 张。`);
            nodes = nodes.slice(0, MAX_CARDS);
            canonicalUrls = canonicalUrls.slice(0, MAX_CARDS);
        }
        if (found.usingFallback && nodes.length) {
            warnings.push('固定卡片选择器未命中，已根据职位详情链接回退识别候选人卡片。');
        }
        const candidates = nodes.map((card, index) => {
            const candidate = extractCard(card, index);
            // The fallback's own canonical URL is authoritative - it is exactly
            // how this card root was found, so it always wins over whatever
            // `extractCard`'s own (unscoped, best-effort) link lookup guessed.
            const canonicalUrl = canonicalUrls[index];
            if (found.usingFallback && canonicalUrl) {
                candidate.source_url = canonicalUrl;
                candidate.external_id = externalIdOf(canonicalUrl);
                candidate.matched_selectors.source_url = 'a[href*="/job_detail/"]';
                candidate.missing_fields = candidate.missing_fields.filter((f) => f !== 'source_url');
            }
            return candidate;
        });
        const usable = candidates.filter((c) => c.title).length;
        if (usable !== candidates.length) {
            warnings.push(`${candidates.length - usable} 张卡片缺少职位名称，可能是广告位或占位卡片。`);
        }
        return { candidates, warnings };
    }
    /**
     * The one click primitive in the whole extension - every navigation path
     * (fixed-selector cards and the anchor-discovery fallback alike) ends
     * here, and only here, so there is exactly one call site that invokes
     * `click` to audit, not two that could quietly drift apart.
     */
    function clickAnchor(candidateAnchor) {
        const anchor = candidateAnchor;
        if (!anchor || typeof anchor.click !== 'function') {
            return { ok: false, error: 'not_clickable' };
        }
        anchor.click();
        return { ok: true };
    }
    /**
     * Clicks one already-rendered card. `expectedUrl` is what makes it the
     * right one.
     *
     * A position is not an identity. The caller decides which card to open from
     * a DETECT it made earlier, and this re-derives the list independently, so
     * anything that re-renders between the two calls changes what an index
     * means - BOSS opens its detail pane on the first card as the results page
     * settles, which shifts the list on its own. On 2026-09-07 that surfaced as
     * `out_of_range` on the very first candidate, killing a sixteen-task batch;
     * the quieter version of the same bug clicks a different posting than the
     * one the caller decided on, and nothing downstream would notice.
     *
     * With `expectedUrl` the card is found by its own canonical URL and the
     * index is only a hint. Gone means gone (`card_gone`) - a skippable
     * condition, not a failed run - and two matches refuse rather than pick.
     */
    function openCandidateLink(doc, index, expectedUrl) {
        // Same discovery `extractSearch` used to report this index, fallback or
        // not - so a click always lands on the card the popup/overlay actually
        // showed at that position.
        const found = findCardRoots(doc);
        if (expectedUrl) {
            const wanted = cleanUrl(expectedUrl);
            const matches = [];
            for (let i = 0; i < found.nodes.length; i += 1) {
                const own = found.canonicalUrls[i];
                const url = own
                    ? cleanUrl(own)
                    : cleanUrl(extractCard(found.nodes[i], i).source_url || '');
                if (url && url === wanted)
                    matches.push(i);
            }
            if (matches.length === 0)
                return { ok: false, error: 'card_gone' };
            if (matches.length > 1)
                return { ok: false, error: 'card_ambiguous' };
            index = matches[0];
        }
        if (index < 0 || index >= found.nodes.length) {
            return { ok: false, error: 'out_of_range' };
        }
        if (found.usingFallback) {
            // The anchor discovery already found and verified this card's one
            // canonical link - no re-search, so there is nothing left to be
            // ambiguous about.
            return clickAnchor(found.anchors[index]);
        }
        const link = pickAll(found.nodes[index], BossSelectors.CARD_LINK);
        if (link.nodes.length === 0) {
            return { ok: false, error: 'no_link' };
        }
        // Exactly one match, whichever selector produced it - anything else is
        // an ambiguous resolution and must not be guessed at.
        if (link.nodes.length !== 1) {
            return { ok: false, error: 'selector_ambiguous' };
        }
        return clickAnchor(link.nodes[0]);
    }
    /**
     * Resolves the results container the same way `activateNextPage` resolves
     * its control: the first `SCROLL_CONTAINER` selector that matches anything
     * must match exactly one element - review finding fixed here - never the
     * first of several silently picked via a bare `querySelector`. No match
     * at all is not ambiguity; it falls through to the page/viewport itself.
     */
    /** The element that actually scrolls, starting from the results list.
     *
     * `.job-list-box` is where the cards live, but it is not necessarily the
     * element with the scrollbar - and `scrollBy` on a container that does not
     * scroll is a silent no-op: no error, no movement, no lazy load. That is
     * what kept eleven of sixteen directions pinned to BOSS's first 15 cards on
     * 2026-09-05 while two others reached 60. The two that worked were the ones
     * that opened many details: clicking a card low in the list makes the
     * browser scroll it into view, which loaded the next batch by accident.
     *
     * Walks up a bounded number of ancestors for the first one that both is
     * scrollable by style and has somewhere to scroll, and falls back to the
     * document. Read-only; it moves nothing itself.
     */
    function scrollableFor(doc, start) {
        const view = doc.defaultView;
        let node = start;
        for (let up = 0; up < 6 && node; up += 1) {
            const overflow = view ? view.getComputedStyle(node).overflowY : '';
            const scrolls = overflow === 'auto' || overflow === 'scroll' || overflow === 'overlay';
            if (scrolls && node.scrollHeight > node.clientHeight + 4)
                return node;
            node = node.parentElement;
        }
        return doc.scrollingElement || doc.documentElement;
    }
    function scrollResultsContainer(doc) {
        const found = pickAll(doc, BossSelectors.SCROLL_CONTAINER);
        if (found.nodes.length > 1)
            return { ok: false, error: 'container_ambiguous' };
        const container = scrollableFor(doc, found.nodes[0] || null);
        const view = doc.defaultView;
        const step = (container && container.clientHeight) || (view ? view.innerHeight : 0);
        if (!container || !step || typeof container.scrollBy !== 'function') {
            return { ok: false, error: 'not_scrollable' };
        }
        // Reach the BOTTOM, not one screen further down. BOSS loads the next batch
        // when the list's end comes into view, and a fixed one-viewport step falls
        // behind the moment the list grows: the first scroll reached the end of 15
        // cards and pulled in 15 more, and every scroll after that landed in the
        // middle of a list whose end had moved away. Measured on 2026-09-05:
        // every task, every city, every keyword reported exactly 30 observed cards
        // and then nothing - which read like a BOSS limit and was ours. A human
        // dragging the scrollbar down does what this line now does.
        //
        // Still exactly one `scrollBy` call site (a contract test pins the count),
        // still one scroll per approved round, still no `scrollTo`/`scrollIntoView`
        // anywhere: only the distance changed.
        const remaining = container.scrollHeight - container.scrollTop - container.clientHeight;
        const rendered = readFrameProbe(view);
        container.scrollBy({ top: Math.max(step, remaining), left: 0 });
        return { ok: true, rendered };
    }
    /**
     * Reads whether a frame was painted since the last scroll step, and arms
     * the probe for the next one. Exactly one `requestAnimationFrame` per
     * scroll step: a bounded, single-shot callback, not a loop and not a poll.
     */
    function readFrameProbe(view) {
        if (!view || typeof view.requestAnimationFrame !== 'function')
            return undefined;
        const host = view;
        const probe = host.__jobagentFrameProbe || { frames: 0, framesAtLastScroll: 0, armed: false };
        host.__jobagentFrameProbe = probe;
        // A counter, not a timestamp: `Date.now()` has millisecond resolution and
        // a frame can land inside the same millisecond as the scroll that armed
        // it, which reads as "no frame" and would report a painting tab frozen.
        const rendered = probe.armed ? probe.frames > probe.framesAtLastScroll : undefined;
        probe.framesAtLastScroll = probe.frames;
        probe.armed = true;
        view.requestAnimationFrame(() => {
            probe.frames += 1;
        });
        return rendered;
    }
    function isNextPageDisabled(el) {
        const classes = Array.prototype.slice.call(el.classList).join(' ').toLowerCase();
        if (BossSelectors.NEXT_PAGE_DISABLED_CLASS_HINTS.some((hint) => classes.indexOf(hint) !== -1)) {
            return true;
        }
        return el.getAttribute('aria-disabled') === 'true';
    }
    function activateNextPage(doc) {
        const found = pickAll(doc, BossSelectors.NEXT_PAGE);
        if (found.nodes.length === 0)
            return { ok: false, error: 'no_control' };
        if (found.nodes.length !== 1)
            return { ok: false, error: 'control_ambiguous' };
        const control = found.nodes[0];
        if (isNextPageDisabled(control))
            return { ok: false, error: 'disabled' };
        if (control.tagName === 'A') {
            const href = control.getAttribute('href');
            // A same-page (`href="#"`/empty/relative-to-here) control has nothing
            // to check; one that names a different page must resolve to *exactly*
            // `https://www.zhipin.com` - review finding fixed here - not merely
            // any host `isSupportedHost` would read a page from (that list also
            // accepts the bare `zhipin.com` host, which is fine for reading a
            // page a human already opened but too loose a bar for a URL this
            // extension itself is about to click into).
            if (href && href !== '#' && !isRequiredNavOrigin(cleanUrl(href))) {
                return { ok: false, error: 'wrong_origin' };
            }
        }
        const result = clickAnchor(control);
        return result.ok ? { ok: true } : { ok: false, error: 'not_clickable' };
    }
    /** The identity used to compare a card and a pane: cleaned the same way a
     * detail page's own company text is, so "纳新电子" and "纳新电子 人力" agree. */
    function companyIdentity(value) {
        if (!value)
            return null;
        return cleanDetailCompany({ value, selector: null }).value;
    }
    function captureAndMerge(doc, currentUrl, canonicalUrl, cachedCard) {
        if (looksLikeLoginRequired(doc, currentUrl)) {
            return { status: 'login_required', candidate: null };
        }
        if (looksLikeVerification(doc, currentUrl)) {
            return { status: 'verification', candidate: null };
        }
        // `canonicalUrl` is already known-correct (it is exactly the URL phase
        // one clicked), so it - not whatever the address bar currently shows -
        // is what resolves this candidate's own source_url/external_id.
        const pane = extractDetail(doc, canonicalUrl);
        if (!pane.title) {
            // Inconclusive, not a confirmed violation - the pane may still be
            // loading. The caller must let the human simply try again.
            return { status: 'not_loaded', candidate: null };
        }
        if (pane.title !== cachedCard.title) {
            return { status: 'identity_mismatch', candidate: null };
        }
        const cachedCompany = companyIdentity(cachedCard.company);
        if (cachedCompany && pane.company && companyIdentity(pane.company) !== cachedCompany) {
            return { status: 'identity_mismatch', candidate: null };
        }
        // Never trust a caller-supplied cached salary at face value - the same
        // `isUsableSalary` rule applies here too, so stale/malformed cached
        // state (e.g. a card captured before this fix, or any other source of
        // `CachedCardLike`) can never override a usable pane value or smuggle
        // PUA-glyph text into intake.
        const cachedSalaryUsable = isUsableSalary(cachedCard.salary_text);
        const usableCachedSalary = cachedSalaryUsable ? cachedCard.salary_text : null;
        const merged = emptyCandidate();
        merged.title = cachedCard.title;
        merged.company = cachedCard.company || pane.company;
        merged.salary_text = usableCachedSalary || pane.salary_text;
        merged.city = cachedCard.city || pane.city;
        merged.experience_text = cachedCard.experience_text || pane.experience_text;
        merged.education_text = cachedCard.education_text || pane.education_text;
        merged.source_url = cachedCard.source_url || pane.source_url;
        merged.external_id = cachedCard.external_id || pane.external_id;
        // Only the pane ever carries a description - cards never do.
        merged.description = pane.description;
        // Later keys win: a field the card itself supplied keeps the card's own
        // selector attribution; only a field the card lacked shows the pane's.
        // An unusable cached salary must not leave behind a selector pointing at
        // the rejected card value while the actual merged value came from the
        // pane (or from neither) - so its own diagnostic selector is dropped
        // here rather than allowed to override the pane's.
        const cachedSelectors = { ...(cachedCard.matched_selectors || {}) };
        if (!cachedSalaryUsable)
            delete cachedSelectors.salary_text;
        merged.matched_selectors = { ...pane.matched_selectors, ...cachedSelectors };
        merged.missing_fields = [
            'title',
            'company',
            'salary_text',
            'city',
            'experience_text',
            'education_text',
            'source_url',
            'description',
        ].filter((field) => !merged[field]);
        merged.warnings = pane.warnings.slice();
        return { status: 'ok', candidate: merged };
    }
    // ------------------------------------------------------ dev-mode diagnostic
    const salaryNodeIds = new WeakMap();
    let salaryNodeSequence = 0;
    /** Read-only screenshot proof: exact canonical card URL, or standalone detail URL.
     * A pane is eligible only when its own job link proves the same identity.
     * Offscreen, ambiguous, clipped or covered regions never earn a screenshot.
     */
    function salaryFrame(doc, currentUrl, canonicalUrl, expectedTitle) {
        const fail = (status) => ({ status, frame: null });
        if (looksLikeLoginRequired(doc, currentUrl))
            return fail('login_required');
        if (looksLikeVerification(doc, currentUrl))
            return fail('verification');
        const clean = cleanUrl(canonicalUrl);
        if (!clean || clean !== canonicalUrl || !externalIdOf(clean) || !isRequiredNavOrigin(clean))
            return fail('identity_mismatch');
        const view = doc.defaultView;
        if (!view || doc.visibilityState !== 'visible' || (view.visualViewport && view.visualViewport.scale !== 1))
            return fail('not_visible');
        const roots = [];
        const detailRoot = salaryDetailRoot(doc);
        if (externalIdOf(cleanUrl(currentUrl))) {
            if (cleanUrl(currentUrl) !== canonicalUrl || pick(doc, BossSelectors.TITLE).value !== expectedTitle)
                return fail('identity_mismatch');
            if (detailRoot)
                roots.push(detailRoot);
        }
        else {
            const found = findCardRoots(doc);
            for (let i = 0; i < found.nodes.length; i++) {
                const card = found.nodes[i];
                const urls = new Set(Array.from(card.querySelectorAll(BossSelectors.DETAIL_JOB_LINK.join(',')))
                    .map((el) => cleanUrl(el.getAttribute('href'))).filter(Boolean));
                if (urls.size === 1 && urls.has(canonicalUrl) && pick(card, BossSelectors.CARD_TITLE).value === expectedTitle)
                    roots.push(card);
            }
            if (roots.length > 1)
                return fail('ambiguous');
            if (detailRoot && pick(doc, BossSelectors.TITLE).value === expectedTitle) {
                const urls = new Set(Array.from(detailRoot.querySelectorAll(BossSelectors.DETAIL_JOB_LINK.join(',')))
                    .map((el) => cleanUrl(el.getAttribute('href'))).filter(Boolean));
                if (urls.size === 1 && urls.has(canonicalUrl))
                    roots.push(detailRoot);
            }
        }
        for (const root of roots) {
            const nodes = Array.from(root.querySelectorAll(BossSelectors.SALARY_NODE.join(',')))
                .filter(salaryRendered);
            if (nodes.length !== 1)
                continue;
            const node = nodes[0];
            const raw = text(node);
            // Text hint guards units/suffixes; no glyph-to-number mapping is attempted.
            if (!raw || raw.length > 40 || !/[Kk万]/.test(raw))
                continue;
            const r = node.getBoundingClientRect();
            if (r.width < 6 || r.height < 4 || r.width > 400 || r.height > 80 || r.left < 2 || r.top < 2 || r.right > view.innerWidth - 2 || r.bottom > view.innerHeight - 2)
                continue;
            if (node.scrollWidth > node.clientWidth + 1 && node.clientWidth > 0)
                continue;
            let covered = false;
            for (const [x, y] of [[r.left + 1, r.top + 1], [r.right - 1, r.top + 1], [r.left + 1, r.bottom - 1], [r.right - 1, r.bottom - 1], [r.left + r.width / 2, r.top + r.height / 2]]) {
                const hit = doc.elementFromPoint(x, y);
                if (!hit || !(hit === node || node.contains(hit)))
                    covered = true;
            }
            // Reject overflow clipping even if the center happens to be visible.
            for (let parent = node.parentElement; parent; parent = parent.parentElement) {
                const css = view.getComputedStyle(parent);
                const pr = parent.getBoundingClientRect();
                if ((css.overflowX !== 'visible' && (r.left < pr.left || r.right > pr.right)) ||
                    (css.overflowY !== 'visible' && (r.top < pr.top || r.bottom > pr.bottom)))
                    covered = true;
            }
            if (covered)
                continue;
            if (!salaryNodeIds.has(node))
                salaryNodeIds.set(node, ++salaryNodeSequence);
            return { status: 'ok', frame: {
                    canonicalUrl, title: expectedTitle, raw, nodeId: salaryNodeIds.get(node),
                    pageUrl: cleanUrl(currentUrl), x: r.left, y: r.top, width: r.width, height: r.height,
                    viewportWidth: view.innerWidth, viewportHeight: view.innerHeight,
                    scrollX: view.scrollX, scrollY: view.scrollY,
                } };
        }
        return fail('no_safe_region');
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
    function isDiagnosticExcluded(el) {
        const tag = el.tagName.toLowerCase();
        if (BossSelectors.DIAGNOSTIC_EXCLUDE_TAGS.indexOf(tag) !== -1)
            return true;
        const classes = Array.prototype.slice.call(el.classList).join(' ').toLowerCase();
        return BossSelectors.DIAGNOSTIC_EXCLUDE_CLASS_HINTS.some((hint) => {
            // The split-pane title strip is trusted and anchored inside the selected
            // detail; do not confuse its semantic class with the global page header.
            if (hint === 'header' && classes.indexOf('job-detail') !== -1)
                return false;
            return classes.indexOf(hint) !== -1;
        });
    }
    function sanitizeClasses(el) {
        return Array.prototype.slice.call(el.classList)
            .filter((cls) => cls.length > 0 && cls.length <= 40)
            .slice(0, 8);
    }
    /** Only this element's own text nodes; never inherit private descendant text. */
    function directText(el) {
        const parts = [];
        for (const node of Array.prototype.slice.call(el.childNodes)) {
            if (node.nodeType === 3 && node.textContent)
                parts.push(node.textContent);
        }
        return parts.join(' ');
    }
    /** Scrub anything that looks like an identifier, not a job description word. */
    function sanitizeSample(raw) {
        let value = raw
            .replace(/[\w.+-]+@[\w-]+\.[\w.-]+/g, '【邮箱】')
            .replace(/\b(?:https?:\/\/|www\.)\S+/gi, '【链接】')
            .replace(/\b(?:securityid|sessionid|token|auth)\s*[:=]\s*\S+/gi, '【令牌】')
            .replace(/\b(?=[A-Za-z0-9_-]{16,}\b)(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+\b/g, '【标识符】')
            .replace(/1[3-9]\d{9}/g, '【电话】')
            .replace(/\d{6,}/g, '【数字】')
            .replace(/\s+/g, ' ')
            .trim();
        if (!value)
            return null;
        if (value.length > MAX_DIAGNOSTIC_SAMPLE_CHARS) {
            value = value.slice(0, MAX_DIAGNOSTIC_SAMPLE_CHARS) + '…';
        }
        return value;
    }
    function describeDiagnosticNode(el, relation) {
        let subtreeSize = Infinity;
        try {
            subtreeSize = el.getElementsByTagName('*').length;
        }
        catch {
            /* an unreadable subtree just gets no sample */
        }
        const small = subtreeSize <= MAX_DIAGNOSTIC_SAMPLE_SUBTREE;
        return {
            relation,
            tag: el.tagName.toLowerCase(),
            classes: sanitizeClasses(el),
            sample: small ? sanitizeSample(directText(el)) : null,
        };
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
    function collectAnchorDiagnostic(anchor, selector, root, budget, climbAncestors) {
        const result = {
            anchor,
            anchor_selector: selector,
            found: !!root,
            nodes: [],
        };
        if (!root)
            return result;
        function add(el, relation) {
            if (budget.remaining <= 0)
                return false;
            if (isDiagnosticExcluded(el))
                return true;
            result.nodes.push(describeDiagnosticNode(el, relation));
            budget.remaining -= 1;
            return true;
        }
        if (!add(root, 'anchor'))
            return result;
        function addSubtree(start, relation, maxDepth) {
            const queue = [];
            for (const child of Array.prototype.slice.call(start.children)) {
                queue.push({ node: child, depth: 1 });
            }
            while (queue.length && budget.remaining > 0) {
                const current = queue.shift();
                if (isDiagnosticExcluded(current.node))
                    continue;
                if (!add(current.node, `${relation}.child(${current.depth})`))
                    return;
                if (current.depth < maxDepth) {
                    for (const child of Array.prototype.slice.call(current.node.children)) {
                        queue.push({ node: child, depth: current.depth + 1 });
                    }
                }
            }
        }
        if (!climbAncestors) {
            // Bounded breadth-first structure below each known detail root. Direct
            // text only means a safe parent can never inherit a private descendant.
            addSubtree(root, 'root', MAX_DIAGNOSTIC_SUBTREE_DEPTH);
            return result;
        }
        let current = root;
        let level = 0;
        while (current.parentElement && level < MAX_DIAGNOSTIC_ANCESTORS && budget.remaining > 0) {
            const parent = current.parentElement;
            if (isDiagnosticExcluded(parent))
                break;
            if (!add(parent, `parent(${level + 1})`))
                break;
            const siblings = Array.prototype.slice.call(parent.children);
            let count = 0;
            for (const sibling of siblings) {
                if (sibling === current)
                    continue;
                if (count >= MAX_DIAGNOSTIC_SIBLINGS)
                    break;
                if (!add(sibling, `sibling(${level + 1})`))
                    break;
                // Text/tag strips commonly use p/ul/dl. Expand those shallowly, but
                // do not spend the shared budget walking arbitrary furniture divs.
                if (['p', 'ul', 'dl'].indexOf(sibling.tagName.toLowerCase()) !== -1) {
                    addSubtree(sibling, `sibling(${level + 1})`, 2);
                }
                count++;
            }
            current = parent;
            level++;
        }
        return result;
    }
    /** Detail pages only: there is no "confirmed anchor" concept for a card. */
    function diagnoseDetail(doc, url) {
        const result = {
            page_type: 'unsupported',
            url: cleanUrl(url) || '',
            anchors: [],
            application_control: diagnoseApplicationControl(doc),
            truncated: false,
            warnings: [],
            errors: [],
        };
        if (!isSupportedHost(url)) {
            result.errors.push('当前页面不是 BOSS 直聘（www.zhipin.com），没有可诊断的内容。');
            return result;
        }
        if (looksLikeVerification(doc, url)) {
            result.warnings.push('BOSS 正在显示安全验证页面，诊断结果可能不完整。');
        }
        result.page_type = detectPageType(doc, url);
        if (result.page_type !== 'detail') {
            result.errors.push('结构诊断仅支持职位详情页，请打开一个职位详情页再试。');
            return result;
        }
        const budget = { remaining: MAX_DIAGNOSTIC_NODES };
        const title = pickNode(doc, BossSelectors.TITLE);
        result.anchors.push(collectAnchorDiagnostic('title', title.selector, title.node, budget, true));
        const salary = pickNode(doc, BossSelectors.SALARY);
        result.anchors.push(collectAnchorDiagnostic('salary', salary.selector, salary.node, budget, true));
        const companyRoot = pickNode(doc, BossSelectors.DIAGNOSTIC_COMPANY_ROOT);
        if (companyRoot.node) {
            result.anchors.push(collectAnchorDiagnostic('company_root', companyRoot.selector, companyRoot.node, budget, false));
        }
        const detailRoots = pickNodes(doc, BossSelectors.DETAIL_ROOT);
        if (detailRoots.length) {
            for (const detailRoot of detailRoots) {
                result.anchors.push(collectAnchorDiagnostic('detail_root', detailRoot.selector, detailRoot.node, budget, false));
                if (budget.remaining <= 0)
                    break;
            }
        }
        else {
            result.anchors.push(collectAnchorDiagnostic('detail_root', null, null, budget, false));
        }
        if (budget.remaining <= 0) {
            result.truncated = true;
            result.warnings.push('已达到诊断节点数量上限，结果已截断。');
        }
        if (!result.anchors.some((a) => a.found)) {
            result.warnings.push('没有找到任何可用的锚点（标题 / 薪资 / 详情容器均未命中）。');
        }
        return result;
    }
    /**
     * Read only one narrowly named control selector.  Values that may carry a
     * session/security token (`href`, `redirect-url`, `data-url`) never leave
     * the page; only their presence is reported.  The BOSS account's configured
     * greeting is not assumed from a button click and stays explicitly unknown.
     */
    function diagnoseApplicationControl(doc) {
        for (const selector of BossSelectors.APPLICATION_CONTROL) {
            let nodes = [];
            try {
                nodes = Array.prototype.slice.call(doc.querySelectorAll(selector));
            }
            catch {
                continue;
            }
            if (!nodes.length)
                continue;
            const states = nodes.map((node) => {
                const view = doc.defaultView;
                const rect = node.getBoundingClientRect();
                const style = view ? view.getComputedStyle(node) : null;
                const disabled = node.disabled === true
                    || node.getAttribute('aria-disabled') === 'true';
                const visible = rect.width > 0 && rect.height > 0
                    && style?.display !== 'none' && style?.visibility !== 'hidden';
                return { node, disabled, visible };
            });
            const visibleUsableCount = states.filter(({ disabled, visible }) => visible && !disabled).length;
            return {
                selector,
                count: nodes.length,
                visible_usable_count: visibleUsableCount,
                unique_visible_usable_control: visibleUsableCount === 1,
                controls: states.slice(0, 3).map(({ node, disabled, visible }) => {
                    const label = sanitizeSample(text(node).slice(0, 40));
                    const dataset = node.dataset || {};
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
                    };
                }),
                confirmed_message_text: null,
                blocker: 'BOSS 首次招呼语正文未在该控件中得到可核实证据；禁止据此执行。',
            };
        }
        return {
            selector: null,
            count: 0,
            visible_usable_count: 0,
            unique_visible_usable_control: false,
            controls: [],
            confirmed_message_text: null,
            blocker: '未找到唯一、可核实的立即沟通控件；禁止执行。',
        };
    }
    /** Resolve the one initial-contact control without reading its URL-bearing attributes. */
    /** Whether BOSS is saying this posting is closed.
     *
     * Reads only elements whose *entire* text is one of the closed markers, so a
     * job description mentioning the phrase cannot trigger it. Absence of the
     * apply control is deliberately not evidence: it is also missing while the
     * page loads, and a false positive here retires a job the user still wants.
     */
    function postingClosed(doc) {
        const markers = BossSelectors.CLOSED_POSTING_TEXT;
        const nodes = Array.from(doc.querySelectorAll('span,div,p,em,b,strong,h1,h2'));
        return nodes.some((node) => {
            if (node.children.length)
                return false; // leaf nodes only
            return markers.some((marker) => text(node) === marker);
        });
    }
    function applicationControl(doc) {
        const all = [];
        for (const selector of BossSelectors.APPLICATION_CONTROL) {
            try {
                all.push(...Array.from(doc.querySelectorAll(selector)));
            }
            catch { /* fail below */ }
        }
        if (!all.length)
            return { ok: false, status: 'control_missing' };
        const visible = all.filter((node) => salaryRendered(node));
        if (visible.length !== 1)
            return { ok: false, status: 'control_ambiguous' };
        const node = visible[0];
        if (node.disabled === true || node.getAttribute('aria-disabled') === 'true') {
            return { ok: false, status: 'control_disabled' };
        }
        // M6 is the first application/greeting only. An existing-friend/ongoing
        // chat control is a different account action and must never be clicked.
        if (text(node) !== '立即沟通' || node.dataset.isfriend !== 'false') {
            return { ok: false, status: 'control_wrong_state' };
        }
        return { ok: true, node };
    }
    /** Pure-read, exact-identity M6 preflight. */
    function preflightConfirmedApplication(doc, currentUrl, expected) {
        if (looksLikeLoginRequired(doc, currentUrl))
            return { status: 'login_required' };
        if (looksLikeVerification(doc, currentUrl))
            return { status: 'verification' };
        if (detectPageType(doc, currentUrl) !== 'detail')
            return { status: 'wrong_page' };
        const observedUrl = cleanUrl(currentUrl);
        const observedExternalId = externalIdOf(observedUrl);
        if (!observedUrl || observedUrl !== expected.canonical_url
            || observedExternalId !== expected.external_id)
            return { status: 'identity_mismatch' };
        const observedTitle = pick(doc, BossSelectors.TITLE).value;
        const observedCompany = cleanDetailCompany(pick(doc, BossSelectors.COMPANY)).value;
        if (observedTitle !== expected.title || observedCompany !== expected.company) {
            return { status: 'identity_mismatch' };
        }
        // Checked before the control, so a closed posting is reported as closed
        // rather than as a missing button - the two need different handling and
        // only one of them is a reason to retire the job.
        if (postingClosed(doc))
            return { status: 'posting_closed' };
        const control = applicationControl(doc);
        if (!control.ok)
            return { status: control.status };
        return { status: 'ok', observed_url: observedUrl, observed_external_id: observedExternalId };
    }
    /** Resolve the chat composer BOSS opens after 立即沟通.
     *
     * Fail-closed at every step, because the failure mode here is a message sent
     * to a real person rather than a field left blank:
     *
     * - exactly one visible textarea, and exactly one send control beside it;
     * - **the textarea must already be empty.** BOSS sometimes sends its own
     *   greeting on 立即沟通 (observed once) and sometimes does not (observed
     *   once). Typing into a box that already has something in it would append a
     *   second message to whatever is there;
     * - both must sit in the same container, so a composer from some other panel
     *   on the page cannot be paired with this one's send button.
     */
    function greetingComposer(doc) {
        const view = doc.defaultView;
        if (!view)
            return { status: 'no_composer' };
        const visible = (node) => {
            const rect = node.getBoundingClientRect();
            if (rect.width < 8 || rect.height < 8)
                return false;
            const style = view.getComputedStyle(node);
            return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
        };
        const inputs = Array.from(doc.querySelectorAll(BossSelectors.GREETING_INPUT.join(',')))
            .filter((node) => node instanceof view.HTMLTextAreaElement)
            .filter(visible)
            .filter((node) => !node.disabled && !node.readOnly);
        if (!inputs.length)
            return { status: 'no_composer' };
        if (inputs.length > 1)
            return { status: 'ambiguous_composer' };
        const input = inputs[0];
        if (input.value.trim())
            return { status: 'input_not_empty' };
        const wanted = BossSelectors.GREETING_SEND_TEXT;
        for (let scope = input.parentElement; scope; scope = scope.parentElement) {
            const matches = Array.from(scope.querySelectorAll(BossSelectors.GREETING_SEND.join(',')))
                .filter(visible)
                .filter((node) => wanted.some((label) => text(node) === label));
            // A wrapper and the element inside it both read as 发送. That is nesting,
            // not ambiguity: keep only the innermost, which is the thing a person
            // clicks. Two *unrelated* controls still fail closed below.
            const controls = matches.filter((node) => !matches.some((other) => other !== node && node.contains(other)));
            if (controls.length === 1) {
                return { status: 'ok', input, send: controls[0] };
            }
            // More than one match at this level is ambiguous; widening the scope
            // would only add more. Stop rather than pick.
            if (controls.length > 1)
                return { status: 'no_send_control' };
        }
        return { status: 'no_send_control' };
    }
    /** Why the composer could not be resolved, as page *shape* only.
     *
     * Three live runs failed three different ways with the same code, so the
     * remaining question is what these pages actually contain - and answering it
     * by guessing has already cost several attempts. This reports counts, tag
     * names and class names: enough to tell an iframe from a contenteditable
     * from a differently-labelled send control, and nothing else. No text
     * content, no URLs, no attribute values beyond the class list - a chat panel
     * is full of a real person's messages and none of that may leave the page.
     */
    function greetingDiagnostic(doc) {
        const view = doc.defaultView;
        const visible = (node) => {
            const rect = node.getBoundingClientRect();
            return rect.width >= 8 && rect.height >= 8;
        };
        const textareas = Array.from(doc.querySelectorAll('textarea'));
        const editable = Array.from(doc.querySelectorAll('[contenteditable="true"]'));
        // Origins only: an iframe's full URL can carry a session token.
        const frames = Array.from(doc.querySelectorAll('iframe')).map((frame) => {
            const src = frame.getAttribute('src') || '';
            try {
                return src ? new URL(src, doc.location.href).host : 'srcless';
            }
            catch {
                return 'bad';
            }
        });
        const wanted = BossSelectors.GREETING_SEND_TEXT;
        const sends = Array.from(doc.querySelectorAll('button,div,span,a'))
            .filter((node) => wanted.some((label) => text(node) === label))
            .map((node) => {
            const cls = (node.getAttribute('class') || '').split(/\s+/).filter(Boolean)[0] || '-';
            return `${node.tagName.toLowerCase()}.${cls}${visible(node) ? '' : '!hidden'}`;
        });
        const parts = [
            `ta=${textareas.filter(visible).length}/${textareas.length}`,
            `ce=${editable.filter(visible).length}/${editable.length}`,
            `ifr=${frames.length ? Array.from(new Set(frames)).join('+') : '0'}`,
            `snd=${sends.length ? Array.from(new Set(sends)).slice(0, 3).join('+') : 'none'}`,
            `vw=${view ? view.innerWidth : '?'}`,
            chatHeaderShape(doc),
        ];
        return parts.join('|').slice(0, 240);
    }
    /**
     * The structural shape of the chat page's job header, for a failure note.
     *
     * `/web/geek/chat` was never captured, and the first attempt at reading it
     * assumed 查看职位 was an anchor to `/job_detail/<id>.html`. It is not:
     * the live page reported zero such links (2026-09-06). Rather than guess
     * again, a refusal records what the page actually contains, so the next
     * selector is written from structure instead of from a screenshot.
     *
     * Tag names and the first class only - never text content, which is a
     * recruiter's name or a message body, and never an href, which carries
     * session tokens.
     */
    function chatHeaderShape(doc) {
        const tag = (node) => {
            if (!node)
                return '-';
            const cls = (node.getAttribute('class') || '').split(/\s+/).filter(Boolean)[0] || '-';
            return `${node.tagName.toLowerCase()}.${cls}`;
        };
        const links = doc.querySelectorAll('a[href*="/job_detail/"]').length;
        // 查看职位 is the one label the header is known to carry; find it, and
        // describe the box it lives in rather than the words next to it.
        const label = Array.from(doc.querySelectorAll('a,span,div,button'))
            .filter((node) => text(node) === '查看职位')
            .filter((node) => !node.querySelector('a,span,div,button'))[0] || null;
        const chain = [];
        let node = label;
        for (; node && chain.length < 4; node = node.parentElement) {
            chain.push(tag(node));
        }
        const kids = label && label.parentElement
            ? Array.from(label.parentElement.children).map(tag).slice(0, 6)
            : [];
        return [
            `jdl=${links}`,
            `see=${chain.length ? chain.join('<') : 'none'}`,
            `row=${kids.length ? kids.join('+') : 'none'}`,
        ].join('|');
    }
    /** Type the confirmed greeting into an empty composer and send it once.
     *
     * The one M6 page mutation beyond the application click itself, and it is
     * separate from it: BOSS turned out not to always send a greeting with
     * 立即沟通, so this is its own authorized action (2026-09-03) rather than
     * "the other half" of one.
     */
    /** The conversation page BOSS navigates to when it does not open its
     *  in-page panel. Path only - the query carries session tokens. */
    function isChatPage(currentUrl) {
        try {
            const url = new URL(currentUrl);
            return url.origin === 'https://www.zhipin.com'
                && url.pathname.startsWith('/web/geek/chat');
        }
        catch {
            return false;
        }
    }
    /**
     * Whether the conversation BOSS opened is the one this approval names.
     *
     * The first attempt read `/job_detail/<id>.html` links, because an id is
     * what an approval binds. The live page has **none** - checked read-only on
     * 2026-09-06: zero such anchors anywhere, and the forty conversation-list
     * items are plain `div`s, not links. So the only identity the page exposes
     * is the header's own text, which is what the user authorized checking.
     *
     * Scoped to `CHAT_CONVERSATION`, and that scope is the whole safety
     * argument: the left list holds every other recruiter this account has
     * spoken to, and a document-wide text match would happily confirm a job we
     * applied to yesterday while BOSS had a different conversation open. One
     * pane, one conversation.
     *
     * Both the company and the title must be found. Either alone is not an
     * identification - several roles at one company is normal, and the same
     * title at two companies is normal too.
     */
    function chatConversationMatches(doc, expectedCompany, expectedTitle) {
        if (!expectedCompany || !expectedTitle)
            return { status: 'chat_job_unknown' };
        const panes = pickAll(doc, BossSelectors.CHAT_CONVERSATION);
        if (panes.nodes.length > 1)
            return { status: 'chat_job_ambiguous' };
        const pane = panes.nodes[0];
        if (!pane)
            return { status: 'chat_job_unknown' };
        // Leaves alone are not enough: BOSS splits a title like
        // 「云迁移运维工程师＋3个月（朝阳区MQ）」 across spans, so no single leaf
        // holds it and the whole approval was refused as `chat_wrong_job`
        // (observed 2026-09-07). A wrapper's `textContent` is every descendant
        // concatenated, which is why the pane itself must not qualify - so a
        // candidate is capped at a little longer than what is being looked for.
        // That admits a header row and excludes the conversation.
        const values = Array.from(pane.querySelectorAll('*'))
            .map((node) => text(node))
            .filter(Boolean);
        const found = (wanted) => values.some((value) => {
            if (value.length > wanted.length + 40)
                return false;
            if (value === wanted)
                return true;
            // BOSS renders 「公司 | 招聘者职位」 and 「职位 25-40K 北京」 as single
            // nodes, so a prefix counts - but only when what follows is a separator,
            // a space or a digit. 「云运维工程师(高级)」 is a different job and the
            // '(' stops it matching 「云运维工程师」.
            if (!value.startsWith(wanted))
                return false;
            const rest = value.slice(wanted.length);
            return /^[\s·•|｜/\-–—]/.test(rest) || /^\d/.test(rest);
        });
        const company = found(expectedCompany);
        const title = found(expectedTitle);
        if (company && title)
            return { status: 'ok' };
        // Neither read means the header could not be read at all; one of the two
        // means this is a conversation about something else.
        return { status: company || title ? 'chat_wrong_job' : 'chat_job_unknown' };
    }
    /**
     * Types and sends the one human-confirmed greeting - and only into the
     * approved job's own detail page.
     *
     * Observed on 2026-09-06: BOSS answered 立即沟通 by navigating the whole
     * tab to `/web/geek/chat` instead of opening its usual in-page panel, on
     * four consecutive applications. There the composer resolves perfectly well
     * - it just belongs to whichever conversation BOSS happened to select,
     * which is a message to a real person and not necessarily the right one.
     *
     * So the greeting goes to one of exactly two places (user authorized the
     * second on 2026-09-06): the job's detail page, checked by the URL's own
     * id, or the conversation BOSS itself opened for that job, checked by the
     * open pane's company and title - see `chatConversationMatches`, and note
     * the live chat page exposes no job id at all. Anywhere else, and anything
     * ambiguous, is refused before a character is typed.
     */
    function sendConfirmedGreeting(doc, greeting, currentUrl, expectedExternalId, expectedCompany = '', expectedTitle = '') {
        const body = (greeting || '').trim();
        if (!body)
            return { status: 'empty_greeting' };
        if (!expectedExternalId)
            return { status: 'wrong_job' };
        // Resolved BEFORE the identity check, and deliberately so. A chat page
        // that has not finished rendering has no composer, no header and no
        // conversation - and reporting that as `chat_job_unknown` made the worker
        // give up at once, because only "not ready yet" statuses are waited out.
        // Observed 2026-09-07: three applications in a row skipped their greeting
        // against a page whose diagnostic read `ta=0/0|snd=none|see=none` - an
        // empty document, not the wrong conversation.
        //
        // Nothing is typed here; the identity check below still gates that.
        const composer = greetingComposer(doc);
        if (composer.status !== 'ok' || !composer.input || !composer.send) {
            return { status: composer.status };
        }
        const observedUrl = cleanUrl(currentUrl);
        const observedId = observedUrl ? externalIdOf(observedUrl) : null;
        if (observedId) {
            if (observedId !== expectedExternalId)
                return { status: 'wrong_job' };
        }
        else if (isChatPage(currentUrl)) {
            const owner = chatConversationMatches(doc, expectedCompany, expectedTitle);
            if (owner.status !== 'ok')
                return { status: owner.status };
        }
        else {
            return { status: 'left_job_page' };
        }
        const view = doc.defaultView;
        if (!view)
            return { status: 'no_composer' };
        // Assigning `.value` alone leaves the page's own state untouched, so the
        // send control stays disabled and nothing would go out. The native setter
        // plus an input event is what a framework-backed field actually listens to.
        const setter = Object.getOwnPropertyDescriptor(view.HTMLTextAreaElement.prototype, 'value')?.set;
        if (setter)
            setter.call(composer.input, body);
        else
            composer.input.value = body;
        composer.input.dispatchEvent(new view.Event('input', { bubbles: true }));
        composer.input.dispatchEvent(new view.Event('change', { bubbles: true }));
        // Re-read rather than trust the write: if the page rejected or rewrote it,
        // nothing is sent.
        if (composer.input.value.trim() !== body)
            return { status: 'input_rejected' };
        composer.send.click();
        return { status: 'sent' };
    }
    /** The only M6 page mutation: repeat preflight and perform exactly one click. */
    function executeConfirmedApplication(doc, currentUrl, expected) {
        const preflight = preflightConfirmedApplication(doc, currentUrl, expected);
        if (preflight.status !== 'ok')
            return preflight;
        const control = applicationControl(doc);
        if (!control.ok)
            return { status: control.status };
        control.node.click();
        return {
            status: 'clicked',
            observed_url: preflight.observed_url,
            observed_external_id: preflight.observed_external_id,
        };
    }
    // ------------------------------------------------------------------- api
    function detect(doc, url) {
        const result = {
            page_type: 'unsupported',
            url: cleanUrl(url) || '',
            verification: false,
            login_required: false,
            candidates: [],
            warnings: [],
            errors: [],
        };
        const removed = strippedParams(url);
        if (removed.length) {
            result.warnings.push(`已从 URL 中移除跟踪/安全参数：${removed.join('、')}。`);
        }
        if (!isSupportedHost(url)) {
            result.errors.push('当前页面不是 BOSS 直聘（www.zhipin.com），没有可检测的内容。');
            return result;
        }
        if (looksLikeVerification(doc, url)) {
            result.verification = true;
            result.warnings.push('BOSS 正在显示安全验证页面。请你自己在浏览器里完成验证 —— 本扩展不会、也不应该替你处理验证。');
        }
        if (looksLikeLoginRequired(doc, url)) {
            result.login_required = true;
            result.warnings.push('BOSS 登录状态已失效。请你自己在浏览器里完成登录，本扩展不会读取或填写账号、密码、短信码。');
        }
        try {
            result.page_type = detectPageType(doc, url);
            if (result.page_type === 'detail') {
                result.candidates = [extractDetail(doc, url)];
            }
            else if (result.page_type === 'search') {
                const search = extractSearch(doc);
                result.candidates = search.candidates;
                result.warnings = result.warnings.concat(search.warnings);
            }
            else {
                result.errors.push('这是 BOSS 的页面，但看不出是搜索结果还是职位详情。请打开一个职位详情页或搜索结果页再试。');
            }
        }
        catch (err) {
            result.errors.push('读取页面时出错：' + String(err?.message || err));
        }
        return result;
    }
    // ---------------------------------------------------------------------
    // The results-page salary filter, read (never clicked)
    // ---------------------------------------------------------------------
    /**
     * BOSS's own salary bands and the codes behind them.
     *
     * Why read them at all: a repeated search of one keyword returns the same
     * top-of-list, so the only way to reach postings underneath is to narrow the
     * query - and BOSS's filter codes are opaque (`salary=406`). This project
     * refuses to ship a guessed table of them (`boss_search_filters.py` says so
     * at length), which left the human pasting one filtered URL per band by
     * hand. So: read the bands BOSS itself renders, with their codes, and let
     * the human confirm what was read.
     *
     * Strictly read-only. It opens no menu, clicks nothing and changes nothing -
     * if the options are not in the DOM the human opens the menu themselves and
     * presses the button again. A band with no code is reported as a band with
     * no code, never paired with a neighbour's.
     */
    /** Reads 薪资待遇's bands - kept as its own name because the console, the
     *  worker and the tests all address it by this one. */
    function readSalaryFilterOptions(doc) {
        return readFilterOptions(doc, 'salary');
    }
    /** The same read, on the 经验 menu. */
    function readExperienceFilterOptions(doc) {
        return readFilterOptions(doc, 'experience');
    }
    function readFilterOptions(doc, kind) {
        const spec = FILTER_MENUS[kind];
        const empty = (reason) => ({ options: [], labels_without_code: 0, reason });
        if (detectPageType(doc, doc.location?.href || '') !== 'search')
            return empty('not_a_search_page');
        // The menu's root: the smallest subtree that both carries the label and
        // contains several bands. Walking up from the label rather than down from
        // a guessed container is what keeps this off the wrong menu.
        let root = null;
        for (const label of spec.labels) {
            const anchors = Array.from(doc.querySelectorAll('*')).filter((el) => (el.textContent || '').trim().startsWith(label) && el.children.length <= 3);
            for (const anchor of anchors) {
                let node = anchor;
                for (let up = 0; up < 5 && node; up += 1) {
                    if (bandsIn(node, spec.band).length >= 3) {
                        root = node;
                        break;
                    }
                    node = node.parentElement;
                }
                if (root)
                    break;
            }
            if (root)
                break;
        }
        if (!root)
            return empty('menu_not_found');
        const options = [];
        let labelsWithoutCode = 0;
        const seen = new Set();
        for (const el of bandsIn(root, spec.band)) {
            const text = ownText(el);
            if (seen.has(text))
                continue;
            seen.add(text);
            const code = codeOf(el, spec);
            if (code)
                options.push({ label: text, code });
            else
                labelsWithoutCode += 1;
            if (options.length >= 30)
                break;
        }
        return {
            options,
            labels_without_code: labelsWithoutCode,
            reason: options.length >= 2 ? null : 'codes_not_found',
        };
    }
    const FILTER_MENUS = {
        salary: {
            labels: BossSelectors.FILTER_SALARY_LABEL,
            band: BossSelectors.FILTER_SALARY_BAND_RE,
            ka: BossSelectors.FILTER_SALARY_CODE_KA_RE,
            href: BossSelectors.FILTER_CODE_HREF_RE,
        },
        experience: {
            labels: BossSelectors.FILTER_EXPERIENCE_LABEL,
            band: BossSelectors.FILTER_EXPERIENCE_BAND_RE,
            ka: BossSelectors.FILTER_EXPERIENCE_CODE_KA_RE,
            href: BossSelectors.FILTER_EXPERIENCE_CODE_HREF_RE,
        },
    };
    /** An element's OWN text, ignoring descendants.
     *
     *  A band is `<li> 1-3年<i class="ui-icon-check"></i></li>`, so it is not a
     *  childless node and `textContent` on its `<ul>` would concatenate every
     *  band into one string. Reading only the direct text nodes gets 「1-3年」
     *  from the `li` and an empty string from the `ul`. */
    function ownText(el) {
        let out = '';
        for (const node of Array.from(el.childNodes)) {
            if (node.nodeType === 3)
                out += node.nodeValue || '';
        }
        return out.trim();
    }
    function bandsIn(root, band) {
        return Array.from(root.querySelectorAll('*')).filter((el) => band.test(ownText(el)));
    }
    /** The option's own code: BOSS's query parameter first, then a numeric
     *  attribute on the option or its immediate parent. Never a sibling's. */
    function codeOf(el, spec) {
        for (const node of [el, el.parentElement].filter(Boolean)) {
            // BOSS's own tracking attribute is where the code actually lives.
            const ka = spec.ka.exec(node.getAttribute('ka') || '');
            if (ka)
                return ka[1];
            const match = spec.href.exec(node.getAttribute('href') || '');
            if (match)
                return match[1];
            for (const attr of Array.from(node.attributes)) {
                if (attr.name === 'href' || !/^(data-|value$)/.test(attr.name))
                    continue;
                if (/^\d{1,12}$/.test(attr.value))
                    return attr.value;
            }
        }
        return null;
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
        greetingComposer,
        postingClosed,
        greetingDiagnostic,
        sendConfirmedGreeting,
        readSalaryFilterOptions,
        readExperienceFilterOptions,
        MAX_DESCRIPTION_CHARS,
        MAX_CARDS,
        MAX_DIAGNOSTIC_NODES,
    };
})();
