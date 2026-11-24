# M1 住宅地皮地图显示

2026-11-24；基线 0796a62，发布分支 feature/auto-fishing-monitor。

## 行为与范围

地图分类新增「住宅地皮」，默认开启。读取主地图 1～12 号住宅的完整建造用地外边界，以浅金色空心轮廓显示，编号置于各自边界内。此处显示的是整个住宅建造用地，不是其中已解锁的小分区，不是地形、围栏外观网格，也不是入口或扩大后的感应区。

图层独立管理 Canvas 图形；不将地皮送入 objects、colliders、A*、公共侧巡查保护区或烹饪/采集流程。专用 NavigationSceneMap 不构造或读取 HousePlotReader。异常、编号不全、未知源码版本及场景切换均清除该显示数据，不能从旧场景补画。缩放、视口移动与窗口尺寸变化时重新投影，文字保持固定屏幕字号。

## 数据来源和真实性证据

1. 复用 HouseEntranceReader 对当前 GameLevel_Main、ScriptEngine 注册配置、MapFieldPointsAsset 列表版本及场景身份的校验。显示编号为 fieldNo，地块身份为 fieldID；二者不能互换。
2. 只读核验已加载的 XDTDataAndProtocol：FieldProtocolManager.Max/Min 直接消费 CraftFieldConfig 的左右前后边界；TryGetFieldTransformComponentData 按 fieldID 查找 MapFieldPointAsset.pos/rot。此转换不使用 playerPos、playerSquarePos、mailPos 或 foreyardAreaId。
3. HomelandComponent.InitFieldBounds 将同一 FieldProtocolManager.Center/Size 写入住宅 fieldLevelObject 的用地范围与 FieldComponent.LocalBounds。现场加载的 2、3、4 号住宅均验证了对应局部 XZ 边界。
4. 当前实机读到左右各 12、前 9、后 21，即 24×30；这些数值不是绘图常量，程序从运行时配置读取。住宅朝向、位置同样来自当前配置，绝不按入口位置推算矩形。
5. StaticFieldTarget 的 12 个实际地块对象与主地图住宅配置逐一精确关联，作为第二条现场几何核对链；12 块世界角点最大误差约 4.08e-6 个游戏单位。它们的进入/离开感应功能不充当用地边界的权威来源。
6. 排除两个容易混淆的来源：foreyardAreaId 关联的门前触发多边形；FieldComponent.RefreshLevelObjectRect 额外扩展 4 个单位的感应区域。两者均不参与本图层的边界计算。

校验的已加载程序集：

| 程序集 | SHA-256 |
|---|---|
| XDTDataAndProtocol | 78f58918e9760f65991d625090162c22ab9667e4d3f6868248e8b6c19eaaa0ba |
| XDTLevelAndEntity | 4b916f587ca0a44ae9700decb81ca0388591a725f1b3e404ea21ae10978675f2 |

生产读取器会对 XDTDataAndProtocol 执行指纹校验；未知版本不显示边界。没有将程序集、解编译源码、进程地址或玩家身份提交仓库。

编号与身份对应：

| 住宅编号 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| fieldID | 11 | 9 | 7 | 5 | 3 | 1 | 2 | 4 | 6 | 8 | 10 | 12 |

## 验证及复现

专项与相邻地图、入口、缓存、后台读取器、导航几何回归：171 项通过。

```powershell
python -m pytest -o addopts= -q tests/unit/test_house_plots.py tests/unit/test_house_plot_layer.py tests/unit/test_house_entrances.py tests/unit/test_native_coordinate_monitor.py tests/unit/test_scene_map_worker.py tests/unit/test_map_draw_cache.py tests/unit/test_scene_display_cache.py tests/unit/test_guidance_planar_scene.py tests/unit/test_scene_float_batch.py
python tools/check_house_plots.py --output m1-live-verification.json
python tools/check_house_plot_window.py m1-live-verification.json m1-map.png
```

后两条在 Windows 已进入主地图的游戏进程上只读运行；最后一条需要交互桌面会话。没有游戏输入或内存写入。

Windows 验证包括：12 个编号唯一、12 块世界轮廓与实际游戏地块对象匹配、已加载住宅 LocalBounds 一致、真实 Tk Canvas 的缩放/平移/尺寸变化及清除、正式 CoordinateWindow 的默认筛选、全部编号在地图内、分类切换和场景清理。验收预览镜头居中于地皮总范围，仅改变测试窗口视角；不代表移动了游戏角色。

实机读取：首次约 117.56 ms，20 次缓存读取中位约 2.58 ms、最大约 3.32 ms；整场景发布成功携带 12 块地皮。导航读取绕过新增图层数据。上述时间为当前电脑现场样本，并非跨设备性能保证。

现场数据：[m1-house-plots-20261124.json](evidence/m1-house-plots-20261124.json)。正式地图窗口截图：[m1-house-plots-map-20261124.png](evidence/m1-house-plots-map-20261124.png)。

## 基线回归缺口

最终全量测试：3992 通过、13 失败、27 预期失败，另有 2 个子测试通过，耗时 199.23 秒。不能称为全量通过。对失败节点使用未修改的 0796a62 基线与 M1 候选进行同环境复核，仍失败的 13 个节点完全一致，新增模块没有增加架构违规项。主要涉及既有钓鱼模块架构登记、阈值登记、旧界面布局及刷新测试设置。这些流程不属于 M1，未修改它们来掩盖结果。

M1 专项验收与实机对照已经通过；全量基线红项仍需另行处理。发布记录应明确保留这一缺口。
