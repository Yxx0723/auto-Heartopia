"""Bounded directed motion index; raw plans/incidents remain in per-run archives.

Limits below are conservative engineering filters, not measured grounded,
slope or step capabilities. A candidate always needs live collision validation.
Runtime scene scopes intentionally prevent cross-process/world identity guesses.
"""
import hashlib
import json
import math
import sqlite3
import threading
import time

from utils.core.coordinate_route import valid_position

MAX_SAMPLE_SECONDS = 1.
MAX_SAMPLE_DISTANCE = 3.
MAX_SAMPLE_HEIGHT = .35
ENTRY_RADIUS = 4.
EDGE_LIMIT = 20000
HISTORICAL_FAILURE_SECONDS = 7 * 86400.
FAILURE_SECONDS = 120.  # Temporary candidate penalty, never a hard collision.


def same_direction(a,b,f,g):
    first = (b[0]-a[0], b[2]-a[2])
    second = (g[0]-f[0], g[2]-f[2])
    return sum(x*y for x,y in zip(first,second)) > .75*math.hypot(*first)*math.hypot(*second)


class TraversalMemory:
    def __init__(self, path, run_id):
        self.path, self.run_id = path, run_id
        self.lock = threading.RLock()
        self.pending = {}
        self.pending_failures = {}
        self.error = None
        self.last_flush = float('-inf')
        self.db = None
        try:
            self.db = sqlite3.connect(path, timeout=.02, check_same_thread=False)
            if self.db.execute('PRAGMA user_version').fetchone()[0] not in (0,1):
                raise sqlite3.DatabaseError('Unsupported traversal memory schema')
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA max_page_count=16384')
            self.db.execute('CREATE TABLE IF NOT EXISTS edges (id TEXT PRIMARY KEY, scope TEXT, '
                            'x REAL, y REAL, z REAL, data TEXT, updated REAL)')
            self.db.execute('CREATE INDEX IF NOT EXISTS near_edges ON edges(scope,x,z)')
            self.db.execute('CREATE TABLE IF NOT EXISTS runs '
                            '(id TEXT PRIMARY KEY, archive TEXT, state TEXT, updated REAL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS failures '
                            '(id TEXT PRIMARY KEY, scope TEXT, data TEXT, updated REAL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS edge_runs (edge TEXT, run TEXT, '
                            'PRIMARY KEY(edge,run)) WITHOUT ROWID')
            self.db.execute('CREATE TABLE IF NOT EXISTS imported_runs (id TEXT PRIMARY KEY)')
            self.db.execute('CREATE INDEX IF NOT EXISTS recent_failures ON failures(scope,updated)')
            self.db.execute('PRAGMA user_version=1')
            self.db.commit()
        except sqlite3.Error as exc:
            self.error = str(exc)
            if self.db is not None:
                self.db.close()
                self.db = None

    def observe(self, start, end, seconds, scope, mode='walk', *, recovery=False, observed=None):
        if mode != 'walk' or not scope or not valid_position(start) or not valid_position(end) \
                or type(seconds) not in (int,float) or not math.isfinite(seconds) \
                or not 0 < seconds <= MAX_SAMPLE_SECONDS \
                or not .05 <= math.dist(start,end) <= min(MAX_SAMPLE_DISTANCE, 12*seconds+.1) \
                or abs(end[1]-start[1]) > MAX_SAMPLE_HEIGHT:
            return
        row = {'start': list(start), 'end': list(end), 'seconds': seconds,
               'scope': scope, 'mode': mode, 'run_id': self.run_id,
               'source': 'continuous_walk_input_and_coordinates', 'grounded_confirmed': False,
               'recovery': recovery, 'observed_epoch': time.time() if observed is None else observed}
        identity = hashlib.sha256(json.dumps([scope, [round(v*4) for v in start], [round(v*4) for v in end]]).encode()).hexdigest()
        with self.lock:
            self.pending[identity] = row
            if len(self.pending) > 512:
                self.pending.pop(next(iter(self.pending)))
        return row

    def failed(self, row):
        verified = row.get('physical_stall') is True and bool(row.get('input_keys')) \
            and row.get('position_source') == 'last_successful_read'
        a,b=row.get('position'),row.get('steering_target') if verified else row.get('target')
        age=row.get('position_age_seconds')
        scope=row.get('learning_scope') if verified else row.get('traversal_scope')
        if row.get('kind') != 'movement_stall' or row.get('traversal_mode') != 'walk' \
                or not scope or not valid_position(a) \
                or not valid_position(b) or type(age) not in (int,float) or not 0<=age<=1.:
            return
        identity=json.dumps([self.run_id,scope,row.get('cycle'),row.get('waypoint'),
                             row.get('recovery_attempt'),[round(v,1) for v in a]])
        observed = row.get('observed_epoch', time.time())
        data={'start':a,'end':b,'scope':scope,'observed':observed,'verified':verified,
              'expires':observed+(HISTORICAL_FAILURE_SECONDS if verified else FAILURE_SECONDS)}
        if data['expires'] <= time.time():
            return
        with self.lock:
            if len(self.pending_failures)<128:
                self.pending_failures.setdefault(identity,data)

    def flush(self, archive, state, force=False, imported=False):
        with self.lock:
            if self.db is None or (not force and time.monotonic()-self.last_flush < 1.):
                return
            self.last_flush = time.monotonic()
            try:
                with self.db:
                    if imported and not self.db.execute('INSERT OR IGNORE INTO imported_runs VALUES (?)',
                                                        (self.run_id,)).rowcount:
                        return
                    self.db.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?,?)',
                                    (self.run_id, str(archive), state, time.time()))
                    self.db.executemany('INSERT OR IGNORE INTO edge_runs VALUES (?,?)',
                                        [(key,self.run_id) for key in self.pending])
                    self.db.executemany('INSERT INTO edges VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                        'x=excluded.x,y=excluded.y,z=excluded.z,data=excluded.data,updated=excluded.updated '
                        'WHERE excluded.updated >= edges.updated',
                        [(key, row['scope'], *row['start'], json.dumps(row), row['observed_epoch'])
                         for key,row in self.pending.items()])
                    self.db.executemany('INSERT OR REPLACE INTO failures VALUES (?,?,?,?)',
                        [(key,row['scope'],json.dumps(row),row['observed'])
                         for key,row in self.pending_failures.items()])
                    self.db.execute('DELETE FROM failures WHERE updated<?',
                                    (time.time()-HISTORICAL_FAILURE_SECONDS,))
                    self.db.execute('DELETE FROM edges WHERE id IN '
                                    '(SELECT id FROM edges ORDER BY updated DESC LIMIT -1 OFFSET ?)', (EDGE_LIMIT,))
                    self.db.execute('DELETE FROM edge_runs WHERE edge NOT IN (SELECT id FROM edges)')
                    self.db.execute('DELETE FROM runs WHERE id IN '
                                    '(SELECT id FROM runs ORDER BY updated DESC LIMIT -1 OFFSET 2000)')
                self.pending.clear()
                self.pending_failures.clear()
                self.error = None
                return True
            except sqlite3.Error as exc:
                self.error = str(exc)

    def historical_failures(self, position, scope, radius):
        if not scope or self.db is None:
            return ()
        with self.lock:
            try:
                rows = [json.loads(row[0]) for row in self.db.execute(
                    'SELECT data FROM failures WHERE scope=? AND updated>? ORDER BY updated DESC LIMIT 256',
                    (scope,time.time()-HISTORICAL_FAILURE_SECONDS))]
            except (sqlite3.Error, ValueError, TypeError) as exc:
                self.error = str(exc)
                return ()
        result = []
        for row in rows:
            if not isinstance(row,dict) or row.get('verified') is not True:
                continue
            a,b = row.get('start'),row.get('end')
            if not valid_position(a) or not valid_position(b) or abs(a[1]-position[1]) > MAX_SAMPLE_HEIGHT \
                    or math.hypot(a[0]-position[0],a[2]-position[2]) > radius:
                continue
            direction = [y-x for x,y in zip(a,b)]
            successes = self.candidates(a, scope, direction)
            if any(edge.get('updated',0) > row['observed'] and math.dist(a,edge['start']) <= .75
                   and same_direction(a,b,edge['start'],edge['end']) for edge in successes):
                continue
            result.append(((a[0],a[2]),(b[0],b[2])))
            if len(result) >= 24:
                break
        return tuple(result)

    def candidates(self, position, scope, direction=None):
        if not scope or not valid_position(position):
            return []
        with self.lock:
            rows = list(self.pending.values())
            failures=list(self.pending_failures.values())
            if self.db is not None:
                try:
                    rows += [dict(json.loads(r[0]), confidence=max(1,r[1]), updated=r[2]) for r in self.db.execute(
                        'SELECT data,(SELECT count(*) FROM edge_runs WHERE edge=edges.id),updated FROM edges WHERE scope=? AND x BETWEEN ? AND ? '
                        'AND z BETWEEN ? AND ? ORDER BY updated DESC LIMIT 128',
                        (scope, position[0]-ENTRY_RADIUS, position[0]+ENTRY_RADIUS,
                         position[2]-ENTRY_RADIUS, position[2]+ENTRY_RADIUS))]
                    failures += [json.loads(r[0]) for r in self.db.execute(
                        'SELECT data FROM failures WHERE scope=? AND updated>? ORDER BY updated DESC LIMIT 128',
                        (scope,time.time()-HISTORICAL_FAILURE_SECONDS))]
                except (sqlite3.Error, ValueError, TypeError) as exc:
                    self.error = str(exc)
        good = {}
        for row in rows:
            if not isinstance(row,dict):
                continue
            a,b = row.get('start'),row.get('end')
            if row.get('scope') != scope or row.get('mode') != 'walk' \
                    or not valid_position(a) or not valid_position(b) \
                    or math.dist(a,position)>ENTRY_RADIUS or abs(a[1]-position[1])>MAX_SAMPLE_HEIGHT:
                continue
            if direction is not None and sum((b[i]-a[i])*direction[i] for i in (0,2))<=0:
                continue
            penalties=0
            for failure in failures:
                if not isinstance(failure,dict) or not valid_position(failure.get('start')) \
                        or not valid_position(failure.get('end')) \
                        or type(failure.get('expires')) not in (int,float):
                    continue
                f,g=failure['start'],failure['end']
                if failure['scope']==scope and failure['expires']>time.time() \
                        and abs(a[1]-f[1]) <= MAX_SAMPLE_HEIGHT and math.dist(a,f)<2. \
                        and same_direction(a,b,f,g) \
                        and (not failure.get('verified') or row.get('updated',0) <= failure.get('observed',0)):
                    penalties+=1
            good[tuple(a)+tuple(b)] = dict(row,failures=penalties)
        return list(good.values())

    def close(self):
        with self.lock:
            if self.db is not None:
                self.db.close()
                self.db = None
