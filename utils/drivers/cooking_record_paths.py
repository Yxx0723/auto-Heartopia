"""Current-user storage and read-only discovery of legacy P0 journals."""
import json
import os
from pathlib import Path
import stat


def resolve_cooking_record_dir(*, home=None):
    local = Path(os.environ.get('LOCALAPPDATA', ''))
    if not local.is_absolute():
        home = Path.home() if home is None else Path(home)
        if not home.is_absolute():
            raise ValueError('无法确定当前用户的数据目录')
        local = home/'AppData'/'Local'
    return local/'auto-Heartopia'/'烹饪记录'


def legacy_cooking_record_dir():
    # The old version used the physical Desktop under the profile, even if
    # Windows redirected the displayed desktop elsewhere.
    return Path.home()/'Desktop'/'烹饪记录'


def _record_stat(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (stat.S_ISLNK(info.st_mode) or
            getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError(f'烹饪记录路径包含重定向，无法确认归属：{path}')
    return info


def pending_entry_journals(root, legacy_root=None):
    """Fail closed on unreadable or ambiguous journals, without migrating data."""
    roots = {Path(root)}
    if legacy_root is not None:
        legacy_root = Path(legacy_root)
        roots.add(legacy_root)
    pending = []
    for directory in sorted(roots):
        try:
            if directory == legacy_root:
                _record_stat(directory.parent.parent)
                _record_stat(directory.parent)
            if _record_stat(directory) is None:
                continue
            for task in sorted(directory.iterdir()):
                if not task.name.startswith('入口验证-'):
                    continue
                task_info = _record_stat(task)
                if task_info is None or not stat.S_ISDIR(task_info.st_mode):
                    raise ValueError(f'入口任务目录无效：{task}')
                path = task/'entry.json'
                info = _record_stat(path)
                if info is None:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f'入口记录不是普通文件：{path}')
                saved = json.loads(path.read_text(encoding='utf-8-sig'))
                if (not isinstance(saved, dict) or saved.get('schema') != 'cooking-entry-p0-v1'
                        or type(saved.get('entry_dispatched')) is not bool
                        or not isinstance(saved.get('state'), str)
                        or not isinstance(saved.get('binding'), str) or not saved['binding']):
                    raise ValueError(f'入口记录格式无效：{path}')
                if saved['entry_dispatched'] and saved['state'] != 'RECIPE_VALIDATED':
                    pending.append((path, saved['binding']))
        except (OSError, ValueError) as exc:
            raise ValueError(f'无法核对烹饪入口记录，请检查目录及记录：{directory}（{exc}）') from exc
    return pending
