# auto-Heartopia 工程规则

本文件描述当前《心动小镇》Windows 原生窗口程序。上游星铁规则原文已归档到 `legacy/CLAUDE.md`；其中的旧模块名、统计、1920×1080 假设和 CI 描述不适用于当前产品。

## 当前入口与目录

- `tools/coordinate_monitor.py`：装配原生 Tk 窗口与回放 runner；导入不启动窗口。
- `utils/core/`：坐标、点位、寻路、脱困预算、资源分类、体力与工具策略等数据逻辑。
- `utils/drivers/`：只读内存、Windows 输入/热键、文件持久化、时钟适配。
- `utils/vision/`：工具和食物面板识别，集中使用 OpenCV。
- `utils/flows/`：坐标反馈回放、临时巡查、采集及补给编排。
- `utils/ui/`：原生布局、绘图缓存、后台读取和回放状态。通过入口注入 runner，不直接导入 flows。
- 原生入口只装配会话和生命周期；布局/控件、地图绘制、路线操作分别在 `monitor_layout.py` / `monitor_widgets.py`、`monitor_painter.py`、`monitor_controller.py`，配色与分类在 `monitor_style.py`。
- 点位回放的会话、校准和跟点留在 `coordinate_player.py`；采集、脱困和补给执行分别在 `playback_collection.py`、`playback_recovery.py`、`playback_sustain.py`，内部异常在 `playback_signals.py`。
- `resources/monitor/`：版本配置和当前识别图片；EXE 中放到 `monitor/`。
- `packaging/HeartopiaMonitor.spec`：Windows 单文件 EXE 构建。
- `tools/build_ore_route.py`：独立维护工具，不因窗口入口未导入它就判为死代码。
- `tests/`、`personal_tests/`：当前离线回归；`pytest` 默认包含两者。
- `legacy/`：上游及已停用链路的源码、图片、旧测试和文档档案，不参与当前构建、导入或默认测试。

`schema.py`、`json_io.py` 仍被当前录制格式使用，不能随旧星铁执行器一起删除。`utils/config/` 的空包可保留作分层边界，旧配置实现已归档。

## 依赖和结构

依赖方向以 `tests/test_architecture.py` 为门禁：core/config 位于底层；drivers 不依赖 vision、ui 或 flows；vision 可依赖 drivers；ui 不依赖 flows；flows 可以装配下层。只有 drivers 直接依赖 Windows 输入/窗口库，只有 vision 使用 cv2。包 `__init__.py` 为空。

导入不能建窗口、启动线程、注册热键、读游戏或创建文件。原生设备和监听器在启动函数内延迟加载；离线替身测试不得依赖 Windows、桌面会话或真实游戏。不要将 `legacy/` 加到 sys.path，也不要添加指向旧模块的兼容 shim。

按职责拆分，不以减少行数或增加转发层为目标。大文件拆分另行验证，不与资源迁移、行为修改混为一次重构。

职责类共享主窗口/回放会话的同一个 self，不独立启动线程或持有第二套暂停、按键、采集进度。没有动态方法查找或逐方法转发；依赖公共样式/策略，不反向导入组装入口。新的结构门禁检查这一方向。涉及状态所有权调整时另加真实行为回归。

## 时间、输入和退出

1. 流程使用可注入的 `utils/drivers/clock.py::PreciseClock` 或替身时钟。轮询必须让出 CPU；不得靠固定 sleep 猜测游戏已完成某个状态。
2. 循环必须有停止条件或时间预算；持续重试也必须响应 F9、F6、失焦和世界切换。
3. 按键成对释放，获取输入资源和按下动作在 try 内；异常、停止与退出均 finally 释放。不要用全局无条件 key-up 干扰用户按住的修饰键。
4. 回放仅绑定已连接的游戏 PID，切出或尺寸变化暂停。屏幕识别依据实际客户区尺寸与 DPI，不能恢复上游固定 1920×1080 假设。
5. 单测仅用替身。不自动在真实游戏中采集、驾驶或消耗物品；实机输入验证使用用户明确启动的 F6，F9 可停止。

## 内存与资源证据

- 游戏内存只读，不写坐标、不注入代码。模块指纹、对象类型、归属、代数、引用和世界上下文必须核对；未知版本拒绝使用旧布局。
- 换进程、换世界或失效引用不能继续复用旧地址。`verified_after_scene_change: false` 不能改成 true，除非有真实换场景验证记录。
- 附着载具时可显示经校验的根实体位置；脚步回放仍要求下车，不把载具锚点导出成直接 XYZ 地址。
- 固定资源定义可缓存，动态动物实时更新。位置线索、当前同步记录、已加载可交互对象必须区分；缓存不能作为当前可采集状态。
- 橡木/萤石巡查是临时计划，不改用户路线；巡查途中不按 F，实际对象到点确认后才采集。未加载或一轮未找到不等于全图没有。
- 道路/水域参考轮廓不是完整通行证明，攻略区域名不能直接猜成 XYZ。新增可通行豁口必须有坐标与通行证据。
- F 已发送、资源状态变化、背包实际增加是不同证据，报告不得混淆。普通点位等待仍是定时等待，不能宣称已经识别采集完成。

## 持久化和阈值

点位使用严格 schema、进程锁和原子替换；Windows 用 msvcrt，POSIX 用 flock，均非阻塞，锁冲突明确报错。删除/清空保留备份及过期编辑校验。构建、测试和部署不能覆盖用户 `点位/`、`地图缓存/`、`回放记录/`。

阈值集中在 `utils/core/thresholds.py`。注明来源、单位，以及属于实测边界、保守余量还是运行预算。没有正负样本时写“待测”，不得编造匹配分数或余量。改常量同步更新 golden contract 并验证实际行为。

捕获能处理的异常；不要吞 BaseException。网络操作沿用用户要求的本机代理，禁止失败后自动直连。凭据不得写进源码、日志或文档。

## 验证与构建

```powershell
python -m pip install -r requirements-lock.txt
python -m pytest -o addopts= -q
python -m PyInstaller --noconfirm packaging/HeartopiaMonitor.spec
```

`.github/workflows/run.yml` 在 Windows、Ubuntu、macOS 上运行离线回归，Windows 另做 EXE 构建。仅原生输入/窗口 ABI 测试在非 Windows 平台明确跳过；这不表示游戏控制支持 macOS/Linux。GitHub 上尚未执行的工作流不能报告成已通过。

修改前跑相关测试，行为修改补复现；通过后跑必要回归。架构名单只减不增，不能为让测试变绿放宽断言。格式化器/校验器的旧星铁地图命令已归档，不接入当前 CI。

报告写清修改、真实执行的检查及结果、未验证项和文件位置。替身测试、只读连接检查、实际游戏输入分开记录；历史回归数字不可冒充当前结果。不要将测试数下降或文件移动描述成性能提升。
