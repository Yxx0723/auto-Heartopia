# 上游与已停用链路档案

2026-10-21 从 auto-Heartopia 活动目录归档的 Fhoe-Rail 星铁执行链、旧 WebUI、旧录制界面、模板图片及对应测试。来源、原作者和 GPL-3.0 许可沿用根目录 `UPSTREAM.md`、`LICENSE`；旧说明保存在本目录。

本目录不参与当前原生窗口的导入、打包、默认测试或 CI。不要把 legacy 加到 sys.path，不要在活动目录创建旧 import 的转发模块。

这是**原始源码档案，不是可直接运行的完整旧发行版**。旧代码仍保留原 import 路径，部分共用数据模块继续位于活动目录；复现旧版应在独立 checkout 中恢复对应基线与依赖，不能直接与当前程序混跑。旧测试保留作为历史证据，不因归档而宣称旧缺陷已修复。

`migration-manifest.json` 列出所有迁移/保留副本的原路径、归档路径和迁移前 SHA-256。图片与旧文件内容未重写。`CLAUDE.md` 与 requirements 文件的副本记录迁移前状态。保留归档不会减少仓库总下载体积或既有 Git 历史。

清单共记录 503 个本地保留文件，其中旧会话的 `webui/coordinate-monitor/coordinates.CT` 原本未纳入 Git，继续只在本地保留，条目标注 `publication: local_only`。公开仓库包含其余 502 个有哈希记录的文件；验证公开快照时跳过该本地条目，不需要生成或恢复它。
