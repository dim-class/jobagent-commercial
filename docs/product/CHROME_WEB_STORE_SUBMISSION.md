# Chrome Web Store 提交准备（P2C-A3）

状态：**仅为提交候选，尚未上传、审核或发布。**

## 单一用途

> 将用户在正常 Chrome 中明确启动的 BOSS 职位搜索与当前页面职位信息，安全地交给同一台电脑上的本地 JobAgent，用于有界采集、去重、整理和人工复核。

扩展不是独立产品，必须配合本地 JobAgent。它不做后台监控、批量投递、自动消息、聊天扫描、CAPTCHA 绕过、凭据读取或遥测。

## 商店 Manifest 与开发 Manifest

`extension/manifest.json` 继续服务本地开发，因此包含 Vite 控制台端口 5173。`scripts/build-extension-store.py` 在内存中派生商店 Manifest：

- 删除全部 5173 host permission 和 content-script match；
- 只保留 `activeTab`、`scripting`、`storage`；
- 只保留 BOSS 与本地 8000 host；
- 添加 16/32/48/128 PNG 图标；
- 使用精确文件白名单构建可复现 ZIP。

任何意外权限、文件、远程脚本、动态执行原语、调试器能力或已暂停 M7 聊天扫描标记都会令构建失败。

## 权限理由（商店 Dashboard 可直接改写使用）

### `activeTab`

用户明确启动职位检测/有界搜索后，薪资有时由特殊字体渲染，普通 DOM 无法可靠读取。扩展只对当前前台标签页的薪资区域进行一次性截图裁剪，并在操作前后核对标签页、窗口前台状态和职位身份。它不截图其他网站或整页保存图片。

### `scripting`

扩展重载或升级后，用户已经打开的 BOSS 标签页可能没有当前版本的内容脚本。该权限仅用于把扩展包内自带的脚本重新注入一个明确、前台、`www.zhipin.com` 标签页；不下载或执行远程代码。

### `storage`

使用 `chrome.storage.session` 保存当前有界任务的临时指针、滚动/候选预算、恢复状态和回填状态。它不是用户岗位数据库，不存密码、Cookie、令牌或聊天正文。

### `https://www.zhipin.com/*`

核心功能需要读取用户当前正常 Chrome 中已经渲染的 BOSS 职位卡和详情，并执行用户从本地控制台启动的有界导航/滚动。登录或验证页面会停止；不会处理凭据或绕过验证。

### `http://127.0.0.1:8000/*` 与 `http://localhost:8000/*`

连接同一台电脑上的 JobAgent 配套服务与编译后的本地控制台，用于任务命令、结构化职位结果和状态计数。不是互联网开发者服务器。

## 数据披露建议

在 Dashboard 的 Privacy practices 中，至少按实际功能披露：

- **Website content**：职位卡、JD、公司、薪资等当前页面内容；
- **Web history / browsing activity（如 Dashboard 将职位 URL 归入此类）**：仅当前 BOSS 职位规范化 URL 与职位 ID；
- **Images or videos（如 Dashboard 要求）**：只处理当前职位薪资区域的短生命周期截图裁剪；
- 数据用途：app functionality；
- 不出售数据、不用于广告、不用于与单一用途无关的行为；
- 数据只传到用户设备上的 loopback JobAgent；可选第三方 AI 由本地应用另行授权，不由扩展直接调用。

提交人不得为了减少披露而隐瞒本地处理的数据。正式勾选项应以届时 Dashboard 的最新分类和法律审核为准。

## 商店文案草案

### 名称

`JobAgent BOSS Detector`

### 简短说明

`连接本地 JobAgent，在正常 Chrome 中执行用户启动的有界 BOSS 职位搜索、采集与人工复核。`

### 详细说明

JobAgent BOSS Detector 是本地 JobAgent 的浏览器配套组件。用户在本地控制台选择城市、岗位方向和数量后，扩展在当前正常 Chrome 的 BOSS 标签页中执行有界搜索，读取已渲染职位信息，并把结构化结果交给本机 JobAgent 去重和整理。

主要特性：

- 复用用户当前正常 Chrome 与已登录会话；
- 有界滚动、增量职位发现和规范化职位身份；
- 薪资 DOM 读取失败时，只处理当前职位薪资区域截图；
- 登录/验证/CAPTCHA 时立即停止；
- 不扫描招聘聊天，不读取 Cookie/令牌，不使用 stealth，不后台运行；
- 不批量投递，不自动发送招聘消息。

需要先在 Windows 安装并启动本地 JobAgent。

## 构建与验收

```powershell
cd F:\jobagent\bossagent1.0\extension
npm ci
npm run build
npm test
cd ..
python scripts\build-extension-store.py
```

输出位于 `.artifacts/chrome-web-store/`：

- `JobAgent-BOSS-Detector-<version>.zip`
- `EXTENSION-SHA256SUMS.txt`
- `EXTENSION-STORE-MANIFEST.json`

GitHub workflow `chrome-web-store-candidate.yml` 只构建和上传 CI artifact，不连接商店 API。

## 仍需产品所有者手工完成

1. 审核隐私政策并填写公开支持/隐私邮箱。
2. 把隐私政策发布到稳定的公开 HTTPS URL。
3. 建立并验证 Chrome Web Store 开发者账号，阅读并接受届时适用的协议/费用。
4. 从无个人信息的演示数据环境截取至少一张真实产品截图；不得使用含真实求职、邮箱、聊天或账户信息的旧截图。
5. 准备 440×280 小型宣传图等 Dashboard 当时要求的素材。
6. 手工上传 ZIP，完整填写单一用途、权限理由和数据披露。
7. 检查预览、提交人工审核；审核通过前不得写“已上架”或“商用可用”。
8. 上架后再做一次干净 Chrome 配置文件的安装/升级/卸载验收。

本里程碑不替代上述人工责任，也不保存任何商店凭据。
