import json
from pathlib import Path

from .facts import detail
from .storage import atomic, connect, records

SCHEMA = """
CREATE TABLE IF NOT EXISTS organization(id INTEGER PRIMARY KEY,school TEXT NOT NULL,start TEXT NOT NULL,
end TEXT NOT NULL,stage TEXT NOT NULL,region TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS organization_interval ON organization(school,start,end);
CREATE TABLE IF NOT EXISTS batches(batch TEXT PRIMARY KEY,checksum TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity(event_id TEXT PRIMARY KEY,school TEXT NOT NULL,day TEXT NOT NULL,
people INTEGER NOT NULL,organization_id INTEGER REFERENCES organization(id),batch TEXT NOT NULL,checksum TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS releases(name TEXT PRIMARY KEY,payload TEXT NOT NULL);
"""


class Warehouse:
    def __init__(self, root):
        self.root = Path(root)
        self.db = connect(root, SCHEMA)

    def close(self):
        self.db.close()


def summary(database):
    return records(database, """SELECT a.day,COALESCE(o.stage,'未归属') stage,SUM(a.people) people
        FROM activity a LEFT JOIN organization o ON o.id=a.organization_id
        GROUP BY a.day,COALESCE(o.stage,'未归属') ORDER BY a.day,stage"""
    )


def publish(database, name):
    with atomic(database):
        payload = {'details': detail(database), 'summary': summary(database)}
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
