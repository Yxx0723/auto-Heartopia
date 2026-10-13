# auto-Heartopia

《心动小镇》Windows 原生坐标观测与路线回放工具。实时读取本地角色 XYZ，手动添加采集点，按保存顺序行走、按 F 采集并等待。使用独立窗口，无需浏览器。

## 功能

- 实时 XYZ、XZ 移动轨迹、游戏连接状态与当前坐标地址。
- 添加当前点位，多条命名路线，新建、另存为、改名、删除和清空当前路线点位。
- 按点位顺序回放，支持完整路线或最近点接入、1～999 轮循环。
- 到点短按 F，支持默认等待及逐点等待时间；暂停保留当前轮次、点位与剩余等待。
- 每次遇阻先长按空格大跳，再后退和左右绕行；连续失败上限可选，默认 5 次，成功恢复前进后清零。
- F6 开始／暂停／继续，F9 停止；切出游戏自动暂停，异常退出释放按键。
- 按当前有效地址导出 CE 坐标表；只读游戏内存，不写入坐标、不注入代码。

仓库不提供预设采集路线。自己的路线保存在程序旁的 `点位/` 目录，已从 Git 跟踪范围排除。

## 从源码运行

环境：Windows 10/11、64 位 Python 3.12。游戏由用户通过官方入口手动启动，进入角色后程序自动连接。读取游戏进程可能需要以管理员身份运行终端。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe tools/coordinate_monitor.py
```

创建环境并安装依赖后，也可运行 `Start-Heartopia.bat`。

## 打包 EXE

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm packaging/HeartopiaMonitor.spec
```

生成 `dist/HeartopiaMonitor.exe`，为单文件原生窗口程序，包含管理员权限清单。构建配置使用仓库相对路径，可在其他电脑复现；不包含游戏文件、个人路线或旧的固定地址表。

## 使用与限制

1. 进入游戏，在需要采集的位置点击“添加当前点位”，按行走顺序添加；转弯处补充点位。
2. 设置路线、起点方式、循环次数、采集等待和连续失败上限。
3. 点击“按顺序回放”，切回游戏按 F6。初始校准需要空旷平地；回放期间保持镜头稳定。

本程序按相邻点之间的直线移动，只能做有限范围的局部脱困，无法识别完整障碍地图。大范围障碍和复杂拐角应补充点位。F 后的等待是定时等待，尚未识别游戏中的采集完成状态。

坐标定位按游戏模块指纹和运行时对象元数据校验。游戏更新、换场景或坐标失效可能需要更新配置；版本不匹配时会拒绝读取。游戏内完整路线与复杂障碍通过效果仍需实测。

详细操作见 [使用说明](docs/使用说明.md)，避障设计依据见 [障碍处理与参考资料](docs/障碍处理与参考资料.md)。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests personal_tests -q
```

2026-10-13 的完整隔离回归为 1328 passed、27 xfailed、2 subtests passed。27 项 xfail 是保留的已知缺陷，不表示全部功能已经实机验证。回放、跳跃和按键释放测试使用替身，不操作真实游戏。发布前另行检查了打包代码和原生窗口布局。

## 来源与许可

本项目基于 [linruowuyin/Fhoe-Rail](https://github.com/linruowuyin/Fhoe-Rail) 修改，保留上游源码、署名及 GPL-3.0 许可证。来源和修改范围见 [UPSTREAM.md](UPSTREAM.md)，完整许可见 [LICENSE](LICENSE)。旧 WebUI 和星铁相关模块保留用于兼容与回归，当前入口为 `tools/coordinate_monitor.py`。
