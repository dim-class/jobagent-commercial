# JobAgent Orchestration Status

- Current (2026-09-01): P3A 综合搜索改造完成并通过**实际渲染验收**（此前只跑了 focused 测试）。
  入口从「先选方向、再管理 4 个串行任务」收敛为一个「按简历与职业方向综合搜」的组合。
- 根因不是"只显示四个"：后端与扩展都把整批硬限制为 5 个任务，两座城市时算法只分配两个方向，
  于是恰好生成四项；每方向默认又只有 3 个候选，覆盖自然很窄。
- 改动：`CLAUDE.md` 按仓库既有格式新增 **P3A 修正案**（用户 2026-09-01 授权），
  **只 supersede 原五任务上限**，改为最多 16 个 `pending` 任务、覆盖最多 8 个简历方向，
  并允许主界面不暴露内部执行顺序与任务 id。**每任务 20 候选 / 5 滚动的硬上限原样保留**，
  全部任务仍逐个串行执行、失败即停。
- 组合算法：`limit = min(8, max(1, 16 // 城市数))`。1–2 城市 → 8 个方向；4 城市 → 各 4 个；
  总单元数恒 ≤ 16。方向池沿用既有的中文优先、去重保序排序。
- **三层上限一致性已逐层核对**（不一致会让后端造 16 个而桥接放行 5 个）：
  后端 `search_plan.MAX_BATCH_TASKS` = 16、前端 `consoleExtension.MAX_CONSOLE_BATCH_TASKS` = 16、
  MV3 worker `BATCH_MAX_TASKS` = 16，且 `extension/dist` 已同步该常量。
- 浏览器实际验收（只访问 localhost，未操作 Chrome/BOSS、未启动真实任务、未调用 AI）：
  控制台主界面只剩三个按钮，**不含任务编号、串行顺序措辞或逐任务控制**；确认弹窗改为汇总式
  （城市 / 岗位方向 / 单元总数 / 每单元上限）；七个路由全部正常渲染，**零控制台错误**。
- 本轮补上一处 Codex 未覆盖的问题：这次改动把默认浏览工作量从 5×3=15 次候选尝试提到
  16×8=**128 次**（硬上限 16×20=320 次），而确认弹窗只显示单元数与每单元上限，没有总量。
  已补「最多 N 次候选尝试……会持续较长时间，中途可随时暂停或取消」，让用户在确认前看到真实规模。
- 验收：完整后端 pytest 退出码 0；扩展 build + 302/302（含 dist 漂移守卫）；前端 build + 32/32。

- Current (2026-09-01): P2C-A3 **Chrome Web Store 提交候选**已完成本地实现与回归。新增
  `scripts/build-extension-store.py`，从开发 manifest 派生仅含 `activeTab` / `scripting` / `storage`
  以及 BOSS + loopback 8000 的商店 manifest，彻底移除 5173；ZIP 只含 17 个白名单运行文件，
  固定条目顺序/时间戳，并输出 SHA-256 与机器清单。两次独立构建字节一致，当前 0.1.20 候选哈希为
  `f551a91e53770218d43397674788aacb1a4a8cb84f0f87a1db4e40bd7e1c57ee`。发布运行时已删除此前暂停但
  仍编译存在的 M7 BOSS 聊天扫描器；历史后端 schema/API 未重新开放，商店包与“不扫描沟通”承诺一致。
  新增中性自有图标、隐私政策草案、权限/数据披露/商店文案和 artifact-only workflow。完整后端 pytest
  100%（既有 cache warning）、扩展 build + 301/301、前端 production build + 31/31、focused
  商店包/DOM 56/56 均 PASS。尚未上传或发布；仍需公开隐私 URL、无个人信息截图/宣传图、开发者账号
  与人工审核，不能声称已上架。
- Current (2026-09-01): P2C-A2 **可重复 Windows 安装生命周期发布门禁**已通过。远程 run
  `33474909477` 在一次性 `windows-2025` runner 上构建 `-a/-b` 两版，并实际完成当前用户安装、
  无 Python/Node runtime PATH 的冻结 EXE doctor/启动、health/database ok、前端 HTTP 200、受保护停止、
  同 AppId 升级、数据库哈希与 sentinel 保持、静默卸载保留默认数据、清理程序/注册项及释放端口；随后
  artifact 核验与上传成功。最终 `0.1.0-3-b` SHA-256 为
  `d3e1787980c852e9297b92b7a522f6c28742b347fc137fdc270b9a8e40b1c235`，下载后独立复算一致，仍明确
  `NotSigned` / `development_candidate` / `commercial_distribution_ready=false`。对应常规 CI
  `33474896134` 三个 job 全绿。runner 本身仍装有构建工具，因此不能冒充真正无工具链 VM 验收。
- Current (2026-09-01): P2C-A **未签名 Windows 当前用户级安装候选**已实现并通过本机隔离生命周期。
  固定 Inno Setup 7.1.0 + 稳定 AppId；安装不需管理员权限，含开始菜单/可选桌面快捷方式，升级/卸载
  只调用已安装 `JobAgent.exe --stop`。卸载默认保留 `%LOCALAPPDATA%\JobAgent`，静默卸载无条件保留；
  交互删除需独立警告并默认 No。p2ca1→p2ca2 实测：doctor、health/database ok、HTML 200、同 AppId
  升级、数据库哈希不变、静默卸载清理程序与注册项、保留数据并释放端口全部 PASS。真实用户数据未读写，
  Chrome/BOSS/AI 均未启动。产物 manifest 明确 `NotSigned`、`development_candidate`、
  `commercial_distribution_ready=false`。当前编译器显示 `Non-commercial use only`；签名、适用商业许可
  确认、干净无工具链 VM 与交互删除数据验收仍是 P2C-B 发布门槛，不能宣称正式商用发行。
- 首次 installer CI run `33451647195` 在 payload/portable 构建审计成功后失败：runner 的 PATH 仍指向
  预装 Inno 6，尽管前一步已安装固定 7.1.0。解析顺序已改为先找 7 的固定目录再退回 PATH，并新增
  顺序契约测试；修复后的 clean-checkout installer CI run `33451995662` 已全部 PASS（构建、文件核验、
  artifact 上传）。下载的 `0.1.0-2` 产物实际 SHA-256 与 manifest 完全一致：
  `398ab6bcfec4cddab3ba106ba878c63808d7b5284157a1395454ff659e48be3b`；清单确认 Inno 7.1.0、
  `NotSigned`、`development_candidate`、`commercial_distribution_ready=false`。P2C-A 远程构建门已通过，
  但这不替代签名、适用商业许可确认或干净 VM 验收。

- Current (2026-09-01): Windows portable 候选修复了一个**正确性 bug**（不只是体验问题），
  已在真实产物上验证。用户目前只在 Windows 使用，本轮按此优先级推进。
- **端口占用会把失败的启动伪装成成功的启动。** 启动器过去在 uvicorn 尝试绑定**之前**就已经
  写好 PID 记录、做完升级备份、并启动了浏览器轮询线程。绑定失败时进程以退出码 3 结束，但
  `_open_when_ready` 已经在轮询 `/health`——如果端口被**另一个 JobAgent** 占用，它会拿到那个
  进程的健康响应，于是：写下 `runtime-version.json` 确认一个自己从未提供过的版本、置位 `ready`
  （这会**抑制 `finally` 里的升级回滚**）、并把浏览器打开到那个进程的界面上。
  也就是说升级失败时备份不会被恢复，而用户看到的是一个"正常工作"的 JobAgent。
  P2B 验收当时出现的假阳性正是这个原因：健康的 `/health` 来自遗留的开发后端。
- 修复：新增 `portable._port_available()`，在写 PID、做备份、开浏览器**之前**探测端口。被占时打印
  端口号、两条解决办法（`--stop` 或 `--port`）、以及"该地址上的页面属于别的程序"，退出码 4，
  不留任何 PID 记录。`--doctor` 同步增加 `port_available` 检查（`cli._port_is_free`），
  端口被占时报 FAIL/退出码 1，而不再是 PASS/0。
- `SHA256SUMS.txt` 改为 LF 行尾（`[System.IO.File]::WriteAllText` + 无 BOM UTF8）。CRLF 时
  `sha256sum -c`（Git for Windows 自带、也是最常用的校验方式）会去找一个名字以回车结尾的文件并
  报 FAILED，即使哈希本身正确。
- 验收：测试先红后绿（`test_portable_runtime.py` 8/8，新增 3 个用例覆盖端口探测、启动拒绝、
  doctor 报错）；完整后端 pytest 退出码 0。重新构建 run 33415774563 全部步骤 success，
  下载产物用 `sha256sum -c` **直接校验通过**（`JobAgent-Windows-x64-0.1.0-4.zip: OK`）。
  用**打包后的真实 exe** 复测：端口被占时 `--doctor` FAIL/退出码 1、启动退出码 4、无 PID 残留。
- 一处**排除掉的伪缺陷**：exe 的中文提示在本会话的控制台捕获里显示为乱码。核对原始字节后确认
  它输出的是 **GBK**（`端` = B6CB），UTF-8 解码失败而 GBK 解码结构完整——在中文 Windows 控制台
  （代码页 936）会正常显示。乱码来自我的捕获管道按 UTF-8 解码，不是产品问题，未做改动。
- P2B/P2C 边界不变：仍是**未签名候选**；签名、安装/卸载 UX、无工具链干净 Windows 验收属 P2C。
  本轮未发布 Release、未签名、未控制 Chrome/BOSS、未调用 AI、未触碰用户数据。

- Current (2026-09-01): **P2B 远程验收 PASS**。`b5be113` 已推送到 `origin/commercial`，
  「Windows portable candidate」workflow 在 GitHub Actions 上完整跑通并产出可下载的未签名候选包。
  P2B 仍是**未签名候选**，不是最终商用安装器；签名、安装/卸载 UX 与无工具链干净 Windows 验收属 P2C。
- 首次远程构建（run 33412956626）**失败在 upload-artifact**，而构建本身是成功的：
  「Build and audit unsigned candidate」与「Verify artifact files」都通过，verify 步骤日志里列出了
  100,651,837 字节的 ZIP 和 SHA256SUMS.txt。原因是 `.artifacts` 以点开头，`actions/upload-artifact@v4`
  默认把点开头路径视为隐藏并跳过（verify 用的是 `Get-ChildItem -Force` 所以看得见）。
  修复提交 `a1e5612` 增加 `include-hidden-files: true`。
- 重跑（run 33413375056）**全部步骤 success**：clean checkout / backend packaging install /
  frontend + extension npm ci / PyInstaller build + release audit / Verify artifact files / upload-artifact。
- 产物核验：远程下载 `JobAgent-Windows-x64-0.1.0-3.zip`（100,651,775 字节），SHA256 与
  `SHA256SUMS.txt` 声明的 `a54220c5ca733ffb5d292ff72e920923d8e73286967d346eabe9f001c0fa5ebb`
  **逐字符一致**。发现一个小缺陷：`SHA256SUMS.txt` 是 CRLF 行尾，Linux/git-bash 的 `sha256sum -c`
  无法直接校验（需先转 LF）；未修，记录待 P2C 处理。
- 包内隐私核查：解压后仅 `runtime/config/career_strategy.yaml` 命中敏感文件名，比对确认它与仓库
  中性模板**去掉 CR 后逐字节相同**（`preferred_roles: []`），与用户个人策略不同。无数据库、无 .env、
  无简历、无 browser_profiles。`release_audit.py` 本身也会拒绝打包 data/uploads/browser_profiles/logs。
- 隔离目录运行验收：`--doctor` **PASS**（data_directory / data_directory_writable / strategy_file /
  database_parent / frontend_bundle 全 OK，OpenAI 未配置为 INFO），退出码 0。
- **一个假阳性被抓住**：首次启动 portable 时 8000 端口被上一轮遗留的开发后端占用，portable 绑定失败
  退出码 3，而此时 curl `/health` 返回的 200 来自**开发服务器**而非 portable。改用
  `--port 8123 --no-open --data-dir <临时目录>` 重跑，才是真实验收：`/health` 返回
  `status=ok` + `database=ok`，根页面 `HTTP 200`（470 字节）。
- `--stop` 验收：停止前 PID 25592 存在，执行后进程消失、`runtime-process.json` 被删除、
  端口 8123 释放、无残留 JobAgent.exe 进程，退出码 0。
- 用户数据未被触碰：运行前后 `data/jobagent.db` 与 `data/career_strategy.yaml` 的 size+mtime 指纹
  **完全一致**；portable 把库写在自己的 `--data-dir` 与 `%LOCALAPPDATA%\JobAgent`，测试产物已清理。
- 本轮未发布 GitHub Release、未签名、未控制 Chrome/BOSS、未调用任何 AI、未改动产品范围。

- Current (2026-09-01): “任何人可用”P2B **未签名 Windows x64 便携候选**已在本机完成真实产物
  验收。锁定 PyInstaller 6.22.2；ZIP 约 102 MB，含冻结 Python 后端、编译前端、迁移、中性策略
  和扩展 0.1.20。3222 文件审计：missing 0 / forbidden 0 / `direct_url.json` 0；ZIP SHA-256 与
  `SHA256SUMS.txt` 一致。解压后的 EXE doctor、HTML 200、health/database ok、隔离首次初始化、
  PID 身份停止均 PASS。成功升级生成 SQLite+策略备份；强制端口冲突退出 3 后，数据库证明行和
  策略均恢复、版本不推进、PID 清除。完整后端 pytest 100%/退出码 0。新增人工/tag Windows
  artifact workflow；远端干净 checkout 构建、代码签名、安装/卸载 UX、无工具链独立 VM 仍待验收。

- Current (2026-08-31): “任何人可用”P2A 商用运行基础已实现并离线验收。新增
  `JOBAGENT_DATA_DIR`/冻结运行时 `%LOCALAPPDATA%\JobAgent` 数据根，数据库、策略、简历、浏览器
  资料与 `.env` 可完全脱离源码；首次启动只在缺失时复制中性策略，不覆盖既有用户。FastAPI 可由
  `JOBAGENT_SERVE_FRONTEND=true` 直接提供已构建 React 前端，新增无敏感输出的
  `app.cli doctor`、`app.portable` 单进程入口与过渡性的 `Start-JobAgent-Commercial.cmd`。
  隔离端口/隔离数据目录生命周期实测：HTML 200、health/database ok、中性策略与新 DB 创建、仅停止
  自有 PID，PASS。商用前端改走 8000 后同步补齐 MV3 控制台桥接：仅精确允许
  `127.0.0.1/localhost × 5173/8000`，任意端口仍 fail closed。完整后端 1787 collected，退出码 0
  （30 个既有环境 skip）；扩展 build + 301/301；前端 build + 31/31；PowerShell AST PASS。未访问
  Chrome/BOSS、未启动任务、未调用 AI，个人
  `data/` 未读写。P2 尚未完成：仍缺包含 Python 的版本化签名 artifact、升级备份/回滚、扩展正式
  分发与无 Python/Node 干净 Windows 验收，当前不得宣称已有最终安装器。

- Current (2026-08-31): 商用分支 `commercial` 已推送到私有仓库
  https://github.com/dim-class/jobagent-commercial ，**CI 三个 job 全绿**
  （run 33400062616：backend / extension / frontend）。旧仓库 `Qirui-JobAgent` 已归档。
- 本轮按「先让自己用得舒服」的优先级做了三件事：
  1. **候选阶段策略作用到队列**（`c700c51`）。此前 P1 只在入库时生效，库里已有的 130 个岗位不受
     影响，`exclude` 策略下仍有 76 分的「2027届秋招」躺在投递队列里。现在队列按当前策略过滤：
     标记在构建 proposal 时用**完整 JD** 经同一分类器算好（不在过滤时用标题重算第二套），
     不动 `Job.status`，隐藏必须说出来（顶部提示 + 一键展开 + 行内「应届/校招」标签）。
     修掉一个自造的 bug：提示说隐藏 1 个而实际消失 2 个，因为计数只算 apply/strong_apply，
     漏了 `include_maybe` 打开后在队列里的 maybe 行。
  2. **搜索方向表现统计**（`3fc7269` + `a9e25b2`）。零 AI，复用 `statistics.py`（不写第二套置信度）。
     真实数据结论：云计算工程师 8/28 推荐率 29%（strong）、运维开发 4/24 17%（strong）、
     **基础设施 0/8、均分 31.5**（moderate，纯烧钱）。排序按「置信档优先，再 Wilson 下界」，
     所以 AWS 的 2/3 = 67% 被正确压到样本不足区、没冒充第一名。面板放在任务控制台关键词选择器下方。
  3. **CI + dist 漂移守卫**（`cc185f5` + `2fdd2e0`）。`dist-freshness.test.cjs` 把当前源码编译到
     临时目录与 `dist/` 逐字节比对；缺少编译器时**失败而非跳过**（那正是掩盖问题的场景）。
     红绿双向验证过：dist 最新时绿，改一行源码不重建即红并给出 `npm run build` 指令。
     workflow 在 push/PR 与每日定时运行三套测试。
- 首次 CI 失败是我流水线自身的问题（pytest 不会创建 `--basetemp` 的父目录，而 `.tmp/` 只是本地
  gitignore 的工作状态），已修并复跑至全绿；未把未验证的流水线当成可用。
- 本轮修复的既有缺陷：迁移测试的精确列集合断言与 `0021` 不同步；`test_analytics_api` 用冻结
  `NOW=2026-08-21` 造数据却调用真实时钟路由，在 8-31 当天开始失败（已锚定真实时钟）；
  `extension` 的 `npm test` 脚本在 Node 22+ 报错；`frontend` 根本没有 `test` 脚本，导致两个 M6
  测试无人执行；`extension/node_modules` 丢失 typescript 使 294 个测试跑在无法重建的 dist 上。
- 个人/商用分离保持不变：个人策略只在 gitignore 的 `data/career_strategy.yaml`；远程 434 个文件中
  敏感模式仅命中 0 字节的 `data/.gitkeep`。本地 `main` 分支与个人数据未被改动。
- 运行提醒：用户的后端**不带 `--reload`**，后端改动需重启才生效；本轮为验证重启过数次，
  当前以后台任务方式运行（PID 由 harness 管理）。
- 下一步（未开始，需用户确认优先级）：M6 真实点击仍未验证（`HUMAN_CONFIRMED_APPLY_ENABLED` 为
  False）；「基础设施」方向建议从策略中移除；给别人使用所需的多用户/认证/部署仍是另一轮设计。

- Current (2026-08-31): 商用基线已在本地分支 `commercial` 提交（`b7ed135`），**未推送**——
  仓库没有配置任何 git remote，按要求不猜测地址、不推送。`main` 分支未被改动。
- P1 候选阶段筛选经审查后**已完整**，未重复造轮子：`job_eligibility.evaluate_early_career_policy`
  提供 exclude/include/only；`search_plan` 给每个新 SearchPlan 任务快照 `early_career_policy`；
  `extension_intake.early_career_policy_for_task(db, task_id)` 让预览与单条导入按任务快照判定。
  已核实 `only` 不按标题预筛卡片（`background.ts` 仅在 `exclude` 下允许标题拒绝，注释说明
  详情页 JD 才是 `only` 的证据来源），符合 TASK.md 要求。未新增第二套 Job 持久化或去重管线。
- 运行测试时发现并修复三类既有缺陷（均非 P1 逻辑本身，但其中一类由 P1 引入）：
  (1) `0021_candidate_stage_policy` 给 `job_search_tasks` 增加了 `early_career_policy`，但
  `test_migrations_search_plan` / `_orchestration_events` / `_supervised_sessions` 三处的
  「精确新增列集合」断言未同步 → 已补入期望集合；
  (2) `test_analytics_api` 用冻结的 `NOW=2026-08-21` 造数据却调用使用真实时钟的 HTTP 路由，
  20 天前的事件在 2026-08-31 正好掉出默认 30 天窗口，**无任何代码改动就开始失败**，且此后每天
  都会失败。`applied_job` 与 `strong_hangzhou` 增加可选 `now` 参数，API 测试改为锚定真实时钟；
  单元测试仍用冻结 NOW（它们同时把 NOW 传给 `compute_analytics`）。
- 个人/商用分离已就位：`config/career_strategy.yaml` 为空模板（`preferred_roles: []` 等），
  个人策略只存在于已 gitignore 的 `data/career_strategy.yaml`。新增忽略 `data/acceptance/`
  （84 个 pytest 临时 .db 与一次性验收脚本）。提交前审计：暂存区 `data/` 下 0 文件，
  无 `.env`/密钥/数据库/日志/简历；内容扫描命中的 `securityId=TOKEN`、`sk-test-not-a-real-key`
  等全部是显式假值测试夹具。个人数据文件在磁盘上原样保留，未删除未覆盖。
- 验收：完整后端 pytest（`--basetemp=.tmp/pytest-commercial`）退出码 0；扩展 build EXIT 0 +
  298/298；前端 build EXIT 0 + 31/31。
- 下一步：用户提供 Git 仓库地址后再执行 `git remote add` 与推送；不使用 force push，不改动 `main`。

- Current (2026-08-31): “任何人可用”产品化 P0 第一段已离线实现。新增 `/setup` 个人设置页，
  新用户可上传本地简历、从后端声明的受支持城市中多选，并保存自己的岗位方向/技能；缺少简历、
  城市或岗位方向时搜索 fail closed 并引导完成设置。新增只读 loopback
  `GET /api/tasks/search-plan/options`，城市能力和上限由后端唯一声明；搜索控制台不再写死北京、
  上海、广州、杭州数组或“云平台”，而是读取当前职业策略与活动简历。后端 focused 95/95、
  full pytest 100%（使用仓库内 basetemp 绕过 Windows 系统临时目录权限）、前端 production build
  与 31/31 tests、`git diff --check` PASS。没有访问 BOSS/Chrome、启动任务或调用付费 AI。
  后续同一阶段又将运行时职业策略迁到 gitignored 的 `data/career_strategy.yaml`，并以 SHA-256
  一致性验证保全当前用户策略；tracked `config/career_strategy.yaml` 改为无城市、岗位、技能或个人
  说明的中性模板，测试改用明确标注为虚构的独立 fixture。空策略首次加载/保存与现有用户策略
  内容保全测试 PASS。后端已安全切换到当前代码（PID 40092），`health/database=ok`；只读验证
  options 返回 4 个已验证城市，策略路径为 `data/career_strategy.yaml` 且当前 13 个岗位方向保留，
  localhost `/setup` 与 `/console` 均返回 200。Next action: 进入候选阶段/应届与社招贯穿式配置 P1；
  该设置必须同时贯穿后端 intake、历史清理和 MV3 搜索过滤，不能只加前端开关。

- Current (2026-08-31): 本地控制台默认工作流与 Windows 启动方式已简化。侧栏从 14 个常驻
  入口收敛为“搜索岗位 / 投递队列 / 岗位库 / 面试 / Offer / 简历”6 个高频入口；数据概览、
  HR沟通记录、采集、分析、策略和设置放入可保持展开的“更多工具”。根路由与未知路由默认进入
  `/console`。控制台常驻区只保留简历驱动搜索和“查看岗位库”，薪资回填、跨任务匹配、旧任务
  管理及批量复核统一放进“更多功能”，原功能和路由仍保留。新增根目录 `Start-JobAgent.cmd` /
  `Stop-JobAgent.cmd` 与 `scripts/launch-console.ps1`：双击后按健康状态复用或隐藏启动既有
  FastAPI/Vite，等待 8000/5173 就绪并打开控制台；PID 仅记录在 `.tmp/launcher`，停止时只处理
  与记录 PID 和端口同时匹配的自有进程。独立端口生命周期验证“启动 → 重复启动复用 →
  health ok / frontend 200 → 安全停止无残留”PASS；前端 production build、8/8 tests、PowerShell
  AST parse、`git diff --check` PASS。本地浏览器只读可视验收确认 6 个主入口、8 个折叠入口且维护
  面板默认不可见；未访问 BOSS、未启动任务、未调用 AI。当前 8000/5173 服务健康可用。

- Current (2026-08-31): 岗位库历史“应届岗位清理”已离线实现并验收。岗位库新增
  “筛选并全选应届/校招/实习岗位”，复用搜索 runner 已有的确定性非应届规则（标题、届别、
  JD 明示要求及“不限应届/非应届/有经验者/社招”等否定保护），只返回状态为新建、已查看或
  已收藏的匹配岗位。用户预览并再次确认后，逐条复用既有 `api.skipJob ->
  application_workflow.skip` 路径追加“已跳过”事件；不物理删除岗位/分析/历史记录，不改写
  已投递、已回复、面试、Offer、已拒绝或已跳过岗位。失败项保留选择且不自动重试；无 AI、
  Chrome 或 BOSS 动作。backend focused jobs API 17/17 PASS；完整 backend pytest 使用仓库内
  唯一临时目录后 100% PASS（首次默认 Windows pytest tmp 目录仅发生 WinError 5 环境错误，
  无产品断言失败）；frontend production build 与 26/26 tests PASS；`git diff --check` PASS。
  已精确停止旧的 8000 端口 backend 并以当前 checkout 重启；`/health` 与 database 均为 `ok`，
  OpenAPI 已暴露 `early_career_cleanup`。只读预览在当前岗位库识别出 6 个待清理岗位，未修改
  任何岗位状态。Next action: 刷新本地岗位库，预览后由用户决定是否批量标记为已跳过；不需要迁移
  或 Reload extension。

- Current (2026-08-31): per user direction, M7 BOSS conversation scanning is suspended rather than
  debugged further. Extension 0.1.20 removes `scan-current-boss-chat` from the localhost bridge,
  foreground action dispatcher and advertised capabilities; HR沟通 no longer shows a scan button.
  Retained parser/fixture code is unreachable from the product UI. M6 now hands an unverified
  one-click result directly to a human “核对并记录本次投递” dialog; only that explicit confirmation
  calls the existing `api.markApplied -> application_workflow.mark_applied` path, after which the Job
  appears under 岗位库 → 已投递. Cancelling keeps the existing `application_result_unknown` audit
  event and does not guess success. Extension build and full 298/298 tests PASS; frontend production
  build and 24/24 tests PASS; no backend/schema change, no live BOSS action and no paid AI call.
  Next action: Reload extension 0.1.20 and refresh the local console; no backend restart is required.

- LIVE HARD STOP (2026-08-31): M7 multi-conversation traversal triggered or coincided with a real
  BOSS `/web/passport/zp/403.html?code=32` account restriction. The page states abnormal behavior,
  temporarily restricted access and a recovery time of 2026-09-01 14:05. This is a hard FAIL, not a
  selector issue or live acceptance. Immediate read-only backend evidence remains zero recruiter
  conversations/messages, so no chat data was imported before the restriction. Do not retry,
  refresh repeatedly, change IP, automate recovery or bypass the restriction. The 0.1.19 bounded
  all-conversation traversal must not be run again after recovery without a separately authorized
  safer redesign; the safe fallback is one explicitly selected/current conversation per human action.

- Current (2026-08-31): M7 live attempt after 0.1.18 is NOT PASS. The extension successfully opened
  the normal logged-in BOSS `/web/geek/chat` page, but a read-only backend check immediately afterward
  still found zero recruiter conversations/messages. A bounded, read-only Chrome structure check found
  the live list/conversation roots (`.user-list`, four visible `li[role=listitem]` and `.friend-content`,
  one `.chat-conversation`) but did not perform another scan or contact click. Extension 0.1.19 adds
  compatible header/message-list fallbacks and a visible BOSS-page status bar showing scanning,
  complete or failed plus aggregate counts only—never message bodies. Focused extension 140/140 and
  frontend build + 23/23 PASS. Next action: Reload 0.1.19, refresh HR沟通, confirm once, then read the
  visible status bar; no backend restart or migration is required.

- Current (2026-08-31): M7 前台入口修复完成（extension 0.1.18，OFFLINE PASS）。0.1.17 在
  没有预先打开 `/web/geek/chat` 时只返回错误，因此本地 HR沟通按钮无法自行进入消息页。
  现在一次人工确认会激活唯一现有消息页；若不存在，则在同一前台正常 Chrome 窗口以固定的
  `https://www.zhipin.com/web/geek/chat` 新建一个活动标签页，不覆盖现有搜索/详情页。扩展
  对新标签页和消息列表做有界就绪检查后才开始原有 50 对话/5 滚动扫描；多个消息页、登录、
  验证、失焦、错误来源或未就绪 DOM 仍 fail closed。新增真实 worker mock 验证“无消息页则
  恰好新建一个”和“已有唯一消息页则复用且不重复新建”。extension focused 140/140、frontend
  build + 23/23 PASS；未做真实 BOSS 扫描。下一步 Reload extension 0.1.18、刷新 HR沟通页并
  人工确认一次 live 扫描；本次无需后端迁移或重启。

- Current (2026-08-31): M7 已扩展为一次人工确认触发的有界前台沟通扫描（extension 0.1.17）。
  HR沟通页不再要求逐个选择岗位；扩展只使用同一正常 Chrome 窗口中唯一的
  `/web/geek/chat` 标签页，最多顺序选择 50 个可见联系人、滚动联系人列表 5 次、读取每个
  对话最多 100 条已渲染文字消息。切换联系人可能让 BOSS 将会话标为已读，确认框已明确提示。
  后端优先按 query-stripped `/job_detail/<external_id>.html` 唯一关联现有 `applied` BOSS Job；
  只有没有职位链接时才允许唯一的公司+职位匹配，歧义/缺失项只计数并跳过，绝不新建第二套
  Job 或猜测关联。仍然无定时、后台轮询、自动重试、AI 分析、输入或发送消息。
- 同轮新增非应届身份硬过滤：搜索 runner 在打开卡片前跳过标题含应届/校招/校园招聘/毕业生/
  管培生/实习/届别的岗位，不占有效候选名额；详情预览与正式 extension import 边界再次使用
  确定性规则拦截，防止绕过。`career_strategy.yaml` 同步加入这些负向偏好；人工历史录入路径未删。
- Offline acceptance: backend 完整 pytest 100% PASS；extension TypeScript build 与 Node tests
  297/297 PASS；frontend TypeScript build 与 Node tests 23/23 PASS；`git diff --check` 无空白错误。
  没有控制真实 Chrome、没有扫描聊天、没有启动搜索、没有 AI 调用。下一步：重启后端、Reload
  extension 0.1.17、刷新本地 HR沟通页，然后由用户确认一次 live 扫描；离线结果不是 live PASS。

- Live attempt (2026-08-31): user reported one scan click after Reload 0.1.16, but read-only backend
  evidence shows no import: `recruiter_conversations=0`, `recruiter_messages=0`, and no row with an
  M7 `source_message_id`. Backend `/health` and database are healthy at revision 0020, and two applied
  BOSS jobs are eligible, so this attempt is **not live PASS**. No retry was initiated by Codex. Next:
  capture the exact success/error banner shown by the HR沟通 scan UI; if the local page was open during
  extension Reload, refresh that local page once so the new 0.1.16 console bridge is injected, then the
  user may explicitly confirm one more scan.

- Live readiness (2026-08-31): user Reloaded extension 0.1.16 and explicitly authorized only the
  backend restart plus migration 0020. `app.cli migrate` upgraded the real SQLite database from
  `0019_application_attempt_claim` to `0020_recruiter_message_source_id`; read-only verification
  confirmed the revision, `source_message_id` column and its conversation-scoped unique index.
  The backend is listening on `127.0.0.1:8000`; `/health` reports status/database `ok` and
  `auto_apply=false`. No BOSS scan, browser action, message action or AI call was started. Next:
  one user-triggered current-conversation live scan from HR沟通; this remains pending live PASS.

- Current (2026-08-31): M7 人工触发的 BOSS 当前对话增量扫描已实现，尚未执行 live 扫描。
  HR沟通页要求人工选择一个已投递的 BOSS 岗位并再次点击；扩展只采用同一正常 Chrome
  窗口中唯一的 `/web/geek/chat` 标签页，切到前台后读取当前已渲染的文字消息一次。真实
  DOM 选择器基于只读核验：`.chat-conversation` / `ul.im-list` / `li.message-item` /
  `.text-content`，方向取 `item-myself|item-friend`，增量身份取 `data-mid`；消息卡片跳过。
  后端复用 `RecruiterConversation` / `RecruiterMessage`，迁移 0020 仅增加可空
  `source_message_id` 与会话内唯一索引。扫描接口仅接受扩展 origin，限制 100 条/10 万字，
  同一 `data-mid` 内容冲突整批零写入，响应只返回会话 ID/聚合计数且固定
  `ai_used=false`，不回显聊天正文。无轮询、后台扫描、滚动、
  联系人切换、自动分析、发送、重试或凭据/会话数据读取。扩展版本 0.1.16。离线验收：
  backend 完整 pytest 退出码 0；frontend production build 与 Node tests 23/23 PASS；
  extension TypeScript build 与 Node tests 296/296 PASS；M7 本地 DOM fixture 2/2 PASS，
  迁移 focused tests 25/25 PASS，`git diff --check` 无空白错误。下一步仅需 Reload 扩展、
  执行 0020 迁移并做一次由用户点击触发的最小 live 扫描；不能把离线结果记作 live PASS。

- Current (2026-08-30): 薪资乱码判定 + 岗位库批量 AI 分析，离线实现与测试完成。
  新增 `backend/app/services/salary_text.py`：BOSS 混淆字体的 Private Use Area 码位
  (U+E000-U+F8FF 及两个补充平面)、U+FFFD 与方块占位符一律判为无效薪资；有数字或
  「面议/薪资面议」才算有效。该判定接入 `job_normalizer.normalize_job`（无效薪资在
  canonical intake 之前就被丢弃，永不入库）、`job_intake.save_posting`（只有当已存储
  薪资无效时才允许再次采集补写，可靠薪资绝不被覆盖）、`extension_intake`（乱码候选按
  「无可读薪资」处理，提示可由本机 OCR 补充）与 `salary_backfill`（无效薪资计为缺失，
  重新进入既有回填/OCR 计划；`record_item(updated)` 也不再接受乱码值）。扩展侧
  `isUsableSalary()` 早已拒绝同一类字符，本轮未改扩展代码。
- 数据迁移 `0017_unreadable_salary` 把库中已存的乱码薪资清为 NULL（占位符不含可恢复
  信息），仅数据、不改结构，可读薪资不受影响。**副作用记录**：用户本地 uvicorn 以
  `--reload` 运行，本轮源码改动触发热重载，其 lifespan `init_db()` 已把真实库升级到
  `0017_unreadable_salary` 并清除了 18 条乱码薪资。当前真实库：119 岗位 / 101 有薪资 /
  18 缺失，全部 18 条为合格 BOSS detail URL，`salary_backfill` 计划 eligible=18；
  既有 run #1 已 `completed`（89/89），因此可新建下一轮回填计划。
- 岗位库新增「批量 AI 分析」：只分析当前已选中的岗位，未选中时按钮禁用。新增只读端点
  `POST /api/jobs/analyze-batch/plan`，复用 `task_matching.plan_jobs`（同一活跃简历、
  同一 fast 模型、同一 `JobAnalysis` 缓存键），返回选中数 / 已缓存数 / 预计新增调用数 /
  批次上限 `MAX_ANALYSES_PER_RUN` / 超限被推迟数；生成计划不调用模型、不写库。用户在
  确认弹窗看到这些数字并点击确认后，才调用既有 `/api/jobs/analyze-batch`（已加入按序去重，
  使计划与实际执行的集合一致）。失败岗位只报告不自动重试；上限不会被偷偷放宽；不改任何
  人工决定状态（仅沿用既有「新建 -> 已查看」规则），无投递/收藏/消息/Chrome 控制。
- 测试：focused 后端（salary_text / salary_backfill / salary_ocr / analysis / migrations /
  extension_api / jobs_api / task_matching）全绿；完整后端 pytest 收集 1687 项、100% 完成、
  退出码 0（含既有 29 项浏览器相关 skip）；前端 `npm run build`（含 tsc 类型检查）PASS，
  前端 Node 测试 14/14；扩展 `npm run build` 无新 dist 漂移，扩展 Node 测试 289/289。
  注：`extension/package.json` 的 `npm test` 脚本在 Node 24 上因 `node --test tests/` 目录
  形式报 `Cannot find module`，改用 `node --test "tests/*.test.cjs"` 可正常运行；这是既有
  脚本问题，本轮未改动。
- 本轮未提交、未推送，未发生任何付费 AI 调用、BOSS 页面访问、截图、OCR、投递、收藏或消息。
- 追加（同日，用户明确要求「三个太少了增加到全部」）：薪资回填的每批 3 个上限现在可以在
  计划**创建时/未开始时**就一次性授权为全部，不必先跑完一批 3 个再点「处理剩余全部」。
  `salary_backfill.authorize_remaining` 由「只允许 paused」放宽为「pending 或 paused，且没有
  处理中岗位」，仍要求 `confirmed=true`、仍按 run 存储、仍受 `MAX_PLAN_JOBS=100` 约束，默认值
  对其他 run 仍是 3。console 面板改为两个按钮：「准备并处理全部 N 个」与「每批 3 个」，
  pending 状态另有「直接处理全部 N 个」，并显示「本次授权 session_processed/session_cap」。
  扩展 worker 无需改动——它的循环完全由后端 `claim` 的状态驱动，上限只在后端。
  放宽的只是连续性，不是权限：登录失效、验证页、前台丢失、worker 错误、手动暂停仍立即
  pause closed，`record_item(updated)` 仍要求 canonical intake 已写入有效薪资。
  `CLAUDE.md`「Historical salary backfill」小节已补记该 session-cap 授权。
- 现场诊断（同日）：用户点了「准备并处理全部 18 个」，计划 #2 已创建且授权成功
  （total_jobs=18、session_cap=18、last_action=remaining_authorized、state=pending、processed=0），
  但扩展拒绝启动，面板显示的「请在同一 Chrome 窗口保持已登录的 BOSS 标签页。」正是扩展返回的
  错误。原因是 `background.ts` 的 `reusableBossTab()` 只接受 path 以 `/web/geek/job`、
  `/web/geek/recommend` 或 `/job_detail/` 开头的标签页，而当时那个 BOSS 标签页停在首页（path `/`）；
  首页/登录/聊天/验证页是被刻意排除的。这是既有的安全取舍，未放宽。
  仅做两处前端修正（无需 Reload 扩展）：pending 且已授权全部时按钮不再谎称「开始首批 3 个」，
  改为「开始（本次授权 N 个）」；面板补一行明确说明 BOSS 标签页必须停在搜索结果页或职位详情页。
- 首页启动放宽（扩展 0.1.11）：`reusableBossTab()` 现在也接受 BOSS **首页**（path 精确等于
  `/`，因此带 `?ka=` 查询串的首页同样可用）。理由：域名仍是精确的 `https://www.zhipin.com`，
  运行开始后本来就会立刻把该标签页导航到自己的目标 URL，导航后的登录/验证 preflight 仍然
  fail closed。用精确 `/` 而非前缀匹配，是为了绝不采用 `/web/user/`（登录）与 `/web/geek/chat`。
  新增 7 个行为测试覆盖首页/带查询首页/详情页可采用，登录页/聊天页/验证页/异域名不可采用；
  去掉改动后首页两例变红，加回后全绿。
- 明确不做「启动时自动新开 BOSS 首页」：`CLAUDE.md` 的 console 启动条款已记载「Console clicks
  do not grant activeTab to the created BOSS tab」。worker 自己新建的标签页没有 `activeTab`
  授权，`tabs.captureVisibleTab` 便不可用，薪资 OCR 会整批失败——这 18 个岗位恰恰是靠 OCR 才
  能读出薪资，结果会是 18 个 `unavailable`。复用「用户已点过 JobAgent 图标的那个标签页」才是
  OCR 能工作的前提；不会为掩盖该限制去申请 all-sites 截图权限。
- 登录状态检测本来就存在，无需新增：回填循环每打开一个详情页都会读 `login_required` /
  `verification` 并 pause closed；扩展从不登录、不读凭据、不绕过验证。
- 岗位库三处反馈修复（前端为主，无需 Reload 扩展）。用户反馈「没有删除按钮 / 跳过点了没用 /
  批量 AI 匹配没反应」。读库核对：事件 244 记录 `job_id=1 status_changed reviewed -> skipped`，
  且 08:02:46-08:04:52 有 12 条 `gpt-5.6-luna` 分析（分数 35-84）——跳过与批量分析**都真的执行了**。
  真实缺陷是可见性：`<Alert>` 渲染在页面最顶部，而操作按钮在表格深处，滚动后消息完全在视野外；
  且 `handleStatus` 成功时不设任何提示，只静默重载。
  修复：(a) 反馈条改为 `position: sticky; top: 0`，任何滚动位置都能看到；(b) 状态变更成功时显示
  「『标题』已标记为已跳过」；(c) 新增每行「删除」按钮，接既有 `DELETE /api/jobs/{id}` 与
  `api.deleteJob`（此前后端与 client 都在，只是没接到页面），删除前 `window.confirm` 明示会连带
  删除分析与事件且不可恢复，删除后同步清理选中集合并作废当前批量计划；(d) 批量确认弹窗补充预计
  耗时（约 10 秒/次调用），避免长时间运行被当成卡死。
- 同时修掉一个真正的静默无操作：`PATCH /api/jobs/{id}` 只在目标状态与当前不同时才调用 workflow，
  对已是该状态的岗位返回 200 且不写事件，与成功无法区分。现在「收藏」「跳过」在岗位已处于该状态时
  直接禁用并给出 title 说明，不再发出只能看起来没反应的请求。后端行为未改（该 no-op 已由测试记录）。
- 新增后端测试：`test_delete_removes_a_job_with_a_status_trail`、
  `test_skip_moves_the_status_and_is_visible_in_the_list`（含 no-op 只产生 1 条 status_changed 事件）、
  `test_deleting_an_analyzed_job_removes_it_and_its_analysis`（分析→跳过→删除，analysis 转 404、
  dashboard analyzed_jobs 归零）。完整后端 pytest 退出码 0；前端 build + 14/14 PASS。
- 岗位库新增「选中未分析的 N 个」按钮：一键选中当前结果集中 `latest_analysis === null` 的岗位，
  按钮标签直接显示可选中的数量，为 0 时禁用。选择范围**限定在当前结果集**——列表的
  `useEffect` 本来就会把选中集合裁剪到实际返回的 id，越界选中会立刻消失。因此同时在高级筛选中
  暴露了后端早已支持但 UI 从未暴露的 `analyzed` 参数（分析状态：全部/未分析/已分析）：当
  `data.total > 当前列表长度` 时按钮带 title 提示「只会选中当前列表里的未分析岗位；如需全部，
  请先在高级筛选中选『未分析』」，不静默少选。选中本身不发起任何调用，仍需另外点「批量 AI 分析」
  并通过费用确认。
- 新增后端测试 `test_analyzed_filter_isolates_the_unanalyzed_jobs`：`analyzed=false` 只返回无分析
  岗位且 `latest_analysis` 均为 null，`analyzed=true` 只返回已分析岗位，不加筛选时两组仍可由同一
  字段区分。完整后端 pytest 退出码 0；前端 build + 14/14 PASS。
- 修正上一轮自己引入的缺陷：「选中未分析的 N 个」只从当前结果集挑，而列表按匹配分排序、
  `limit: 100`，未分析岗位没有分数排在最后，正好被整页截掉。实测 118 个岗位中 100 个已分析、
  18 个未分析，按钮却显示「0 个」并禁用，等于谎称没有未分析岗位。改为**先筛选再全选**：
  按钮「筛选并全选未分析岗位」把 `filters.analyzed` 设为 false，重载后整体选中；选中集合的
  规则抽成纯函数 `frontend/src/pages/jobSelection.ts`（照 `matchResultLifecycle.ts` 的既有做法），
  select-all 用 `useRef` 只消费一次，加载失败时清除，避免残留标记在之后某次无关刷新时全选。
  列表被截断时工具栏显式说明「共 N 个符合条件，当前只显示前 M 个」。
- 浏览器实测（只读，未确认任何付费操作）：点击后列表变为 18 行且 18 项全部选中；再点「重置筛选」
  回到 100 行且选中归零，证明 select-all 标记未被二次消费。「批量 AI 分析」弹窗正确显示
  选中 18 / 上限 50 / 实际处理 18 / 缓存 0 / 预计新增 18 次调用 / 约 3 分钟，随后点「取消」退出，
  本会话未发起任何付费调用。
- 修复 `extension/package.json` 的 `test` 脚本：`node --test tests/` 在 Node 22+ 会把目录当模块解析
  并报 `Cannot find module`，改为 `node --test "tests/*.test.cjs"`，`npm test` 恢复可用（289/289）。
- README 补充岗位库批量分析、筛选并全选未分析、删除岗位与「同状态按钮禁用」的说明。
- 本轮验收：完整后端 pytest 退出码 0；前端 build PASS、Node 测试 **20/20**（新增 6 个
  `jobSelection` 用例）；扩展 `npm test` 289/289。`git diff --check` 无空白错误。
- 半自动投递流水线（用户 2026-08-30 选择「先半自动」；自动点「立即沟通」仍**不实现**，
  `auto_apply_enabled` 保持硬编码 False，本次未改动任何投递/消息发送路径）。现状调查：
  18 个 apply 推荐、33 个 maybe，实际只投了 1 个；瓶颈是 `ApplicationProposal` 没有
  `source_url`，队列里看得到推荐却打不开岗位页。追踪侧（事件流/投递周期/漏斗/简历归因/
  面试/offer/决策快照）代码早已齐备，只是缺数据。
- 实现：`ApplicationProposal` 增加 `source_url`，`application_queue` 填充；队列每条新增
  「去投递 ↗」——真实 `<a target="_blank" rel="noopener noreferrer">`（不用 `window.open`，
  避免被弹窗拦截），点击时把招呼语写入剪贴板并提示「发送后回来点标记已投递才会记录」。
  打开页面与写剪贴板都不是决定：不改 `Job.status`、不发送任何内容，测试断言了这一点。
- 顺带修掉一个真实的 token 泄漏：`canonical_url()` 的 docstring 写着「before anything is
  persisted」，但只有扩展与快速采集路径调用了它，**手动粘贴的 `POST /api/jobs` 从未调用**，
  于是 `?lid=…&securityId=…` 原样入库。现在改在共享的 `job_normalizer.normalize_job` 里做，
  所有 intake 路径一致；`application_queue` 再防御性地清一次，保证修复前入库的旧行也不会被
  渲染成带 token 的可点击链接。
- 新增后端测试：队列返回的 `source_url` 已去查询串且不含 lid/securityId、读取队列不改状态、
  无 URL 的岗位返回 null 而不是坏链接。完整后端 pytest 退出码 0；前端 build + Node 20/20 PASS。
- 浏览器实测（只读）：队列页渲染 18 个「去投递 ↗」链接，href 为纯 `/job_detail/<id>.html`、
  rel 为 `noopener noreferrer`；未点击任何投递或标记操作。
- 用户反馈「去投递打开的岗位页面不存在」+ 城市下拉截图。两个都是真缺陷，已修并实测：
  (a) **坏链接**：点到的是 job 7，`source_url=/job_detail/live1.html`、`external_id=None`——
  live 验证留下的测试数据，恰好排在队列第一条。存着的 URL 不等于能打开的链接。新增
  `urls.is_openable_posting_url(url, external_id)`：站点由 URL 自身的 host 判定（`detect_source`），
  BOSS 链接必须满足 `salary_backfill` 已有的同一条身份规则（path 恰为
  `/job_detail/<external_id>.html`），否则队列返回 `source_url=None`、前端不渲染「去投递」而显示
  「无可用岗位链接」。手工粘贴的 BOSS 链接虽然 `source='manual'` 也一样受检。
  (b) **facet 塌缩**：`application.py` 的 `build_facets(eligible)` 与 `jobs.py` 的 facet 都是在筛选
  **之后**统计的，所以选了北京，杭州/上海就从下拉里消失，不重置就切不回去。改为每个 facet 排除
  它自己那一维再统计（队列用 `dataclasses.replace(filters, city=None)`，岗位库用只带关键词的
  `facet_stmt`），行仍然照常被筛选。
- 实测（只读）：不筛选与筛选北京时城市菜单都是「北京/杭州/上海」，而行只剩北京；队列 18 条中
  17 条给出链接，被抑制的恰好只有 job 7「杭州星澜科技有限公司（已人工确认）」。
- 新增后端测试：BOSS URL 与行 id 不匹配时不提供链接、匹配时提供、选中某城市后城市菜单仍列出全部
  （队列与岗位库各一份）、岗位库状态 facet 同理。完整后端 pytest 退出码 0；前端 build + 20/20 PASS。
- 遗留数据问题（需用户决定）：job 7 是 live 验证留下的测试岗位，仍占着队列第一条。岗位库现在有
  「删除」按钮可自行清理；未擅自删除。
- 用户反馈「一键搜索只出了杭州·云计算工程师」「查看候选岗位点了没反应」。两个都是真缺陷：
  (a) `search_plan._resume_search_keyword()` 只取策略里**第一个含「云」的岗位**就返回，而用户的
  `preferred_roles` 有 13 条，其余 12 条从未参与搜索。CLAUDE.md 的 M4e 条款本来写的就是
  「deterministically expands configurable **city x keyword** inputs」，是快捷入口把它压成了 1 个。
  新增 `resume_search_keywords(strategy, limit)`：中文岗位优先（BOSS 是中文站，英文关键词命中少）、
  含「云」的中文岗位领先、去重、保序、截断到 limit；仍然零 AI、零网络。
  `prepare_resume_searches` 改为生成「城市 × 方向」组合，`limit = max(1, MAX_BATCH_TASKS // 城市数)`，
  `MAX_BATCH_TASKS = 5` 与 `consoleExtension.selectBoundedPendingTasks` 的 1–5 硬上限对齐，**不放宽**。
  实测真实策略：1 城市 → 5 个方向（云计算/云平台/云运维/云原生/运维开发工程师）、2 城市 → 各 2 个、
  3 城市 → 各 1 个、4 城市 → 各 1 个，任务数分别为 5/4/3/4，均 ≤ 5。
  (b) 「查看候选岗位」只设置了 `selectedTaskId`，而候选面板渲染在 `ConsolePage.tsx:771`，远在
  「历史缺薪回填」下方，点了内容出现在屏幕外——与之前岗位库提示条同一类问题。改为选中后
  `scrollIntoView`（用 ref 标记，在 `selectedTaskId` 变化后的 effect 里执行一次）。
- UI 文案同步：数量输入改为「每个方向搜索岗位数量」，卡片副标题与提示说明「一次最多 5 个
  『城市 × 方向』组合（城市越多，每个城市分到的方向越少）」，准备完成的消息列出实际方向。
  批次确认弹窗本来就逐条列出 `#id · 城市 · 关键词`，5 个任务会全部列出。
- 新增/更新后端测试：单城市展开为 5 个不重复方向、四种城市数下的任务数均 ≤ 5、关键词中文优先且
  去重保序、空策略报错；两处既有断言（单城市任务数、路由返回）按新语义更新。
  完整后端 pytest 退出码 0；前端 build + Node 20/20 PASS。
- 用户已完成薪资回填：控制台显示 115 个岗位、115 个有薪资、可回填 0 个。
- 用户反馈「刚搜到的 20 个杭州岗位又没有薪资」。核对：新入库 15 条（#120-#134，09:30:57-09:31:32），
  全部来自详情页（JD 110-1009 字），`salary_text` 全为 NULL，且 intake 注记是普通的
  「岗位已创建（Chrome 扩展检测）」而非 OCR 那一条——说明 DOM 薪资被字体混淆挡掉后，
  本机截图 OCR 回退**没有成功，而且失败是静默的**。
- 真实缺陷：`supplementSalary` 其实算出了原因，并以 `本地薪资 OCR 未采用：<reason>` 写进
  `candidate.warnings`（reason 取值 unavailable / uncertain / rate_limited / frame status），
  schema 也接收（`extension.py:39`），但 `extension_intake.inspect` 自建 warnings 列表、
  从不读候选带来的那个，`import_one` 也不记录——**原因被算出来、传过来，然后扔掉**。
  这就是为什么 15 个无薪资岗位在库里没有任何解释。
- 修复（仅后端，无需 Reload 扩展）：新增 `SALARY_OCR_WARNING_PREFIX` / `salary_ocr_note()`；
  `inspect` 把该原因并入返回的 warnings；`import_one` 的创建注记抽成 `_creation_note()`，
  在 OCR 成功时保持原文案，在 OCR 失败时写「岗位已创建（Chrome 扩展检测；<原因>，薪资待回填）」。
  未新增权限、未改 OCR 逻辑本身、未放宽任何上限。
- 未能确定本次 15 条的具体 reason：修复之前它就已经被丢弃了，无法事后还原。下次采集会记录。
  最可能的原因仍是运行标签页缺少 `activeTab` 授权（`CLAUDE.md` 已载明 console 启动创建的标签页
  不获得该授权，截图 OCR 因此可能不可用，且不得为此申请全站权限）。
- 补救路径已就绪：薪资回填计划显示 total 130 / present 115 / missing 15 / eligible 15，无进行中计划。
- 新增后端测试：OCR 失败原因写入岗位事件注记并含「薪资待回填」、成功读到薪资时不写该注记、
  预览接口同样返回该原因。完整后端 pytest 退出码 0。
- 全站巡检（浏览器实测）：14 个路由全部正常渲染、所有 API 200、无运行时错误。之前猜测的
  `#/resumes`/`#/decision` 不存在是我路径写错，真实路由为 `#/resume` 等；控制台里的 HMR 500 是
  编辑过程中的旧记录，重载后正常，新文案（「每个方向搜索岗位数量」「一次最多 5 个城市 × 方向」）已生效。
- 发现并修复一个真实的缓存正确性缺陷：`compute_cache_key` 用 `Job.content_hash`
  （公司 + 职位 + JD 正文），**不含薪资**；但 `scoring.py:302-306` 会解析 `salary_text` 并算出
  `salary_min/max/salary_meets_minimum`，既喂给模型也影响 `heuristic_score`。因此「先分析（无薪资）
  → 后回填薪资」会让缓存键保持不变，那份没看到薪资的分析被永久复用，与 `CLAUDE.md`
  「改了 JD 会自动失效缓存」的声明矛盾。
- 修法是**定向失效**而不是把薪资并入缓存键（后者会让 115 条既有分析全部失效并产生重新付费）：
  在唯一会事后补薪资的地方 —— `job_intake.save_posting` 的 `enrich_missing_salary` 分支 ——
  删除该岗位的缓存分析，计数记入 `IntakeResult.invalidated_analyses` 与事件注记
  「已作废 N 条基于旧薪资的分析缓存，请重新分析」。已确认无任何外键引用 `job_analyses`，删除安全。
  薪资未变化的岗位一条都不会被作废。新增两个测试分别覆盖这两个方向。
  `CLAUDE.md` 的 Caching 小节与 README 使用流程同步记录了该规则与推荐顺序（先回填再分析）。
- 用户在本轮同时完成了第三次薪资回填：run #3 完成 15/15、成功 15、无法读取 0。当前数据库
  130 个岗位、**0 个缺薪资**、115 个已分析、15 个待分析，且缺薪资与已分析集合无交集，
  因此本次修复对既有数据零成本。上一批 OCR 失败基本可归因于运行标签页缺少 activeTab 授权。
- 本轮验收：完整后端 pytest 退出码 0；前端 build + Node 20/20；扩展 `npm test` 289/289。
- M6 实现，**第一阶段（后端确认门）完成；扩展 DOM 点击未实现，已停在那里等用户裁决**。
  用户 2026-08-30 先授权 M6 策略入 `CLAUDE.md`，随后明确授权实现代码。
- 实现内容：`models/application_approval.py`（`ApplicationApproval`，快照 job_id/company/title/
  canonical_url/external_id/resume_id/resume_hash/answers_text+hash，状态 pending|consumed|
  invalidated，结果 applied|unknown|failed，`applied_event_id`）；迁移 `0018_application_approval`
  （纯 CREATE TABLE，避免 0005 的批量重建级联；重复 `TimestampMixin` 的 server_default）；
  `EventType.application_result_unknown`；`services/application_approval.py` 三个操作
  `request_approval` / `validate`（纯读，检查不会消费）/ `record_outcome`（一次性消费）。
- 关键保证：确认必须 `confirmed=true`；一次确认只绑一个 job；同一 job 的新确认作废旧的；
  job/简历内容/答复文本/页面身份任一变化即 fail closed 且不写任何结果；`applied` 只经既有
  `application_workflow.mark_applied` 写入（无第二条路径），保证恰好一条 applied 事件并保留
  v0.7 简历归因；结果不可核实记为 `application_result_unknown` 且**不动 `Job.status`**；
  已投递的 job 不能再次授权；无 canonical URL/external_id 的 job 不能授权。
- 测试 `tests/test_application_approval.py` 20/20，逐条覆盖 Implementation Gate 十项，其中
  「无 AI→执行路径」用 AST 检查真实导入与标识符（不是 grep 散文），「无批量入口」用签名反射断言
  不存在 `job_ids` 参数。既有测试 `test_every_event_type_has_a_chinese_timeline_label` 抓到新
  EventType 缺中文标签，已在 `ApplicationTimeline.tsx` 补「投递结果待确认」。
- **未实现且已刻意停止**：扩展侧的实际提交/点击、路由与前端确认 UI。两个原因：
  (1) 在 BOSS 上「投递」就是点「立即沟通」，而它打开聊天并把招呼语发给招聘方——M6 §1 绑定了
  message text，§3/§7 却禁止 automatic recruiter messaging/打招呼。在 BOSS 上这两件事是同一个
  动作，需要用户明确裁决「逐岗位人工确认的一次性招呼语是否属于 M6 授权范围」；
  (2) 无法查看 BOSS 真实 DOM（CLAUDE.md 禁止测试触碰 zhipin.com），盲写提交选择器会导致点错按钮
  或向真实招聘方发错消息。
- 验收：完整后端 pytest 退出码 0；前端 build + Node 20/20。迁移 head 断言由 `0017_unreadable_salary`
  更新为 `0018_application_approval`（5 个文件 10 处）。真实数据库未升级，未运行任何投递。
- 用户裁决（2026-08-30）：在 BOSS 上「立即沟通」与其不可分割的首次招呼语属于同一个投递动作，
  定名 **human-confirmed single application action with an inseparable initial greeting**。
  已据此同步 `CLAUDE.md` M6：章节头写入该定名；§1 绑定字段改为裁决列举的九项
  （job_id / company / title / canonical_url / external_id / resume_id / resume_hash /
  answers-message_text / 其 hash），并新增「每次真正执行前必须再次显示同一快照的最终确认界面」；
  §3 把「no 打招呼」改写为「首次招呼语作为不可分割的另一半在 M6 内，此后任何消息一律禁止」；
  §6 明确 applied 只能由页面出现可核实成功状态后经既有 `mark_applied` 写入；§7 重写为一句边界
  「M6 may send the greeting that *is* the application; it may never send a message that *follows* one」。
- 同时修掉一处新产生的矛盾：Security rules 里原写「Sending or following up on a recruiter message
  ... stay forbidden **inside** M6 too」，与裁决冲突，已改为「首次招呼语在授权内，其后的任何消息与
  一切账号状态变更仍禁止」。全局复查确认无残留矛盾行。
- 本轮**未实现任何真实点击**，按用户要求等待可靠的、经过脱敏的按钮 DOM fixture。
- 观察到一个影响 M6 身份校验的事实（来自用户截图，非我访问站点）：「立即沟通」按钮出现在
  `/web/geek/jobs?...` 搜索页的右侧详情面板上，而非独立的 `/job_detail/<id>.html` 页面。M6 §4/§5
  要求标签页身份与 `canonical_url` 精确一致，在该页面上会直接 fail closed。因此 fixture 需要明确
  投递动作发生在哪个 URL 形态下，否则身份校验规则本身需要重新定义。
- 验收：完整后端 pytest 退出码 0（M6 后端确认门 20/20 仍全绿）；`git diff --check` 无空白错误。
- 接手 Codex 的 M6 实现并完成独立安全审查（2026-08-30）。**未启动后端、未迁移真实数据库、未触碰 BOSS。**
- 独立复跑：完整后端 pytest 退出码 0；扩展 294/294；前端 build + 22/22。
- 审查中发现并修复三个问题：
  (1) **验证缺口**：`extension/node_modules` 里的 typescript 丢失，`npm run build` 无法运行，
  意味着 294/294 是跑在可能过时的 `dist/` 上。用 `frontend/node_modules` 的 tsc 编译到临时目录后
  与 `dist/` **逐字节 diff**，结果 IDENTICAL，缺口关闭。修复方式仍需用户在 `extension/` 执行
  一次 `npm install`（`.\scripts\dev.ps1 test` 会先构建扩展，当前会失败）。
  (2) **两个 M6 前端测试无人执行**：`frontend/tests/m6Application.test.cjs` 存在，但 frontend
  **没有 `test` 脚本**，`.mjs` glob 也匹配不到它，等于那两条 M6 保证会静默失效。已给
  `frontend/package.json` 补 `test` 脚本，同时覆盖 `.mjs` 与 `.cjs`（22/22）。
  (3) **安全最关键选择器上的假陈述**：`selectors.ts` 的 `APPLICATION_CONTROL` 注释写着
  「Nothing in the current extension clicks this selector」，而 `extract.ts` 的
  `executeConfirmedApplication` 正在点它。已重写为「**This selector IS clicked**」并保留
  provisional 说明；`m6-readonly-diagnostic.test.cjs` 增加断言，禁止该注释再次退回旧说法。
- 构建环境已修复（用户授权）：在 `extension/` 执行 `npm install`（added 1 package = typescript，
  0 vulnerabilities）。`npm run build` 现在 EXIT 0，`npm test` 294/294，`.\scripts\dev.ps1 test`
  的扩展构建步骤恢复可用。重建后的 `dist/` 与审查时逐字节一致，构建未引入任何变化，
  因此上述审查结论继续成立。`extension/node_modules` 已 gitignore。
- 补上一个真实缺口（红→绿）：worker 若在 `begin_attempt` 与 `record_outcome` 之间崩溃，approval
  永远卡在 `executing`——不自动重试是对的，但**无人工出口**，该岗位从此无法再走 M6，且事件轨迹上
  没有任何解释。新增 `abandon_attempt(confirmed=True)` 与 `POST /{id}/abandon`：**没有 outcome 参数，
  只可能记 `unknown`**，写入 `application_result_unknown` 事件，不动 `Job.status`，不重试，
  并让该岗位可以重新确认。该路由刻意**不**要求扩展来源（因为前提就是扩展没回应）。测试 28/28。
- 已核验的安全属性：构建产物中 `.click()` 恰好两处（M4 卡片导航 + M6 单次投递），由测试锁死；
  扩展上报结果的类型是 `'unknown' | 'failed'`，**`'applied'` 无法表示**；`begin` 用条件 UPDATE +
  rowcount 原子领取；`record_outcome` 必须处于 `executing` 且两半页面身份齐备，并在领取后重校验
  全部快照；`outcome` 路由要求扩展来源，console 无法自称目击站点结果；控件必须唯一可见、非 disabled、
  文案恰为「立即沟通」且 `data-isfriend="false"`（拒绝点击已在聊天中的控件）；执行路径无
  `setInterval`/`MutationObserver`；五个端点全部单数、无批量入口；扩展权限仍是
  `activeTab`/`scripting`/`storage`，**未新增任何权限**；凭据/存储相关字符串全部只出现在注释中。
- 选择器已在用户真实登录的 BOSS 页面上**只读核对**（2026-08-31，用户明确要求代为执行；
  全程只读属性，未点击任何控件，用完关闭标签页）。两种状态均确认：
  未聊过 `<a class="btn btn-startchat" data-isfriend="false">立即沟通</a>`；
  已聊过 `<a class="btn btn-startchat" data-isfriend="true">继续沟通</a>`，外层多一个
  `<div class="btn btn-startchat-wrap">`。两种状态下严格选择器都匹配 2 个节点、**恰好 1 个可见**
  （BOSS 渲染了一个响应式隐藏副本），这正是唯一性判定只看可见节点的原因。
  `ka`/`data-url`/`redirect-url` 在真实页面上是 379-406 字符的会话令牌，代码只读其**存在性**、
  从不读值（既有测试已断言）。用户提供的 URL 带 `securityId`，未写入任何文件或日志。
- 据此把重建 fixture 改为真实属性集（补 `href="javascript:;"` 与 `ka`），并**新增**
  `boss_job_detail_already_chatted.html`；`test_extension_extraction.py` 增加 4 个**真实 DOM 行为
  测试**（非源码 grep）：未聊过岗位 preflight 通过且 observed_url 已去除 securityId/lid；
  **已聊过岗位 preflight 与 execute 双双返回 `control_wrong_state`、绝不 clicked**——这是
  「永不发送后续消息」这条 M6 硬边界的首个真实结构证据；身份不符拒绝；搜索分栏页不是投递面。
- `selectors.ts` 注释更新为已核对状态，并保留唯一仍未验证项：**点击是否真的会提交，从未在真实站点
  验证过**（没有点过）。诊断测试新增断言锁住这两句表述。
- 构建环境修复后复跑：完整后端 pytest 退出码 0；扩展 build EXIT 0 + 294/294；前端 22/22。
  `HUMAN_CONFIRMED_APPLY_ENABLED` 与 `AUTO_APPLY` 当前均为 False。
- M6 最终离线复核（2026-08-31，Codex direct）：补上并发硬停止——一个 approval 已进入
  `executing` 时，同一岗位不能生成第二份确认。前端也已接通 lost-worker 人工出口：最终执行
  命令一旦发出，同一 approval 永不重新启用；后端状态未知时确认窗锁住且只能刷新；确认已
  `executing` 时只能由本人点击“结束为结果未知”，该操作不接触 BOSS、不重试、不改变
  `Job.status`。M6 后端 32/32、真实结构 DOM 4/4、扩展 build + 294/294、前端生产构建 +
  23/23 均通过；`git diff --check` 无空白错误。实际数据库未迁移，feature flag 仍默认 false，
  没有执行真实投递。
- M6 下一步仅为一次真人 live acceptance：先明确授权后端迁移/重启并启用
  `HUMAN_CONFIRMED_APPLY_ENABLED=true`，Reload 扩展 0.1.14、刷新前端；然后由用户在投递
  队列选择一个本来就愿意投递的岗位，逐岗位手填预计招呼语、勾选“BOSS 无法独立核实”、
  核对快照并完成第二次最终确认。该点击可能真实发送首次招呼；Codex 不代替最终确认。
- M6 live acceptance 已进入准备点（2026-08-31）：用户授权开始后，真实数据库已从 `0018`
  增量迁移到 `0019_application_attempt_claim`；后端以本次进程环境
  `HUMAN_CONFIRMED_APPLY_ENABLED=true` 启动，`/health` 的 app/database 均为 ok，M6 启用
  探针返回预期 404（不存在的 approval），前端返回 200。正常 Chrome 控制台只读诊断显示
  JobAgent 扩展 **0.1.14 / protocol 1 / ready**，已登录 BOSS 标签页仍存在，本地投递队列已打开
  并保留给用户。尚未选择验收岗位、填写预计招呼语、创建 approval 或点击「立即沟通」；
  下一步必须由用户指定一个确实愿意投递的岗位，并在最终界面手填/确认预计招呼语。
- M6 首次 live 结果（2026-08-31）：用户在岗位 #120 完成了最终确认，approval #1 按设计
  原子领取并只点击一次；扩展无法核实站点成功态，因此本地记录为 `consumed / unknown /
  clicked_site_result_unverified`，`Job.status` 仍为 `reviewed`，applied 事件 0、unknown 事件 1。
  用户随后在 BOSS 聊天页发现实际发出的首次招呼语与确认框内手工填写的“预计文本”不同。
  消息正文未写入 STATUS/日志。该结果证明 `human_attested_unverified` 只能记录人的预期，不能
  约束或预测 BOSS 的真实动态招呼语；本次 M6 live acceptance 判定 **FAIL**，不得继续第二个
  岗位。后端已立即以默认配置重启，M6 探针返回 403（feature flag false），其余后端/数据库
  health 均为 ok。后续必须先重新裁决确认语义；在此之前不再启用 M6。

- Current (2026-08-30): BOSS login preflight is implemented in extension 0.1.9. The pure DOM
  detector distinguishes a login URL or narrowly selected, visible login affordance from a CAPTCHA/
  rate-limit interstitial. Automatic startup, every card inventory read, detail capture, salary OCR
  recheck and every controlled scroll now fail closed into `paused_login_required` before screenshot,
  intake or the next batch task. The backend exposes the distinct loopback transition and the console
  renders “需要登录 BOSS”; only an explicit resume after the human logs in continues the same bounded
  task. No credential/form/session storage is read or written and no login/CAPTCHA is automated.
- Login-preflight acceptance PASS: extension build + 280/280 Node tests; focused login/SearchPlan/
  salary backend 169/169; frontend typecheck and production build PASS. Read-only localhost health
  reports app/database `ok` and OpenAPI exposes `/api/tasks/{task_id}/run/login-required`. No BOSS
  task, navigation, screenshot, import or AI call ran. Reload unpacked extension 0.1.9 once before
  live use; if logged out, complete login in the visible BOSS tab and click “恢复”.

- Current (2026-08-30): quick resume search now accepts 1–4 intended cities (Beijing,
  Shanghai, Guangzhou, Hangzhou) and creates one exact bounded SearchPlan task per selected city.
  The displayed job-count limit remains per city; duplicate city input is collapsed in order and
  every city is validated before any task is written. A multi-city start reuses the existing exact
  ordered batch confirmation instead of adding another runner or persistence path.
- Salary follow-up (introduced in extension 0.1.8, current package 0.1.9): the missing values in the job library are real null database
  values, not a table-rendering defect. Console starts now prefer an eligible BOSS search/job tab in
  the same normal Chrome window, so a one-time JobAgent toolbar click on that tab can continue to
  authorize the existing bounded screenshot-crop OCR path. No all-sites permission, hidden browser,
  credential access or remote OCR was added. When the same canonical job is later captured with a
  reliable salary, canonical intake may fill only a previously missing `salary_text`; it never
  overwrites an existing salary and does not count the duplicate as a new import. Historical nulls
  are not guessed and remain null until reliable recapture or human correction.
- Acceptance PASS: extension build + 280/280 Node tests; focused salary/SearchPlan backend 169/169;
  frontend typecheck and production build PASS. `git diff --check` found no whitespace errors. No
  BOSS task, browser action, paid AI call, application, favorite or message ran. One live activation
  step remains: Reload unpacked extension 0.1.9, click its icon once on the logged-in BOSS search
  tab, close the popup, then start from the local console.

- Current (2026-08-30): the local console now has a resume-bound quick-search workflow whose only
  primary inputs are intended city and target job count (1–20). One click creates a fresh bounded
  SearchPlan task bound to the active resume, deterministically selects a Chinese cloud-role keyword
  from the local career strategy without AI/network access, and opens the existing explicit browser
  safety confirmation. Confirming starts the unchanged MV3 runner; collection still precedes paid
  model matching and human review. Custom keyword, task history, diagnostics and batch controls stay
  under advanced settings. Focused SearchPlan backend 80/80, frontend typecheck, 14/14 Node tests,
  production build, OpenAPI route check and local UI snapshot PASS. No BOSS task or AI call ran.

- Current (2026-08-30): local-console usability follow-up PASS. The job library now defaults to a
  compact keyword search with optional advanced filters and supports select-all for the current
  result set, clear-all and individual row selection. Cross-task review adds bounded select-all
  (at most 20 completed tasks) and clear-all. Selection alone performs no AI call, status change,
  application, favorite or message. Frontend typecheck, 14/14 Node tests and production build PASS.

- Current (2026-08-29): M5b bounded cross-task matching + unified human review is implemented and
  OFFLINE PASS. The localhost console can select an exact 1–20 set of completed SearchPlan tasks,
  deduplicate their existing `TaskCandidate` associations by canonical `job_id`, and generate a
  read-only plan bound to the task criteria/candidates, active resume, fast model and cache state.
  One separate confirmation permits exactly the displayed number of new calls, capped at 3 for the
  whole batch (and by `MAX_ANALYSES_PER_RUN` if lower); failures consume a slot and are not retried.
- M5b reuses `task_matching`/`job_matcher`/`JobAnalysis`; it adds no table, migration, Job,
  association, score, review event or persistence pipeline. The unified view is score sorted and
  flags missing/conflicting city, salary, experience and education as `待确认`. Matching never
  changes `Job.status` and never applies, favorites, messages or controls Chrome.
- M5b acceptance: focused M5a+M5b backend 22/22; full backend 1630 collected, 100% completed with
  the repository's 29 pre-existing browser-dependent skips; frontend typecheck, 14/14 Node tests
  and production build PASS. The first full run was invalidated only by Windows denying pytest's
  default temp directory; rerunning with an isolated repository temp directory passed. No real
  model call, backend restart, live matching run, browser action, commit or push occurred.
- Runtime activation PASS: with the user's continuation, only the existing JobAgent backend was
  restarted (old PID 23100 -> new PID 31464). `/health` reports application/database `ok` and the
  new loopback route produced a read-only real-data plan across 12 completed tasks: 25 unique jobs,
  4 cached, 21 pending, whole-batch new-call cap 3, and a 64-character stale-state fingerprint.
  No match run, model call, search task, Chrome/extension action or frontend restart occurred.
- Authorized M5b runtime batch (2026-08-30): the previously confirmed local-console run consumed
  exactly 3 fast-model calls, completed 3 analyses and reported 0 failures. No application,
  favorite, message, Chrome action or Job.status change occurred. This authorization is exhausted;
  no second paid batch was started. A later read-only health check found port 8000 unavailable,
  so the persisted plan could not be re-queried in this turn.
- Local-console UI acceptance PASS through the built-in local browser: the M5b panel loaded 12
  completed-task checkboxes, selected the exact 12-task set and generated the same free plan in the
  UI (25 unique / 4 cached / 21 pending / cap 3). It rendered 25 detail links, 4 `待确认` cached
  items and 21 `待分析` items; the cached review table is score sorted and exposes deterministic
  conflict reasons. The cost-confirmation button was verified present but was not clicked.
- Authorized scope record: `TASK.md` defines M5b as one cache-aware, deduplicated matching plan
  across a finite set of completed SearchTasks, followed by a unified human review view. Its runtime
  cost confirmation remains separate and caps new fast-model calls at 3 for the whole batch.
- M4g extension 0.1.7 is LIVE PASS (2026-08-29). The explicitly confirmed two-task batch ran
  #14 Shanghai / DevOps, then #15 Shanghai / SRE. Supervised session #34 recorded cap=1,
  extracted=1, scrolls=1 and stopped at 12:38:05Z; only afterward session #35 started at
  12:38:07Z with cap=1, extracted=1, scrolls=1. Both SearchTasks completed with no error/pause.
  Neither candidate satisfied canonical association/intake, so imported=0 for both; this is batch
  sequencing/budget evidence, not an import claim. Existing M4f live evidence remains the canonical
  intake proof. No paid AI, application, favorite, message, automatic retry, commit or push occurred.
- M4g 0.1.6 live run is PARTIAL/FAIL against the acceptance cap, not a product PASS. The batch did
  run serially: task #7 completed before #13 started, both bounded and error-free. However both
  supervised sessions recorded candidate_cap=3 because the batch reused the single-task default;
  #13 extracted 3 and canonically associated jobs #75 and #76. Those legitimate records are kept.
  0.1.7 separates the batch cap from the single-task cap and defaults it to 1, with the confirmed
  value locked in the stale-state snapshot. No automatic retry or task reset occurred.
- M4g pre-live review found 0.1.5 always selected the first five pending tasks, so the approved
  two-task/cap-1 acceptance could not be expressed safely. No batch was started. Extension 0.1.6
  adds an explicit 1–5 task-count control (default 2); the confirmation and stale-state check now
  bind to that exact count. A Reload was required at that point before live acceptance.
- With explicit permission, the existing FastAPI backend was restarted and its health/database check
  passed. Read-only planning selected the first two pending tasks for the eventual live confirmation:
  #7 Beijing / Infrastructure Engineer, then #13 Shanghai / Cloud Engineer, candidate cap 1 each.
  Neither task was started or mutated during this preparation.

- Current (2026-08-29): corrected M4g implementation is LIVE PASS in extension 0.1.7. One in-page
  confirmation may approve an exact ordered list of 1–5 existing pending SearchPlan tasks. The MV3
  worker validates the whole list before browser work, creates one foreground BOSS tab, and advances
  serially only after a normally completed task. Failure/cancel stops the batch; verification,
  foreground loss or explicit pause pauses it. A worker/browser restart never auto-resumes; explicit
  resume preserves the current task and remaining candidate/scroll budgets.
- M4g acceptance: extension build + 276/276 Node; focused console/runner suite 128/128; frontend
  production build + 14/14 Node. Review found no new backend persistence/dedup path, browser driver,
  paid AI, apply/favorite/message action, credential access or BOSS-tab close call. The corrected
  live batch completed under cap 1. No further Reload or acceptance run is required now.
- Current (2026-08-29): extension 0.1.4 LIVE PASS for automatic bounded collection from the local
  console. Normal Chrome -> JobAgent MV3 -> loopback FastAPI -> canonical intake is unchanged.
- This repair updates the unpacked extension package to 0.1.4. Startup now distinguishes a lost
  tab (`tab_lost`), wrong origin (`wrong_origin`), foreground loss (`not_foreground`), and bounded
  readiness failures (`stabilization_timeout:target_mismatch|content_unavailable|unsupported_page`).
  A lost startup tab ends the backend run and clears the local runner pointer instead of leaving a
  stopped pointer that blocks later work. Errors remain fixed codes; raw URLs/exceptions are not sent.
- The local console uses an in-page confirmation modal instead of native `window.confirm` for free
  start/resume. Cancel, Escape, or a task/state/keyword/cap change performs no browser action;
  confirmation issues exactly one command. Existing foreground, pause/cancel, 5-scroll and candidate
  budgets are unchanged. No paid matching, application, favorite or message path was added.
- Acceptance PASS: extension build + 269/269 Node; frontend production build + 12/12 Node.
  Actual React + MV3 fixture acceptance: 9/9 relevant scenarios (normal bounded collection,
  verification pause/cancel, five connection faults, and two stale-confirmation cases), zero skips.
  Evidence: `.tmp/startup-acceptance-20260829.xml` (8 pass + one test-only enum typo), followed by
  `.tmp/startup-stale-20260829.xml` (2/2 pass after correcting the test). Fixtures never contacted
  BOSS and are not a live PASS.
- The disappearance of the previous BOSS tab is still not attributed: project inspection found no
  `chrome.tabs.remove` or BOSS-tab close path. The only extension `window.close()` closes its own
  popup after a successful start/resume. Do not claim the site, JobAgent, or a browser tool caused it.
- Historical live evidence is unchanged: user-started task #11 (Beijing / infrastructure), cap 3,
  failed after about 4.60s with the older generic `stabilization_timeout`; zero visible/observed/new/
  duplicate/imported jobs and zero candidates. It was not reset or automatically retried.
- New 0.1.2 live evidence: connection/handshake PASS. The first Codex click was rejected because
  Chrome was not foreground and left task #4 pending with no tab. After a controlled focus action,
  the second click started #4 (Beijing / DevOps) and opened the exact search URL, then failed before
  scroll/intake with `stabilization_timeout:content_unavailable`; all counters stayed zero.
  Inspection found the popup already repairs a missing post-Reload content script by injecting the
  packaged scripts once, while the automatic runner did not. 0.1.3 adds that same exact-host,
  foreground-rechecked, single injected retry; the new regression was red on 0.1.2 and green on 0.1.3.
- New 0.1.3 live evidence: #5 (Beijing / SRE) opened the exact search URL, observed 15 unique jobs,
  scrolled once and imported/associated job #74 through canonical intake. It then failed on candidate
  two with `missing_job_identity`, while the preview actually represented an intake-incomplete job.
  0.1.4 treats that as one consumed candidate attempt, records `candidate_incomplete`, and continues
  within the original cap. A malformed duplicate without an id still fails terminally. The regression
  was red on 0.1.3 and green on 0.1.4; no AI call occurred.
- New 0.1.4 live evidence: user confirmed Chrome loaded 0.1.4, and #6 (Beijing / Platform Engineer,
  cap 3) ran from the local console without popup use or manual search/scroll. It completed after
  four controlled scroll rounds with observed=8, new=2, duplicate=6, no_new_rounds=3, last_error=null
  and paused_reason=null. No candidate met canonical intake in #6, so imported=0 and the task has no
  candidate associations; this is recorded as bounded collection/stop evidence, not an import claim.
  Combined with #5's unchanged canonical import path (job #74, SRE intern at ByteDance), the live
  chain now covers automatic navigation, repeated bounded scrolling, incremental discovery,
  duplicate filtering, detail processing, canonical intake and bounded stop. No CAPTCHA appeared.
- Previous 0.1.4 boundary: single-task live collection is accepted. M4g now adds only explicitly
  confirmed finite cross-task chaining; it does not add timers, scheduling or automatic restart.
  Login/verification and Chrome extension Reloads remain human/security actions.
- Backend/frontend local services were restored for acceptance. The live chain made one canonical
  import (#74) during #5; no paid AI, new permission/driver, application, favorite, message, commit or
  push occurred. Existing dirty worktree was preserved.
- Current (2026-08-30): authorized historical missing-salary maintenance is implemented offline.
  A read-only audit found 89 current missing-salary Jobs, all eligible canonical BOSS detail URLs.
  A persistent plan/run/item control plane reuses canonical intake and pauses after every 3 detail
  pages; login, verification and worker errors pause closed. Console + MV3 0.1.10 expose explicit
  create/start/resume/pause/cancel and counters. Acceptance: backend focused 32/32 after updating
  migration-head expectations; the preceding full suite reached 100% with only those 10 stale-head
  assertions failing. Extension build + focused executor/contract 127/127; frontend production build
  PASS. No real BOSS page, task start, screenshot, import or paid AI was used. Next action is Reload
  unpacked extension 0.1.10, refresh the console, then explicitly confirm the first 3-job live batch.
- Runtime preparation (2026-08-30): with explicit permission, the existing database upgraded in
  place from `0014_bounded_matching` to `0015_salary_backfill` and the loopback backend restarted
  healthy (PID 31800). The read-only plan reports 119 Jobs / 30 with salary / 89 missing and all 89
  eligible; no active backfill run exists. No plan, BOSS navigation, screenshot, import or AI call
  was started during preparation.
- Salary backfill live batch 1 (2026-08-30): user explicitly started run #1. It processed exactly
  the configured 3-job session cap and paused closed with `paused_reason=batch_limit`,
  `last_action=paused_batch_limit`, `last_error=null`, processed=3, updated=3, unavailable=0,
  failed=0, remaining=86. Read-only Job API verification confirmed canonical salary text was
  written for #27 (`16-19K`), #28 (`8-13K`) and #29 (`15-25K`). This is live PASS for the first
  bounded batch only; the next batch remains paused and requires an explicit resume.
- Salary backfill remaining-run authorization (2026-08-30): the user explicitly authorized this
  plan's remaining 86 jobs. Migration `0016_salary_cap` persists a run-scoped finite session cap;
  the default remains 3 for every other run. The console now offers one explicit “处理剩余全部”
  confirmation, while login/verification/user pause/worker errors still stop closed and successful
  salary writes still use canonical intake. A final-item boundary defect was found and fixed so a
  run whose remaining count exactly equals its cap completes instead of pausing again. Focused
  backend acceptance 34/34, full backend suite 100% (with the existing pytest cache warning), and
  frontend production build PASS. The real database migrated to `0016_salary_cap`; backend restarted
  healthy and run #1 is safely paused at 3/89 with `session_cap=86`, `last_action=remaining_authorized`.
  Codex Chrome control disconnected during the local-console refresh and no start was recorded.
  One human click on “继续处理剩余 86 个” is still required; do not click twice.
- M6 continuation review (2026-08-30, Codex direct): selected the standalone, query-stripped
  `https://www.zhipin.com/job_detail/<external_id>.html` page as the only admissible execution
  surface. Search-page split-panel execution remains rejected because the tab URL cannot prove the
  selected job identity exactly. No live BOSS action was attempted.
- The existing M6 approval service had an execution-boundary defect: `record_outcome()` accepted a
  caller that omitted observed page URL/external id. It now requires both fields for every outcome,
  so neither a local caller nor a stale backend record can become the browser witness. New loopback
  routes create/read one approval; validation/outcome additionally require an exact unpacked Chrome
  extension origin. There is no plural/batch endpoint. The independent
  `HUMAN_CONFIRMED_APPLY_ENABLED` flag defaults false and only exposes the gate; legacy
  `AUTO_APPLY` remains permanently false.
- M6 offline acceptance after the repair: focused backend 25/25; full backend suite PASS using a
  workspace-local pytest temp directory (the first full run's migration tests were blocked only by
  Windows denying pytest's default temp root). No frontend or extension execution UI was exposed,
  no real database migration/restart occurred, and no application/message/site action happened.
- M6 execution remains intentionally unreachable. Public reference code corroborates a provisional
  `.btn-startchat` control, but JobAgent still lacks a sanitized live DOM fixture and, more
  importantly, cannot prove which exact BOSS default greeting the click will send. Because M6 binds
  the exact `message_text`, guessing either would violate the confirmation snapshot. Next step is a
  bounded, read-only DOM/message-text evidence capture; only after that evidence can the final
  foreground confirmation overlay and one-click adapter be implemented and fixture-tested.
- Extension 0.1.12 now adds exactly that bounded evidence probe to the existing developer-mode
  “结构诊断”: it reports only the candidate control's selector/count/tag/classes/visible/disabled
  state plus the presence (never values) of redirect/data URL attributes. It explicitly reports the
  confirmed greeting text as unknown and contains no M6 execute/apply/submit message or click path.
  Extension TypeScript build PASS and Node fixture/contract suite 291/291. One manual Reload plus a
  developer-mode structure diagnostic on a standalone BOSS detail page is the next evidence step;
  it does not apply, message, navigate or upload the diagnostic.
- User supplied that sanitized live diagnostic (2026-08-30) from a standalone canonical
  `/job_detail/<external_id>.html` page. It proves the title/salary/detail anchors and two responsive
  `.btn-startchat` nodes: one visible+enabled and one hidden, both labelled “立即沟通”. The read-only
  0.1.13 probe/fixture now records `visible_usable_count` and `unique_visible_usable_control`, so hidden
  responsive duplicates do not create a false ambiguity while anything other than exactly one
  visible usable control still fails closed. No attribute values, click or message path was added.
- The same live diagnostic reports `confirmed_message_text=null`; therefore M6's exact-message
  snapshot requirement is still unsatisfied. The selector/identity evidence is sufficient, but the
  execution path remains intentionally unreachable until repository policy explicitly chooses how
  a site-controlled, pre-click-unobservable BOSS greeting can be confirmed. Extension build PASS;
  full extension suite 291/291 (also synchronized the stale manifest-version assertion).
- M6 unknown-dynamic-greeting amendment (2026-08-31, user authorized; extension 0.1.15): the first live attempt proved
  that a human-entered expected greeting is not a reliable description of what BOSS sends. The M6
  approval contract now accepts only `boss_dynamic_unverified` with an empty compatibility text
  marker; it rejects legacy `human_attested_unverified` approvals and every non-empty/AI-supplied
  greeting. The two per-job confirmations now prominently say the first greeting is unknown,
  BOSS-dynamic, and cannot be previewed, independently verified or controlled by JobAgent. No
  greeting body is requested, persisted or logged. Single-job identity, foreground, one-attempt,
  no-retry, no-bulk and no-follow-up-message boundaries are unchanged.
- Offline acceptance for this amendment: focused M6 backend 35/35; full backend suite exit 0;
  frontend production build + 23/23; extension build + 294/294; `git diff --check` PASS (line-ending
  warnings only). M6 remains feature-disabled in the normal backend and no second live application
  was started. Next action, only if the user wants another live acceptance: restart with the M6 flag,
  reload the rebuilt unpacked extension, and explicitly confirm one job while accepting the unknown
  dynamic greeting.
