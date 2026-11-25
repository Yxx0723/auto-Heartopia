"""Bounded asynchronous fishing trace and atomic summary."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
from queue import Full, Queue
from threading import Thread

from utils.core.fishing_log import append_fishing_event
from utils.drivers.route_archive_maintenance import backup_database, write_json

import json


class FishingArchive:
    def __init__(self, out, metadata):
        self.out = Path(out)
        self.queue = Queue(maxsize=512)
        self.dropped = 0
        self.error = None
        self.metadata = metadata
        self.worker = Thread(target=self._write,name='fishing-archive',daemon=True)
        self.worker.start()

    def add(self,row):
        try:
            self.queue.put_nowait(('event',row))
        except Full:
            self.dropped += 1

    def finish(self,summary):
        try:
            self.queue.put(('summary',summary),timeout=.25)
        except Full:
            self.error = '归档队列写入超时'
        self.worker.join(timeout=.5)

    def _write(self):
        try:
            write_json(self.out/'run.json',self.metadata)
            samples = {}
            with (self.out/'autofish.jsonl').open('a',encoding='utf-8') as stream:
                while True:
                    kind,data = self.queue.get()
                    if kind=='summary':
                        stream.flush()
                        summary = {**data,'dropped_events':self.dropped,'verified_aim_samples':len(samples)}
                        write_json(self.out/'summary.json',summary)
                        self._store_learning(samples)
                        return
                    if data['ev']=='catch' and data.get('aim_evidence'):
                        samples[data['cast_number']] = data['aim_evidence']
                    stream.write(json.dumps(data,ensure_ascii=False,allow_nan=False)+'\n')
                    if data['ev'] not in ('aim_observe','line_sample'):
                        append_fishing_event(self.out/'钓鱼日志.txt',data)
                    if self.queue.empty():
                        stream.flush()
        except (OSError,ValueError,TypeError,sqlite3.Error) as exc:
            self.error = str(exc)


    def _store_learning(self,samples):
        fields = ('profile','position','camera','window_size')
        identity = [self.metadata.get(field) for field in fields]
        if any(value is None for value in identity) or not samples:
            return
        scope = hashlib.sha256(json.dumps(identity,sort_keys=True,allow_nan=False).encode()).hexdigest()
        path = self.out.parent/'fishing-aim.sqlite3'
        # Reuse the WAL-consistent backup and conservative restore procedure.
        if path.exists():
            backup_database(path)
        with closing(sqlite3.connect(path,timeout=.5)) as db, db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1):
                raise sqlite3.DatabaseError('钓鱼经验库版本不兼容')
            db.execute('CREATE TABLE IF NOT EXISTS aim_evidence (scope TEXT, run TEXT, cast_no INTEGER, '
                       'data TEXT, PRIMARY KEY(scope,run,cast_no)) WITHOUT ROWID')
            db.execute('PRAGMA user_version=1')
            db.executemany('INSERT OR IGNORE INTO aim_evidence VALUES (?,?,?,?)',
                           [(scope,self.out.name,number,json.dumps(row,allow_nan=False))
                            for number,row in samples.items()])
        backup_database(path)
