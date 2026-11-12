# 参与贡献

本仓库维护《心动小镇》Windows 原生桌面版，当前入口为 `tools/coordinate_monitor.py`。建议先阅读 [README](README.md)、[使用说明](docs/使用说明.md) 与 [工程规则](CLAUDE.md)。`fhoe.py`、`webui/` 中保留有上游兼容代码，不要将历史功能当成当前桌面版发布功能。

## 开发环境

- Windows 10/11；安装 Python（CI 使用 Python 3.13）。
- 在项目根目录创建 `.venv`，使用仓库中的锁定依赖复现环境。
- 不需要启动游戏即可进行静态检查和大部分模拟测试；真实内存读取及输入行为必须在实际游戏环境中单独验收。

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m compileall -q utils tools tests fhoe.py get_width.py
.\.venv\Scripts\python.exe -m pytest -q tests/unit/test_cooking.py
.\.venv\Scripts\python.exe -m pytest -q tests
```

若在锁定依赖以外安装了新版本，请在 PR 中明确 Python 版本和依赖变化。CI 只执行基础语法检查与烹饪状态机回归测试；完整套件及实机验收是额外的验证步骤，不能用绿色 CI 代替。

## 修改原则

1. **小范围变更。** 一次 PR 优先处理一个问题；列出修改的文件和预期行为，不顺带重构不相关模块。
2. **保持分层。** `utils/core/` 尽量使用无副作用的纯逻辑；`drivers/` 封装进程读数与键鼠交互；`flows/` 编排流程；`ui/` 处理 Tk 状态。参阅 `CLAUDE.md` 的架构约束。
3. **先验证再输入。** 无法确认游戏窗口、数据归属、任务状态或库存时，不应盲目重试会消耗材料的操作；停止和暂停应及时释放输入。
4. **保留可追踪证据。** 发生异常时优先提供可复现步骤、预期与实际结果、脱敏后的日志和相关测试。不要把仅在模拟测试中通过的结果写成“实机已通过”。
5. **不提交本地数据。** 不要提交游戏路径、个人路线、背包/角色原始快照、日志、内存地址、令牌、账号资料、未公开的 EXE 或任何其他敏感信息。上传截图之前请移除用户名、UID 等信息。
6. **保留来源与许可证。** 遵循根目录 `LICENSE` (GPL-3.0) 和 `UPSTREAM.md` 的署名与来源要求。

## Pull Request 检查

- 说明问题与修复逻辑，并关联 Issue（如有）。
- 列出受影响模块、运行的测试命令及结果；无法运行的项目须明确说明。
- 对需要游戏验证的改动，单独记录游戏版本、场景、复现与验收步骤。不要提交私人验收日志或本地可执行文件。
- 若涉及运行方式或操作行为，同时更新 `README.md`、`docs/使用说明.md` 或 `CHANGELOG.md` 中相关内容。

问题反馈可以使用仓库的 [Issue 模板](https://github.com/Yxx0723/auto-Heartopia/issues/new/choose)。
