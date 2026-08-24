# JobAgent BOSS Detector（Chrome 扩展 · POC）

一个 **Manifest V3** 扩展，用来验证一件事：

> 在你**平时用的、已经登录的 Chrome** 里，能不能读取你**已经打开**的
> BOSS 直聘页面，把结构化岗位信息交给本机 JobAgent？

这一阶段**只是可行性验证**。在你自己实测之前，不要认为它对线上 BOSS 一定有效
—— 见文末「还需要真实验证的部分」。

---

## 它做什么

```
你自己打开 BOSS 页面（搜索结果 或 职位详情）
        ↓  你点扩展图标 → 点「检测当前页面」
读取当前 DOM，识别页面类型，抽取可见字段
        ↓  你点「发送到 JobAgent 预览」
POST 127.0.0.1:8000/api/extension/jobs/preview  → 新岗位 / 已存在 / 信息不足
        ↓  你对某个岗位点「导入」
POST 127.0.0.1:8000/api/extension/jobs/import   → 走 job_intake，和粘贴 JD 完全同一条路
```

## 它明确**不**做什么

- 不自动搜索、不翻页、不滚动、不点击任何职位；
- 不投递、不打招呼、不发消息；
- 不读取 Cookie、localStorage、sessionStorage、表单值、密码或任何认证信息；
- 不做验证码识别、不做反检测、不改指纹、不用 Playwright / CDP；
- 不上传整页 HTML —— 只发送 `selectors.ts` 里点名的那些字段；
- 检测之外不做任何事：没有定时器、没有 MutationObserver、没有后台轮询。
  只有你点「检测当前页面」时才会读一次页面。

遇到 BOSS 的安全验证页面时，它会**告诉你**并停下，请你自己在浏览器里完成验证。

---

## 目录

```
extension/
  manifest.json          MV3 清单
  popup.html / popup.css 弹窗界面
  src/
    boss/selectors.ts    ★ 所有 BOSS 选择器都在这里，别处不许写
    boss/extract.ts      页面类型识别 + 字段抽取（纯 DOM 函数）
    content.ts           内容脚本：收到消息才干活
    popup.ts             弹窗逻辑 + 调用本地后端
    config.ts            后端地址（仅 127.0.0.1）
    chrome.d.ts          手写的 chrome API 类型（只声明用得到的那几个）
  dist/                  tsc 编译输出（Chrome 实际加载的就是它）
  tests/fixtures/        手写的仿真 HTML，用于离线测试
```

选择器全部集中在 `src/boss/selectors.ts`，并且每个字段都是**多个候选依次尝试**，
不使用 `div > div:nth-child(3) > span` 这类生成式长路径 —— 那种选择器一改版就废，
而且失效时什么信息都给不了你。

---

## 构建

```bash
cd extension
```

```bash
npm install
```

```bash
npm run build
```

只有一个开发依赖（TypeScript）。`dist/` 是**提交进仓库的**，因为 Chrome 直接加载
`extension/`，测试也会注入编译产物。

---

## 手动安装与验证步骤

后端需要先跑起来（在仓库根目录）：

```bash
.\scripts\dev.ps1 backend
```

然后：

1. 打开 `chrome://extensions`
2. 右上角打开「**开发者模式 / Developer mode**」
3. 点「**加载已解压的扩展程序 / Load unpacked**」，选择 `F:\jobagent\bossagent1.0\extension`
4. **使用你平时用的、已经登录的 Chrome**（不需要新建配置文件，不需要重新登录）
5. 在这个 Chrome 里**手动打开**一个 BOSS 页面：
   - 搜索结果页，例如 `https://www.zhipin.com/web/geek/job?query=云计算`
   - 或某个职位详情页 `https://www.zhipin.com/job_detail/xxxxx.html`
6. 点浏览器工具栏上的扩展图标
7. 点「**检测当前页面**」
8. 记录**哪些字段成功抽取到了**：页面类型、URL、岗位数量，以及每个岗位的
   职位名称 / 公司 / 薪资 / 城市 / 经验 / 学历 / 职位链接 / 职位描述

把第 8 步的结果反馈回来，就能知道选择器需要怎么调整。

> 修改代码后：重新 `npm run build`，然后在 `chrome://extensions` 里点该扩展的
> **刷新**按钮，再**刷新 BOSS 页面**（内容脚本是在页面加载时注入的）。

### 开发者模式

弹窗里的「开发者模式」勾选框会显示**每个字段是被哪个选择器命中的**，
没命中的字段会显示「没有任何候选选择器命中」。

它只显示选择器字符串本身，不显示任何页面内容、账号信息或令牌。
字段抽取失败时，这是最快的定位手段。

### 结构诊断（开发者模式 · 仅职位详情页）

公司 / 城市 / 经验 / 学历 / 职位描述这几个字段，只有在真实页面上选择器还能命中时
才是可靠的。勾选「开发者模式」后，在职位详情页点「结构诊断」会读取标题 / 薪资 /
多个已知详情容器这些**已经可信**的锚点附近的一小圈 DOM：每个节点只有标签名、class 列表
和一段很短的、脱敏过的文本样本（超过一定大小的容器不给样本，只给结构）。

- 不读取 id、href、data-\*、`aria-*` 或任何其他属性；
- 不会向上爬到 nav / header / footer / 聊天 / 账号相关的区域；
- 样本里的邮箱、手机号、长数字串、链接都会被替换成占位符；
- 什么都不会发送到后端或任何地方 —— 结果只显示在弹窗里，点「复制诊断结果」
  手动复制到剪贴板即可用来调整 `selectors.ts`。

同样是一次点击、一次读取，没有定时器，没有轮询。

---

## 与本地 JobAgent 的集成

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/extension/jobs/preview` | 返回 检测数 / 新岗位 / 已存在 / 信息不足 + 警告。**不保存任何东西** |
| POST | `/api/extension/jobs/import` | 保存一个岗位，必须 `confirmed=true`。走 `job_intake` |

两个接口都**只接受本机（loopback）请求**。后端本来就只绑定 `127.0.0.1`，
这个检查让它成为一条断言而不是一个假设。

导入复用 `services/job_intake.save_posting`：规范化 → 内容哈希 → 去重 → `Job` +
`ApplicationEvent`。**没有第二条落库路径** —— 扩展导入的岗位在下游和手动粘贴的
岗位完全没有区别。

URL 在两侧各清洗一次：扩展只返回 `scheme + host + path`，后端再调用
`canonical_url()`。BOSS 会在查询串里放 `lid` / `securityId` 这类会话令牌，
它们不会进入数据库，也不会出现在日志里。

---

## 测试

抽取逻辑的测试在后端测试套件里（`backend/tests/test_extension_extraction.py`），
用**无头 Chromium** 加载 `extension/tests/fixtures/` 里的本地 HTML，注入**真正编译
出来的** `dist/boss/*.js`，调用真实的 `BossExtract.detect()`。

也就是说，测试跑的是实际发布的那份代码，而不是一个 Python 复刻版 ——
不存在两份会各自漂移的抽取器。

```bash
cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_extension_extraction.py tests/test_extension_api.py
```

固件覆盖：搜索结果页、职位详情页、无薪资的详情页、不支持的页面。
**任何自动化测试都不会访问 zhipin.com。**

没有先执行 `npm run build` 时，抽取测试会**跳过**（skip）并说明原因，
与本仓库对待「Playwright 浏览器未安装」的做法一致。

---

## 还需要真实验证的部分

固件是照着 BOSS 的**结构惯例**手写的，不是抓下来的真实 DOM。所以测试通过只能说明
抽取器能处理这些形状，**不能说明它对线上 BOSS 有效**。以下必须由你实测：

- 真实页面的 class 名是否仍然匹配 `selectors.ts` 里的候选（BOSS 会改版）；
- 职位描述是否在首屏就渲染好，还是需要滚动或点击「展开」才出现
  —— 本扩展不滚动也不点击，所以后者会抽不到；
- 搜索结果是否为虚拟列表（只渲染可视区域的若干张卡片）；
- 详情页是不是同一个 SPA 路由内切换的 —— 那样内容脚本不会重新注入，
  可能需要刷新页面；
- 登录态、地区、A/B 实验会不会让 DOM 结构不同；
- BOSS 是否会因为扩展的存在而改变行为（不应该，因为没有任何自动化行为，
  但只有实测能确认）。

在你完成上面的手动步骤并反馈结果之前，**不要把这个 POC 当成可用功能**。

---

## M4a：受监督会话脚手架（无翻页/滚动/点击）

在 CLAUDE.md「Chrome extension - M4 supervised navigation policy」明确授权之后，
M4a 只实现了**会话本身**的脚手架，不实现任何页面操作：

```
弹窗：人工选任务、设置不超过上限的页数/候选人/滚动次数、确认当前标签页是
      https://www.zhipin.com  →  POST /api/extension/sessions
      →  立即给该标签页发一条 chrome.runtime 消息，覆盖条立刻出现（无需刷新）

approved 标签页：固定覆盖条（内容脚本 overlay.ts）显示任务名 + 上限 + 进度
      （M4a 进度恒为 0，因为没有任何翻页/滚动发生）
      →  点击「停止会话」→ POST .../stop → 覆盖条立刻消失

停止/失效路径全部 fail closed：浏览器重启、标签页不匹配、后端会话已停止 —
      不会静默恢复，只会清除本地指针并（如需要）把后端会话标记为 stale_tab
```

**架构要点**（`extension/src/session.ts` 弹窗 / `background.ts` 服务工作线程 /
`overlay.ts` 内容脚本）：

- `chrome.storage.session`（本扩展自己的会话级存储，浏览器重启即清空，
  绝不是网站的 cookie/localStorage/sessionStorage）只有弹窗和 service worker
  能直接读写 —— 这是 Chrome 默认的访问级别，本扩展**从不**调用
  `setAccessLevel` 去放宽它；
- 覆盖条（内容脚本，运行在网站页面里）**不能**直接碰 `storage.session`，
  只能通过两条极窄的 `chrome.runtime` 消息（`jobagent:get-pointer` /
  `jobagent:clear-pointer`）问 service worker 要「属于我这个标签页」的指针 ——
  service worker 会先核对 `sender.tab.id`，绝不把指针泄露给别的标签页；
- 开始/停止时弹窗会直接给已确认的标签页发 `jobagent:session-started` /
  `jobagent:session-stopped` 消息，覆盖条立即出现/消失，不依赖刷新页面；
  刷新页面时覆盖条重新运行 `overlay.ts`，会重新问一遍 service worker，
  结果一致，所以刷新也能正确恢复；
- 没有本地指针、但后端仍报告有会话在跑（典型场景：浏览器重启导致
  `storage.session` 被清空）—— 这种「僵尸会话」会被覆盖条检测到并调用
  停止接口，reason 记为 `stale_tab`，不会无限挂着；
- 全程没有计时器、没有轮询、没有 `MutationObserver`；没有
  `chrome.tabs.update` / `chrome.tabs.create` / `chrome.webNavigation` /
  `chrome.debugger`；不点击、不滚动、不翻页、不投递、不发消息。

测试：

```bash
cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_extension_m4a_contract.py
```

70 项，全部是对 `src/*.ts` 源码和（已构建时）`dist/*.js` 产物的静态断言 ——
manifest 接线、覆盖条不碰 `storage.session`、service worker 独占指针读写、
弹窗即时推送、任务名/上限/进度展示、缺指针+后端仍在跑时的 fail-closed 停止，
以及禁止出现的导航/滚动/计时器/观察者类 API。**不涉及真实浏览器，不涉及
zhipin.com。**

**还没有验证的部分**：本 README 之前「还需要真实验证的部分」列出的抓取风险
之外，M4a 本身还需要你在真实、已登录的 Chrome 里手动确认：开始会话后覆盖条
是否真的出现在正确的标签页上、切换/关闭标签页后是否符合预期地不出现在别的
标签页、刷新页面后是否正确恢复、以及重启浏览器后是否真的不会残留任何仍在
「运行」的后端会话。这些手动步骤完成之前，不要认为 M4a 在真实 Chrome 里可用。

---

## M4b：受限导航（人工逐次点击「下一位候选人」）

在 CLAUDE.md「Chrome extension - M4 supervised navigation policy」明确授权之后，
M4b 在 M4a 的会话之上加了**一步导航**：覆盖条上的「下一位候选人」按钮。

```
approved 标签页处于搜索结果页  →  人工点击「下一位候选人」
      →  覆盖条重新读取当前页面（验证/风控提示、页面类型、剩余未打开的候选人）
      →  任何策略性硬停条件命中：立即结束会话，什么都不点
      →  否则先问 background.ts「prepare」（只授权，不写库） →  再点开一张
         已经渲染好的候选人卡片链接  →  再向 background.ts「confirm」真实点击
         结果（成功才计数，失败会被审计但不计数）
```

同一次点击只会做这一件事：每次「下一位候选人」都是人工发起的独立一步，没有
自动连续点击、没有定时器、没有轮询。快速连点两下也只会真正执行一次——覆盖条
里有一个在任何 `await` 之前就同步生效的进行中标记（同时会临时禁用按钮），
`background.ts` 里还有一层独立的、按标签页隔离的内存态互斥锁包住那一次真正的
`navigate/prepare` 请求，两层都不依赖计时器，也不会扩大 `chrome.storage` 的
访问范围。

**M4b 明确不做的事**：不滚动、不翻页、不搜索；不投递、不打招呼（立即沟通）、
不发消息、不关注/收藏；不读取 Cookie / localStorage / sessionStorage / 表单值 /
认证信息。遇到验证码、身份/手机验证、登录提示或风控提示会立即结束会话，
不会重试、不会绕过。

测试：`node --test extension/tests/m4b-behavior.test.cjs
extension/tests/m4b-overlay-hardstop.test.cjs
extension/tests/m4b-doubleclick-guard.test.cjs`，以及后端的
`test_supervised_sessions.py`。全部是对已构建产物 / mock 消息通道的断言，
**不涉及真实浏览器，不涉及 zhipin.com**。

**还没有验证的部分**：和 M4a 一样，M4b 目前只有自动化测试跑过；它在真实、
已登录 Chrome 里对线上 BOSS 的兼容性**尚未验证**——选择器是否仍能命中、
候选人卡片是否真的可以只点一次就打开、"下一位候选人"多次点击后的行为是否
符合预期，都需要你自己手动确认。这些手动步骤完成之前，不要认为 M4b 在真实
Chrome 里可用。M4c（滚动/翻页）仍未实现，且需要一次独立于 M4b 的、新的
明确授权才能开始编写。
