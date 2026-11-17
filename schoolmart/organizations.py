from datetime import date

from .storage import atomic, records, require_text


def assign(database, school, start, end, stage, region):
    require_text(school, "学校")
    require_text(stage, "学段")
    require_text(region, "地区")
    if date.fromisoformat(start) >= date.fromisoformat(end):
        raise ValueError("归属区间需要正长度")
    with atomic(database):
        overlap = database.execute("SELECT 1 FROM organization WHERE school=? AND start<? AND end>?",
                                   (school, end, start)).fetchone()
        if overlap:
            raise ValueError("学校归属区间重叠")
        cursor = database.execute("INSERT INTO organization(school,start,end,stage,region) VALUES(?,?,?,?,?)",
                                  (school, start, end, stage, region))
    return cursor.lastrowid


def resolve(database, school, occurred):
    row = database.execute("SELECT * FROM organization WHERE school=? AND start<=? AND end>?",
                           (school, occurred, occurred)).fetchone()
    return dict(row) if row else None


def history(database, school):
    return records(database, "SELECT * FROM organization WHERE school=? ORDER BY start", (school,))
