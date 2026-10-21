# 当前离线回归

从仓库根目录运行 `python -m pytest -o addopts= -q`；默认收集 tests 与 personal_tests，legacy 明确排除。安装依赖使用 requirements-lock.txt，声明列表见 requirements-dev.txt。

conftest 不在收集阶段导入产品代码、改 cwd 或创建配置文件。各测试在临时目录运行；窗口、内存与输入用替身。需要真实图片的用例从 resources/monitor 和 tests/unit/fixtures 读取，不连接游戏。

架构约束在 test_architecture.py：依赖单向、输入仅在 drivers、cv2 仅在 vision、导入无副作用。test_offline_imports_and_locks.py 额外阻断 Windows / OpenCV 模块验证离线导入，并通过子进程检验文件锁冲突、正常释放及进程异常退出后的释放。

非 Windows 仅跳过原生输入/窗口 ABI 测试；其他纯数据、流程替身和持久化回归仍运行。Windows 上运行全部当前用例。旧星铁与 WebUI 回归归档到 legacy/tests、legacy/personal_tests，不作为当前产品的通过数。

GitHub 工作流另在 Windows 构建 EXE。新工作流未经远端实际运行前，只能报告“已配置”，不能报告跨平台 CI 已通过。
