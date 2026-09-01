# Windows 商用发行状态

## 当前已经成立（P2C-A 未签名安装候选）

- `scripts/build-windows-portable.ps1` 使用锁定的 PyInstaller 6.22.2 构建 Windows x64 onedir，
  正常运行不依赖系统 Python、Node 或源码目录。
- 包内含编译前端、数据库迁移、中性策略模板和精简的 MV3 扩展目录；不含浏览器二进制、登录
  会话、开发者配置或用户数据。
- `scripts/release_audit.py` 要求关键文件齐全，并拒绝数据库、`.env`、日志、uploads、浏览器资料、
  PID 状态和 editable `direct_url.json`；通过后写出逐文件 SHA-256 清单。
- 构建同时生成 ZIP 与 `SHA256SUMS.txt`。GitHub 工作流可在人工触发或 `v*` tag 时从干净 checkout
  构建并保留 14 天的 unsigned artifact。
- `JobAgent.exe` 默认使用 `%LOCALAPPDATA%\JobAgent`，首次创建中性策略和数据库；`--doctor`
  只输出非敏感状态。重复启动识别同一进程，`Stop-JobAgent.cmd` 在 PID、可执行文件路径和进程
  创建时间全部吻合后才停止。
- 版本变化且已有数据库时，启动前用 SQLite backup API 备份数据库并复制策略。新运行时未达到
  health 时自动恢复二者，不推进版本标记。
- `scripts/build-windows-installer.ps1` 用固定 Inno Setup 7.1.0 将已审计 bundle 编译为单个
  **per-user / non-admin** 安装 EXE；稳定 AppId 支持覆盖升级，提供开始菜单、可选桌面快捷方式和
  Windows 卸载注册。
- 卸载默认保留 `%LOCALAPPDATA%\JobAgent`。交互卸载只有在独立警告中再次选择“是”才删除；
  静默卸载始终保留。安装器 manifest 明确标记 `NotSigned`、`development_candidate` 和
  `commercial_distribution_ready=false`。
- GitHub installer workflow 会从同一已审计 payload 构建两个版本，并在上传前于一次性 Windows runner
  实际执行安装、无 Python/Node runtime PATH 启动、健康/前端检查、同 AppId 升级、静默卸载保留数据、
  注册项/程序清理与端口释放。脚本遇到已有 JobAgent 安装、数据或卸载注册会 fail closed，且拒绝在
  GitHub Actions runner 之外运行。

## 构建候选包

构建机需要 Windows、Python 3.11+、Node 22+；最终用户不需要这些工具：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -e ".[packaging]"
cd ..
.\scripts\build-windows-portable.ps1 -Version 0.1.0
.\scripts\build-windows-installer.ps1 -Version 0.1.0
```

输出位于 gitignored 的 `.artifacts/`。解压 ZIP 后先核对 SHA-256，阅读 `README-FIRST.txt`，再运行
`JobAgent.exe`。Chrome 扩展仍需在 `chrome://extensions` 选择包内 `extension/` 进行开发者模式
加载；停止时运行 `Stop-JobAgent.cmd`。用户数据不会写回解压目录。

## 本机与远程隔离验收

两个连续候选版本已在仓库内隔离目录完成：非管理员安装、冻结 EXE doctor、health/database ok、
根页面 HTTP 200、同 AppId 升级、数据库哈希不变、静默卸载保留数据、程序目录/卸载注册清理和
端口释放全部 PASS。没有启动 Chrome/BOSS，也没有读取或修改真实用户数据。

远程 run `33474909477` 把同一生命周期作为可重复发布门禁执行并通过。最终 artifact 的实际 SHA-256
与 manifest 完全一致；运行时 PATH 已移除 Python/Node。该 runner 仍安装了构建工具，因此这里只能证明
冻结 EXE 不从 PATH 解析它们，不能替代真正无开发工具链的独立 Windows VM。

## 还没有完成，不能对外宣称

1. 候选 EXE/ZIP 尚未用可信证书签名，Windows 会显示未知发布者。
2. 当前 Inno Setup 编译器输出为 `Non-commercial use only`。正式商用分发前必须确认并满足适用的
   Inno Setup 商业许可；本阶段没有购买、读取或伪造许可证。
3. 扩展仍是开发者模式加载，尚无 Chrome Web Store 或受控企业分发。
4. 虽然 ZIP 解压后的 EXE 已独立运行，仍需在无 Python、Node、源码和开发缓存的干净 Windows VM
   完成首次安装、升级、失败回滚和交互式卸载清除测试；本机没有点击会删除真实默认数据目录的选项。
5. 还没有正式 GitHub Release、签名校验链、SBOM 和最终用户支持策略。

下一阶段 P2C-B 应完成签名、商业许可确认和独立干净 VM 矩阵。以上
证据齐全前只能发布“unsigned test candidate”，不能叫正式商用安装包。
