# -*- coding: utf-8 -*-
import os
import sys

os.environ['PYTHONIOENCODING'] = 'utf-8'
for _out in (sys.stdout, sys.stderr):
    if hasattr(_out, 'reconfigure'):
        _out.reconfigure(encoding='utf-8', errors='replace')

# Personal edition routes WebUI and diagnostics before importing game services.
if __name__ == "__main__" and ("--webui" in sys.argv or len(sys.argv) == 1):
    import runpy
    from pathlib import Path
    import pyuac
    if not pyuac.isUserAdmin():
        pyuac.runAsAdmin()
        raise SystemExit(0)
    _base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    runpy.run_path(str(_base / "webui" / "server.py"), run_name="__main__")
    raise SystemExit(0)

if __name__ == '__main__' and '--coordinate-check' in sys.argv:
    import argparse
    import json
    import time
    from pathlib import Path
    import pyuac
    if not pyuac.isUserAdmin():
        pyuac.runAsAdmin()
        raise SystemExit(0)
    from utils.drivers.coordinate_memory import ReadOnlyPosition
    _parser = argparse.ArgumentParser(description='只读坐标诊断，不发送输入')
    _parser.add_argument('--coordinate-check', action='store_true')
    _parser.add_argument('--pid', type=int, required=True)
    _parser.add_argument('--check-output', required=True)
    _check_args = _parser.parse_args()
    _base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    _reader = None
    _result = {'ok': False, 'read_only': True, 'pid': _check_args.pid}
    try:
        _reader = ReadOnlyPosition(_check_args.pid, _base / 'coordinate-profile.json')
        _points = []
        for _ in range(60):
            _points.append(list(_reader.read()))
            time.sleep(.05)
        _result.update(ok=True, samples=len(_points), first=_points[0], last=_points[-1],
                       chains=len(_reader.profile['paths']), modules_verified=True)
    except Exception as _exc:
        _result['error'] = str(_exc)
    finally:
        if _reader:
            _reader.close()
        Path(_check_args.check_output).write_text(json.dumps(_result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(_result, ensure_ascii=False), flush=True)
    raise SystemExit(0 if _result['ok'] else 1)

if __name__ == "__main__" and "--self-check" in sys.argv:
    import json
    import runpy
    from pathlib import Path
    _base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    _config = json.loads((_base / "config.json").read_text(encoding="utf-8"))
    _routes = list((_base / "map").rglob("*.json"))
    assert _config.get("personal_edition") is True
    assert (_base / "webui" / "index.html").is_file()
    assert (_base / "map" / "default").is_dir()
    _server = runpy.run_path(str(_base / "webui" / "server.py"), run_name="myrail_diagnostics")
    assert callable(_server['run_start'])
    from utils.vision.diagnostics import check_runtime
    import orjson
    import onepush
    assert check_runtime(_base / "picture") and callable(orjson.dumps) and onepush
    from utils.drivers.raw_mouse import Header, MouseData, KeyboardData, Device, RawMouse
    from utils.drivers.windows_input import Input
    from utils.drivers.town_window import TownWindow, TimelineInput
    from utils.drivers.timeline_capture import Capture
    from utils.core.input_timeline import Timeline
    from pynput import mouse as _diagnostic_mouse
    from utils.flows.timeline_player import play_events
    from utils.flows.coordinate_player import drive_route
    from utils.ui.coordinate_record import record_main as _coordinate_record
    from utils.core.coordinate_profile import validate_profile
    from utils.core.coordinate_route import make_basis, direction_keys
    assert not validate_profile(json.loads((_base / 'coordinate-profile.json').read_text(encoding='utf-8')))
    assert direction_keys([0, 0, 0], [0, 0, 2], make_basis([0, 0, 1], [1, 0, 0])) == {'w'}
    assert drive_route and _coordinate_record
    import ctypes
    assert ctypes.sizeof(Header) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
    assert ctypes.sizeof(MouseData) == 24 and ctypes.sizeof(Device) > 0
    assert ctypes.sizeof(KeyboardData) == 16
    assert ctypes.sizeof(Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    assert TownWindow and TimelineInput and Capture and play_events
    # Exercise pynput's actual callback adaptation without starting hooks.
    _capture = Capture(Timeline(), None, None)
    _mouse = _diagnostic_mouse.Listener(on_click=_capture.click, on_scroll=_capture.scroll)
    _mouse.on_click(0, 0, _diagnostic_mouse.Button.left, True, False)
    _mouse.on_click(0, 0, _diagnostic_mouse.Button.left, False, False)
    _mouse.on_scroll(0, 0, 0, 1, False)
    _raw = RawMouse(_capture.motion, on_button=_capture.raw_button,
                    on_scroll=_capture.raw_scroll, on_key=_capture.raw_key)
    _raw.dispatch(bytes(Header(type=1)) + bytes(KeyboardData(scan=17, vk=87)))
    _raw.dispatch(bytes(Header(type=1)) + bytes(KeyboardData(scan=17, vk=87, flags=1)))
    _raw.dispatch(bytes(Header(type=0)) + bytes(MouseData(dx=12, dy=-3, buttons=1)))
    _raw.dispatch(bytes(Header(type=0)) + bytes(MouseData(buttons=2)))
    assert _capture.timeline.events == []
    assert not _capture.held_keys and not _capture.held_buttons
    print(f"MyRail self-check OK; personal routes: {len(_routes)}")
    raise SystemExit(0)

if __name__ == '__main__' and any(arg in sys.argv for arg in ('--record', '--map', '--engine', '--white', '--dev')):
    import argparse
    import json
    from pathlib import Path
    import pyuac
    if not pyuac.isUserAdmin():
        pyuac.runAsAdmin()
        raise SystemExit(0)
    _base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    os.chdir(_base)
    if not os.environ.get('MYRAIL_CONTROLLED'):
        (_base / 'logs/stop-request').unlink(missing_ok=True)
    _parser = argparse.ArgumentParser(description='MyRail 心动小镇个人路线')
    _parser.add_argument('--record', action='store_true')
    _parser.add_argument('--map')
    _parser.add_argument('--map-version', default='default')
    _parser.add_argument('--engine', action='store_true')
    _parser.add_argument('--white', action='store_true')
    _parser.add_argument('--dev', action='store_true')
    _parser.add_argument('--route-name', default='我的路线')
    _parser.add_argument('--route-author', default='我')
    _parser.add_argument('--route-planet', default='1')
    _args = _parser.parse_args()
    try:
        if _args.map_version != 'default':
            raise ValueError('个人版只使用我的路线库')
        if _args.record:
            from utils.ui.coordinate_record import record_main
            _cfg = json.loads((_base / 'config.json').read_text(encoding='utf-8'))
            record_main(_args.route_name, _args.route_author, _args.route_planet,
                        _base, _cfg.get('target_window_title', '心动小镇'))
        else:
            from utils.flows.timeline_player import play_main
            play_main(_base, _args.map)
    except Exception as _exc:
        print(f'MyRail：{_exc}', flush=True)
        raise SystemExit(1)
    raise SystemExit(0)

# 强制控制台使用UTF-8编码，防止cp932等非中文编码导致UnicodeEncodeError
os.environ["PYTHONIOENCODING"] = "utf-8"
for stream_name in ("stdout", "stderr"):
    stream = getattr(sys, stream_name)
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import datetime
import subprocess
import time
import traceback

import pyuac

# 日文系统(cp932)下中文输出会崩,统一把 stdout/stderr 改成 UTF-8
# 要在 import utils.core.log 之前执行
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass  # 部分环境（如某些重定向场景）不支持 reconfigure，忽略即可

from get_width import check_mult_screen
from utils.config.config import ConfigurationManager
from utils.core.log import log
from utils.flows.map_operations import MapOperations
from utils.core.map_info import MapInfo
from utils.ui.map_selector import choose_map, choose_map_debug
from utils.core.notify import Notify, get_error_summary
from utils.ui.setting import Setting
from utils.core.time_utils import TimeUtils
from utils.drivers.window import Window

cfg = ConfigurationManager()
time_mgr = TimeUtils()
map_info_instance = MapInfo()
setting = Setting()
notify = Notify()


def filter_content(content, keyword):
    # 将包含指定关键词的部分替换为空字符串
    return content.replace(keyword, "")


def print_version():
    try:
        with open("version.txt", "r", encoding="utf-8") as file:
            version = file.read().strip()
            log.info(f"当前版本：{version}")
        log.info(f"{cfg.CONFIG_FILE_NAME}")
        ConfigurationManager.modify_json_file(
            ConfigurationManager.CONFIG_FILE_NAME, "version", version
        )
        cfg.config_file.get("version", "")
    except (FileNotFoundError, IOError) as e:
        log.error(f"版本文件读取错误: {e}")


def print_info():
    log.info("")  # 添加一行空行
    php_content = "MyRail 个人版 · 只使用你自己录制的路线"
    filtered_content = filter_content(php_content, "舔狗日记")  # 过滤关键词
    log.info("\n" + filtered_content)  # 将过滤后的内容输出到日志
    log.info("")  # 添加一行空行
    log.info("=" * 60)
    log.info("开始运行")
    print_version()


def print_end():
    log.info("结束运行")
    log.info("=" * 60)
    # 清理 keyboard 全局监听（Pause 注册的 F7-F10 监听线程非 daemon，
    # 不清理会导致进程结束后无法退出）
    try:
        import keyboard

        keyboard.unhook_all()
    except Exception:
        pass


def parse_map_arg():
    """解析 --map <地图ID> 参数：非交互指定起始地图（供 WebUI 选图运行/开发者选图使用）
    例如: fhoe.py --dev --map 3-5_1
    """
    if "--map" in sys.argv:
        idx = sys.argv.index("--map")
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


def parse_map_version_arg():
    """解析 --map-version <版本名> 参数：临时覆盖地图版本（供 WebUI 选图运行使用）"""
    if "--map-version" in sys.argv:
        idx = sys.argv.index("--map-version")
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


def main():

    start_in_mid = False  # 是否为优先地图，优先地图完成后自动从1-1_0开始
    dev = False  # 初始开发者模式，为否
    map_arg = parse_map_arg()  # --map 参数：非交互指定起始地图（WebUI 选图运行）
    single_map_mode = map_arg is not None

    # WebUI 选图运行：若指定了 --map-version，临时切换地图版本（单图运行完后进程即退出）
    map_version_arg = parse_map_version_arg()
    if map_version_arg and map_arg:
        cfg.modify_json_file(cfg.CONFIG_FILE_NAME, "map_version", map_version_arg)
        # 直接同步 MapInfo 单例缓存，避免 property 延迟检测
        map_info_instance._map_version = map_version_arg
        log.info(f"[单图运行] 临时切换地图版本: {map_version_arg}")

    if len(sys.argv) > 1:
        if sys.argv[1] == "--map":
            cfg.main_start()
            start = (map_arg, False)
        elif sys.argv[1] == "--debug":
            cfg.main_start()
            start = (
                choose_map_debug(map_info_instance) if not map_arg else (map_arg, False)
            )
        elif sys.argv[1] == "--config":
            cfg.main_start_rewrite(setting)
            start = choose_map_debug(map_info_instance)
        elif sys.argv[1] == "--dev":
            cfg.main_start()
            start = (
                choose_map_debug(map_info_instance) if not map_arg else (map_arg, False)
            )
            dev = True  # 设置开发者模式
        elif sys.argv[1] == "--white":
            cfg.main_start()
            start = choose_map(map_info_instance)
            cfg.modify_json_file(
                cfg.CONFIG_FILE_NAME, "allowlist_mode_once", True
            )  # 启用一次白名单模式
        elif sys.argv[1] == "--record":
            from utils.ui.personal_record import record_main

            def record_arg(flag, default):
                if flag in sys.argv:
                    i = sys.argv.index(flag)
                    if i + 1 < len(sys.argv):
                        return sys.argv[i + 1]
                return default

            record_main(record_arg("--route-name", "我的路线"),
                        record_arg("--route-author", "我"),
                        record_arg("--route-planet", "1"))
            return
        elif sys.argv[1] == "--test":
            # 测试模式：单独测试某个功能
            cfg.main_start()
            Window().switch_window()
            # from utils.flows.handle import Handle
            # handle = Handle()
            # handle.auto_use_technique_consumable()
            return
        else:
            cfg.main_start()
            start = choose_map(map_info_instance)
    else:
        cfg.main_start()
        start = choose_map(map_info_instance)

    if isinstance(start, tuple):
        start_in_mid, start = start[1], start[0]

    if start:
        if cfg.config_file.get("personal_edition") is True:
            single_map_mode = True
        # 保存原始地图版本（单图运行可能临时切换版本）
        old_map_version = None
        if map_version_arg:
            old_map_version = cfg.load_config().get("map_version", "default")
            cfg.modify_json_file(cfg.CONFIG_FILE_NAME, "map_version", map_version_arg)
            map_info_instance._map_version = map_version_arg
            cfg._config = None  # 让 config_file property 重新加载新值
            log.info(f"[单图运行] 临时切换地图版本: {map_version_arg}")

        try:
            cfg.config_fix()
            log.info(f"config.json:{cfg.load_config()}")
            log.info("切换至游戏窗口，请确保1号位角色普攻为远程，黄泉地图1号位为黄泉")
            check_mult_screen()
            Window().switch_window()

            map_instance = MapOperations()
            time.sleep(0.5)
            log.info("开始运行，请勿移动鼠标和键盘.向着星...呃串台了")
            log.info("MyRail 个人版：本次只运行所选的自录路线。")
            start_time = datetime.datetime.now()
            notify.send_start(start)
            map_instance.process_map(
                start, start_in_mid, dev=dev, single_map=single_map_mode
            )  # 读取配置
            start_map = start if single_map_mode else "1-1_0"
            allow_run_again = not single_map_mode and cfg.read_json_file(
                cfg.CONFIG_FILE_NAME, False
            ).get("allow_run_again", False)
            if allow_run_again:
                map_instance.process_map(start_map, start_in_mid, dev=dev)
            end_time = datetime.datetime.now()

            # 结束通知（含统计摘要）
            try:
                summary = {
                    "总计用时": map_instance.map_statu.total_time
                    if hasattr(map_instance, "map_statu")
                    else None,
                    "战斗次数": map_instance.handle.combat.total_fight_cnt
                    if hasattr(map_instance, "handle")
                    else None,
                    "未战斗次数": map_instance.handle.combat.total_no_fight_cnt
                    if hasattr(map_instance, "handle")
                    else None,
                    "疾跑节约": map_instance.handle.movement.tatol_save_time
                    if hasattr(map_instance, "handle")
                    else None,
                    "系统卡顿": map_instance.handle.movement.time_error_cnt
                    if hasattr(map_instance, "handle")
                    else None,
                }
                summary = {k: v for k, v in summary.items() if v is not None}
                notify.send_end(summary)
            except Exception as e:
                log.warning(f"结束通知发送失败: {e}")
            shutdown_type = cfg.read_json_file(cfg.CONFIG_FILE_NAME, False).get(
                "auto_shutdown", 0
            )
            shutdown_computer(shutdown_type)
            if not single_map_mode and cfg.read_json_file(
                cfg.CONFIG_FILE_NAME, False
            ).get("allow_run_next_day", False):
                log.info("开始执行跨日连锄")
                if time_mgr.has_crossed_4am(start=start_time, end=end_time):
                    log.info("检测到换日，即将从头开锄")
                    map_instance.process_map(start_map, start_in_mid, dev=dev)
                else:
                    map_instance.handle.back_to_main(delay=2.0)
                    now = datetime.datetime.now()
                    refresh_hour = cfg.config_file.get("refresh_hour", 4)
                    refresh_minute = cfg.config_file.get("refresh_minute", 0)
                    next_4am = now.replace(
                        hour=refresh_hour,
                        minute=refresh_minute,
                        second=0,
                        microsecond=0,
                    )
                    if now.hour >= refresh_hour and now.minute >= refresh_minute:
                        next_4am += datetime.timedelta(days=1)
                    wait_time = (next_4am - now).total_seconds()
                    wait_time += 60
                    if wait_time <= 14400:
                        log.info(f"等待 {wait_time:.0f} 秒后游戏换日重锄")
                        time.sleep(wait_time)
                        map_instance.process_map(start_map, start_in_mid, dev=dev)
                    else:
                        log.info("等待时间过久，结束跨日连锄，等待时间需要 < 4小时")
            # shutdown_type = cfg.read_json_file(cfg.CONFIG_FILE_NAME, False).get('auto_shutdown', 0)
            # shutdown_computer(shutdown_type)
            if dev:  # 开发者模式自动重选地图
                main()
        finally:
            if old_map_version is not None:
                try:
                    cfg.modify_json_file(
                        cfg.CONFIG_FILE_NAME, "map_version", old_map_version
                    )
                    map_info_instance._map_version = old_map_version
                    cfg._config = None
                    log.info(f"[单图运行] 恢复地图版本: {old_map_version}")
                except Exception:
                    pass
    else:
        log.info("前面的区域，以后再来探索吧")
        main()


def shutdown_computer(shutdown_type):
    if shutdown_type == 0:
        pass
    elif shutdown_type == 1:
        log.info("下班喽！I'm free!")
        os.system("shutdown /s /f /t 10")
    elif shutdown_type == 2:
        log.info("10秒后注销")
        time.sleep(10)
        os.system("shutdown /l /f")
    elif shutdown_type == 3:
        log.info("关闭指定进程")
        taskkill_name = cfg.read_json_file(cfg.CONFIG_FILE_NAME, False).get(
            "taskkill_name", None
        )
        if taskkill_name:
            subprocess.call(["taskkill", "/im", taskkill_name, "/f"])
    else:
        log.info("shutdown_type参数不正确")


if __name__ == "__main__":
    try:
        if not pyuac.isUserAdmin():
            pyuac.runAsAdmin()
        else:
            print_info()
            main()
            print_end()
    except BaseException:
        print(traceback.format_exc())
        log.error(traceback.format_exc())
        # 出错通知
        try:
            notify.send_error(get_error_summary(sys.exc_info()[1]))
        except Exception:
            pass
        print("请重新运行")
        try:
            input("按回车键退出")
        except EOFError:
            pass  # WebUI 等无 stdin 环境下不阻塞退出
