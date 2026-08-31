# Windows 商用发行状态

## 当前已经成立

- 开发模式保持原有 FastAPI + Vite 双进程，个人现有数据路径不变。
- 设置 `JOBAGENT_DATA_DIR` 后，数据库、策略、简历和浏览器资料进入该独立目录；显式覆盖时也从
  该目录读取 `.env`，不会意外吸收源码仓库里的开发者配置。
- 冻结运行时默认使用 `%LOCALAPPDATA%\JobAgent`，首次启动只在不存在时复制中性的
  `config/career_strategy.yaml`，绝不覆盖既有策略。
- `JOBAGENT_SERVE_FRONTEND=true` 时，FastAPI 直接提供 `frontend/dist/index.html` 与 assets，
  因而最终发行只需一个本地监听进程。
- `python -m app.cli doctor [--json]` 只报告组件状态和是否配置 AI，不打印 Key、简历、岗位或正文。

## 当前如何验证单进程模式

这是开发/验收入口，不是面向最终用户的安装步骤：

```powershell
cd frontend
npm run build
```

然后双击 `Start-JobAgent-Commercial.cmd`。它使用现有后端虚拟环境，监听 `127.0.0.1:8000`，
打开首次设置页，并把用户数据写到 `%LOCALAPPDATA%\JobAgent`。用 `Stop-JobAgent.cmd` 停止。

## 还没有完成，不能对外宣称

1. 尚无包含 Python 运行时的签名安装器或便携包；全新电脑仍不能直接双击安装。
2. 尚无正式升级器；必须实现升级前数据库/策略备份、迁移失败回滚和保留/清除数据选择。
3. Chrome 扩展仍是开发者模式加载，尚无商店发布或受控企业分发方案。
4. 尚未在无 Python、无 Node、无源码的干净 Windows 用户环境做安装与卸载验收。
5. 尚未建立代码签名、版本化产物、校验和及发布说明流水线。

下一阶段 P2B 应先产出可重复构建的 Windows artifact，再在隔离干净环境验证安装、首次设置、
升级备份与卸载保留数据；只有这些证据齐全后才能称为“任何人可安装”。
