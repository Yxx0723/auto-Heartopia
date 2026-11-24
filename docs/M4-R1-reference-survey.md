# M4-R1 TapTap 参考地图调查

2026-11-24。执行本轮前基线 `8c68504`。本文件只记录**实测证据**，未做任何程序改动。

## 1. 入口与渲染方式

指定地址 `https://www.taptap.cn/app-map/45213?map=10&area=1118&…` 打开后，外层页面内嵌一个 iframe：

```
https://www.taptap.cn/app-map/detail/45213?map=10&area=1118&zoom=7&…
```

外层 `document.body.innerText` 为零，全部内容在内层。内层渲染器为 **Mapbox GL JS 1.13.3**，画布 `mapboxgl-canvas` 的 CSS 尺寸 1440×635、`width/height` 属性 2880×1270（DPR=2）。

后台标签页的 `requestAnimationFrame` 被浏览器节流，Mapbox 不会绘制，截图只剩 `bg_color`。**取证不能用整页截图，必须直接取瓦片。**

## 2. 底图配置（来自官方接口）

`GET https://www.taptap.cn/map/index/index?game_id=45213` 返回 148 KB JSON，`data.maps` 共 2 张图。`map=10`（心动小镇 主地图）的 `tile_sets` 原文：

```json
{"name":"main","path":"xz/tiles/main_v1","type":"raster",
 "pattern":"/{z}/{x}_{y}","min_zoom":8,"max_zoom":11,
 "auto_webp":1,"extension":"jpg","tileSize":250,
 "bounds":[-1.4,-1.4,1.4,1.4],
 "lng":0,"lat":0,"offset_revise":{"x":0,"y":0,"zoom":0}}
```

同图元信息：`initial_zoom=9`、`start_lat=-0.0005182479`、`start_lng=0.0007558873`、`bg_color=#6c9fb0`。

另一张 `map=118`（鲸落峡谷）使用 `xiaozhen/tiles/小镇-鲸落峡谷切图`，`tileSize=256`、`extension=png`，与本轮无关。

## 3. 瓦片实测

模板：`https://assets.tapimg.com/cupid-apps/game-map/xz/tiles/main_v1/{z}/{x}_{y}.jpg?x-oss-process=image/format,webp/quality,q_85`

| 层级 | 网格 | 瓦片数 | 成图分辨率 | 平均字节 |
| --- | --- | --- | --- | --- |
| z=8 | x 127..128, y 127..128 | 4 | 500×500 | — |
| z=11 | x 1016..1031, y 1016..1031 | 256 | **4000×4000** | 22,392（最小 3,081 / 最大 48,892） |

单块瓦片 250×250 JPEG。z=11 为官方 `max_zoom`，即**原生最高细节为 4000×4000**。已实测 256 块全部 200 且体积正常，无缺块；拼接后无接缝。

## 4. 底图与标记的分离方式

- 底图：Mapbox GL 的 raster 图层，走 `mapboxgl-canvas`，独立于 DOM。
- 标记：独立 DOM `<img>`，来自 `img.tapimg.com/market/images/*.png`（`pointIcon`、`underPointIcon`、`underEntryIcon` 等在 `theme_option` 中逐项声明），叠在画布之上。

结论：**底图与标记天然分离**，可以只取底图，不影响标记。

## 5. 覆盖范围与地貌细节

底图完整覆盖整张游戏地图：主岛、北侧砂岩山地、东侧森林、中央草原与城镇、海岸与离岸小岛。1～12 号住宅地皮编号**已直接画在底图里**。

山地内部不是平涂：可见岩面纹理、坡面明暗过渡、山脊线与冲沟、以及台面上的青绿色水潭。草原有等高走向的柔和明暗与底纹。海岸有带状滩涂与深浅水过渡。这些是**二维手绘地貌阴影，不是真实海拔**——按任务要求，程序里也不得据此声明海拔。

## 6. 素材使用权限

未找到任何公开发布的使用许可。瓦片由 TapTap 的 CDN（`assets.tapimg.com`）托管，画面为游戏《心动小镇》官方地图美术。公开可访问 **不等于** 允许再分发。

因此本轮的处理是：**运行时按需拉取 + 本地缓存，不入库、不随程序分发**。仓库只提交抓取与校准代码，`.gitignore` 排除缓存目录。删除缓存即回到无底图状态。

## 7. 世界坐标转换关系

底图不自带世界坐标元数据，转换关系由**实测标定**得到。

方法：把项目已有的世界水面多边形（`MAP_WATER_AREAS` 58 个 + 草原湖）按不同尺度栅格化到 500×500，用 FFT 互相关求最佳位移，取交集/并集比（IoU）最大的尺度，再四分之一步长细化。

结果（`outputs/basemap/calibration.json`）：

| 量 | 值 |
| --- | --- |
| 尺度 | 0.76 px/世界单位（500 栅格） |
| 位移 | dx 6、dy −13（500 栅格） |
| 水面 IoU | 0.522 |
| 换算到 4000px 原图 | 6.08 px/单位，dx 48、dy −104 |

正算公式（`x,z` 为游戏世界坐标，原点取水面多边形包围盒中心 `(0.97, 23.87)`）：

```
X_img = 2000 + (x - 0.97) * 6.08 + 48
Y_img = 2000 - (z - 23.87) * 6.08 - 104
```

**校验方式不只看 IoU**：把世界水面轮廓、参考道路、木质平台按上式画回底图（`outputs/basemap/alignment-check.png`），逐项目视核对。海岸线、湖泊、涵洞位置吻合；环状主路与画中道路重合；草原湖黄色轮廓落在画中池塘上；南侧木质平台落在画中平台上。残差目测在数像素量级（1200px 图），主要来自底图艺术化绘画本身与游戏碰撞数据的不完全一致，而非转换错误。

## 8. 与 M4 的关系

M4 已验收的部分保留不动：高度数据调查报告、山壁高度分类模块（`utils/core/terrain_height.py`）、对应单测、`MapDrawCache` 的缝合与预展平优化。

M4 未通过的原因在本轮对比图里一目了然：M3 底图是三块平涂色块（`_TERRAIN_TINTS` 的 upland/forest/town），没有纹理、没有明暗、没有海岸细节。这不是调色能解决的，是底图本身缺内容。

按任务要求，山壁图层降级为独立辅助图层，不再作为地势呈现的主要成果。

## 9. 产出文件

| 文件 | 内容 |
| --- | --- |
| `outputs/basemap/basemap-z11.png` | 4000×4000 拼接底图（本地缓存，不入库） |
| `outputs/basemap/calibration.json` | 标定结果 |
| `outputs/basemap/alignment-check.png` | 世界轮廓叠加校验 |
| `outputs/basemap/compare-m3-vs-taptap.png` | M3 与参考底图整图对比 |
| `outputs/basemap/region-*.png` | 北部山地 / 草原湖 / 东侧森林 / 中央城镇四区裁切 |

工具：`tools/fetch_base_map.py`（抓取拼接）、`tools/calibrate_base_map.py`（标定）、`tools/verify_base_map_alignment.py`（叠加校验）、`tools/render_reference_sheets.py`（对比图）。
