# Windows 商用发行状态

## 当前已经成立（P2B 未签名候选）

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

## 构建候选包

构建机需要 Windows、Python 3.11+、Node 22+；最终用户不需要这些工具：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -e ".[packaging]"
cd ..
.\scripts\build-windows-portable.ps1 -Version 0.1.0
```

输出位于 gitignored 的 `.artifacts/`。解压 ZIP 后先核对 SHA-256，阅读 `README-FIRST.txt`，再运行
`JobAgent.exe`。Chrome 扩展仍需在 `chrome://extensions` 选择包内 `extension/` 进行开发者模式
加载；停止时运行 `Stop-JobAgent.cmd`。用户数据不会写回解压目录。

## 还没有完成，不能对外宣称

1. 候选 EXE/ZIP 尚未用可信证书签名，Windows 会显示未知发布者。
2. 尚无安装器、开始菜单/桌面快捷方式、升级下载器，以及卸载时“保留或清除数据”的界面。
3. 扩展仍是开发者模式加载，尚无 Chrome Web Store 或受控企业分发。
4. 虽然 ZIP 解压后的 EXE 已独立运行，仍需在无 Python、Node、源码和开发缓存的干净 Windows VM
   完成首次安装、升级、失败回滚、卸载保留/清除数据验收。
5. 还没有正式 GitHub Release、签名校验链、SBOM 和最终用户支持策略。

下一阶段 P2C 应建立安装器/卸载器和独立干净 VM 矩阵；拿到签名证书后再加 Authenticode。以上
证据齐全前只能发布“unsigned test candidate”，不能叫正式商用安装包。
