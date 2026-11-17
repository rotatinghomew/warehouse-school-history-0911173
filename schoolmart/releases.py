import json
from pathlib import Path

from .facts import detail, detail_as_of
from .storage import atomic, connect, reading

SCHEMA = """
CREATE TABLE IF NOT EXISTS organization(id INTEGER PRIMARY KEY,school TEXT NOT NULL,start TEXT NOT NULL,
end TEXT NOT NULL,stage TEXT NOT NULL,region TEXT NOT NULL,
known_at TEXT NOT NULL DEFAULT '0000-01-01');
CREATE INDEX IF NOT EXISTS organization_interval ON organization(school,start,end);
CREATE TABLE IF NOT EXISTS claim(claim_id TEXT PRIMARY KEY,school TEXT NOT NULL,start TEXT NOT NULL,
end TEXT NOT NULL,stage TEXT NOT NULL,region TEXT NOT NULL,known_at TEXT NOT NULL,
supersedes TEXT REFERENCES claim(claim_id),note TEXT,effective_from TEXT NOT NULL,checksum TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS claim_school ON claim(school,known_at);
CREATE TABLE IF NOT EXISTS batches(batch TEXT PRIMARY KEY,checksum TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity(event_id TEXT PRIMARY KEY,school TEXT NOT NULL,day TEXT NOT NULL,
people INTEGER NOT NULL,organization_id INTEGER REFERENCES organization(id),
assignment_id INTEGER REFERENCES assignment(id),batch TEXT NOT NULL,checksum TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS activity_school ON activity(school,day);
CREATE TABLE IF NOT EXISTS assignment(id INTEGER PRIMARY KEY,school TEXT NOT NULL,start TEXT NOT NULL,
end TEXT NOT NULL,status TEXT NOT NULL,source TEXT NOT NULL,stage TEXT NOT NULL,region TEXT NOT NULL,
claim_ids TEXT NOT NULL DEFAULT '[]',pending INTEGER NOT NULL DEFAULT 0,
UNIQUE(school,start,end));
CREATE INDEX IF NOT EXISTS assignment_lookup ON assignment(school,start,end);
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,recorded_at TEXT NOT NULL,
scope TEXT NOT NULL,action TEXT NOT NULL,cause TEXT,detail TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_scope ON audit(scope,seq);
CREATE TABLE IF NOT EXISTS releases(name TEXT PRIMARY KEY,payload TEXT NOT NULL);
"""

MIGRATIONS = {
    # 旧库没有补报/派生投影/审计；保留原始事实并回填时间线与活动挂接。
    'organization': [("known_at", "TEXT NOT NULL DEFAULT '0000-01-01'")],
    'activity': [("assignment_id", "INTEGER REFERENCES assignment(id)")],
}


def _migrate(database):
    upgraded = False
    with atomic(database):
        for table, columns in MIGRATIONS.items():
            present = {row['name'] for row in database.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns:
                if name not in present:
                    database.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                    upgraded = True
        if upgraded:
            database.execute("""INSERT INTO audit(recorded_at,scope,action,cause,detail)
                VALUES(datetime('now'),'warehouse','migrate','schema',
                       '{"note":"旧库升级，保留原始事实并由其重建派生投影"}')""")


class Warehouse:
    def __init__(self, root):
        self.root = Path(root)
        self.db = connect(root, SCHEMA)
        _migrate(self.db)
        from .projection import rebuild
        rebuild(self.db)

    def close(self):
        self.db.close()


def _summary(rows):
    grouped = {}
    for row in rows:
        key = (row['day'], row['stage'])
        grouped[key] = grouped.get(key, 0) + row['people']
    return [{'day': day, 'stage': stage, 'people': people}
            for (day, stage), people in sorted(grouped.items())]


def _as_of(database, as_of):
    """as_of 存在时，明细与汇总都按当时已知信息还原。"""
    return detail_as_of(database, as_of) if as_of is not None else detail(database)


def summary(database):
    """当前口径的分学段汇总，与当前明细同源，均反映最新更正。"""
    return _summary(detail(database))


def summary_as_of(database, as_of):
    """``as_of`` 当时掌握的信息下的分学段汇总。"""
    return _summary(detail_as_of(database, as_of))


def snapshot(database, as_of=None):
    """同一快照内读取明细与汇总，二者不会跨越一次更正而彼此错配。

    使用只读事务：不升级为写锁，因此并发查询不会互相阻塞，也不会读到
    一次更正只完成一半的中间状态。
    """
    with reading(database):
        rows = _as_of(database, as_of)
        return {'details': rows, 'summary': _summary(rows)}


def publish(database, name):
    """已对外发出的月度摘录：写入后不可变，后续更正不会改写它。"""
    with atomic(database):
        payload = snapshot(database)
        previous = database.execute("SELECT payload FROM releases WHERE name=?", (name,)).fetchone()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if previous:
            if previous[0] != encoded:
                raise ValueError("发布名称已经绑定其他数据")
            return payload
        database.execute("INSERT INTO releases VALUES(?,?)", (name, encoded))
    return payload


def read_release(database, name):
    row = database.execute("SELECT payload FROM releases WHERE name=?", (name,)).fetchone()
    if not row:
        raise KeyError(name)
    return json.loads(row[0])
