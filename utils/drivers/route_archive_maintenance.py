"""Background validation, conservative import and consistent SQLite backups."""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from threading import Lock, Thread
from queue import SimpleQueue
from uuid import uuid4

from utils.core.reordered_route import isolation_id
from utils.drivers.traversal_memory import TraversalMemory


def load_options(path):
    defaults = dict(archive=True, check=True, learn=True, backup=True, use_history=False)
    try:
        values = json.loads(path.read_text(encoding='utf-8'))
        return {key: values[key] if type(values.get(key)) is bool else value
                for key, value in defaults.items()} if isinstance(values, dict) else defaults
    except (OSError, ValueError):
        return defaults


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp-' + uuid4().hex)
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_rows(path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f'{path.name} 存在无效记录')
            yield row


def check_archive(path):
    route_bytes = (path/'route.json').read_bytes()
    route = json.loads(route_bytes)
    summary = json.loads((path/'summary.json').read_text(encoding='utf-8'))
    index = json.loads((path/'learning.json').read_text(encoding='utf-8'))
    if not isinstance(summary, dict) or not isinstance(index, dict) or not isinstance(index.get('files', {}), dict):
        raise ValueError('档案清单无效')
    run_id = index['run_id']
    if not isinstance(route, dict) or not isinstance(run_id, str) or not path.name.endswith('-'+run_id):
        raise ValueError('档案编号或路线无效')
    if hashlib.sha256(route_bytes).hexdigest() != index['route_sha256']:
        raise ValueError('路线校验值不一致')
    finalized = index.get('finalized') is True and bool(summary.get('ended_at'))
    sources = {'trajectory':'sample_count','plans':'planned_path_count','incidents':'incident_count',
               'events':None}
    if index.get('files', {}).get('verified_motion') == 'motion.jsonl':
        sources['motion'] = 'verified_motion_count'
    for name, key in sources.items():
        count = sum(1 for _ in read_rows(path/(name+'.jsonl')))
        if finalized and key and count != summary.get(key, 0):
            raise ValueError(f'{name}.jsonl 记录数量不一致')
    state = 'interrupted' if not finalized or summary.get('state') == 'interrupted' else 'verified'
    return route, summary, run_id, state


def healthy(db):
    return db.execute('PRAGMA user_version').fetchone()[0] == 1 and         db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]


def backup_database(path):
    if not path.is_file():
        return '未创建'
    backup = path.with_suffix('.backup.sqlite3')
    temporary = path.with_suffix('.backup-tmp-'+uuid4().hex)
    deadline = time.monotonic() + 15.

    def check_progress(status, remaining, total):
        if time.monotonic() >= deadline:
            raise sqlite3.OperationalError('数据库备份超过后台维护时限')
        if remaining:
            time.sleep(.001)

    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.25)) as source:
            try:
                version = source.execute('PRAGMA user_version').fetchone()[0]
                if version != 1:
                    return '版本不兼容，保留原库'
                intact = healthy(source)
            except sqlite3.DatabaseError as exc:
                if getattr(exc,'sqlite_errorcode',0) & 255 not in (sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB):
                    raise
                intact = False
            if intact:
                with closing(sqlite3.connect(temporary)) as destination:
                    source.backup(destination, pages=64, progress=check_progress, sleep=.05)
                    if not healthy(destination):
                        raise sqlite3.DatabaseError('备份完整性校验失败')
                temporary.replace(backup)
                return '备份已验证'
        if not backup.is_file():
            return '库已损坏，无可用备份'
        with closing(sqlite3.connect(backup.as_uri()+'?mode=ro', uri=True)) as source:
            if not healthy(source):
                return '备份损坏，已保留原库'
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination, pages=64, progress=check_progress, sleep=.05)
        # Preserve the damaged files. A WAL may belong to an active writer,
        # or replay into the restored database. Defer recovery in that case.
        if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm')):
            return '发现 WAL 或活动连接，已延后恢复'
        with closing(sqlite3.connect(path)) as original:
            try:
                original.execute('BEGIN EXCLUSIVE')
            except sqlite3.DatabaseError as exc:
                if getattr(exc,'sqlite_errorcode',0) & 255 not in (sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB):
                    raise
        from shutil import copy2
        copy2(path, path.with_suffix('.damaged-'+uuid4().hex+'.sqlite3'))
        temporary.replace(path)
        return '已从校验通过的备份恢复'
    finally:
        temporary.unlink(missing_ok=True)


def import_archive(path, route, summary, run_id, database):
    memory = TraversalMemory(database, run_id)
    try:
        if memory.db is None:
            raise sqlite3.DatabaseError(memory.error)
        if memory.db.execute('SELECT 1 FROM imported_runs WHERE id=?', (run_id,)).fetchone():
            return 0, 0
        # Legacy XYZ streams lack continuous input and collision evidence.
        # Only explicitly verified samples and physically supported stalls qualify.
        motion = path/'motion.jsonl'
        if not motion.is_file():
            return 0, 0
        count = 0
        for row in read_rows(motion):
            epoch = row.get('observed_epoch')
            if row.get('source') == 'continuous_walk_input_and_coordinates' and type(epoch) in (float,int) \
                    and math.isfinite(epoch):
                if memory.observe(row.get('start'), row.get('end'), row.get('seconds'), row.get('scope'),
                                  row.get('mode'), recovery=row.get('recovery') is True, observed=epoch):
                    count += 1
                    if count % 256 == 0:
                        if not memory.flush(path, summary['state'], force=True):
                            raise sqlite3.DatabaseError(memory.error)
        for row in read_rows(path/'incidents.jsonl'):
            if row.get('learning_scope'):
                row['observed_epoch'] = datetime.fromisoformat(row['at']).timestamp()
                memory.failed(row)
        failures = len(memory.pending_failures)
        if not memory.flush(path, summary['state'], force=True, imported=True):
            raise sqlite3.DatabaseError(memory.error)
        return count, failures
    finally:
        memory.close()


class RouteArchiveMaintenance:
    def __init__(self, root, options, log):
        self.root, self.options, self.log = Path(root), options, log
        self.jobs = SimpleQueue()
        self.worker = Thread(target=self._work, name='route-archive', daemon=True)
        self.worker.start()
        self.closed = False
        self.lock = Lock()

    def submit(self, archive=None):
        with self.lock:
            if not self.closed:
                self.jobs.put((archive,))

    def close(self):
        with self.lock:
            self.closed = True
            self.jobs.put(None)

    def _work(self):
        import sys
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), -2)
        while (job := self.jobs.get()) is not None:
            self._run(*job)

    def _run(self, archive):
        counts = dict(verified=0, interrupted=0, damaged=0)
        learned = failures = 0
        databases = set()
        default = self.root/'寻路记忆库.sqlite3'
        try:
            paths = [archive] if archive else (
                p for root in (self.root, self.root/'recorded') if root.is_dir()
                for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                and ('-' in p.name and p.name[-9:-8] == '-'))
            if self.options['backup'] and not archive:
                databases.update((default, self.root/'recorded'/default.name))
                databases.update((self.root/'隔离记忆').glob('*/寻路记忆库.sqlite3'))
                for db in tuple(databases):
                    if db.is_file():
                        self.log('archive', f'寻路记忆库：{backup_database(db)}')
                databases.clear()
            for path in paths:
                if not (self.options['check'] or self.options['learn']):
                    continue
                try:
                    route, summary, run_id, state = check_archive(path)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    counts['damaged'] += 1
                    if self.options['check']:
                        write_json(path/'archive-check.json', {'state':'damaged','error':str(exc)})
                    continue
                counts[state] += 1
                namespace = isolation_id(route)
                db = path.parent/'隔离记忆'/namespace/default.name if namespace else path.parent/default.name
                databases.add(db)
                valid, failed = 0, 0
                if self.options['learn'] and state == 'verified' and (path/'motion.jsonl').is_file() \
                        and summary.get('options', {}).get('learn', True):
                    db.parent.mkdir(parents=True, exist_ok=True)
                    valid, failed = import_archive(path, route, summary, run_id, db)
                    learned += valid
                    failures += failed
                if self.options['check']:
                    write_json(path/'archive-check.json', {'state':state,'valid_learning':valid,
                                                           'failure_evidence':failed})
            if self.options['backup'] and archive is not None and not databases:
                databases.add(archive.parent/default.name)
            status = [backup_database(path) for path in sorted(databases)] if self.options['backup'] else []
            self.log('archive', f"路线归档：校验 {counts['verified']}，中断 {counts['interrupted']}，"
                     f"异常 {counts['damaged']}；有效学习 {learned}，失败经验 {failures}；"
                     f"记忆库：{'、'.join(sorted(set(status))) or '无待备份库'}")
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            self.log('archive', f'路线归档或记忆库维护未完成：{exc}')
