from datetime import date

from .organizations import resolve
from .storage import atomic, fingerprint, records, require_text


def ingest(database, batch, events):
    require_text(batch, "批次")
    checksum = fingerprint(events)
    with atomic(database):
        previous = database.execute("SELECT checksum FROM batches WHERE batch=?", (batch,)).fetchone()
        if previous:
            if previous[0] != checksum:
                raise ValueError("相同批次内容不一致")
            return 0
        inserted = 0
        for event in events:
            require_text(event['event_id'], "活动标识")
            date.fromisoformat(event['day'])
            if not isinstance(event['people'], int) or isinstance(event['people'], bool) or event['people'] < 0:
                raise ValueError("参与人数需要非负整数")
            identity = fingerprint(event)
            previous = database.execute("SELECT checksum FROM activity WHERE event_id=?", (event['event_id'],)).fetchone()
            if previous:
                if previous[0] != identity:
                    raise ValueError("活动标识冲突")
                continue
            organization = resolve(database, event['school'], event['day'])
            database.execute("INSERT INTO activity VALUES(?,?,?,?,?,?,?)",
                             (event['event_id'], event['school'], event['day'], event['people'],
                              organization['id'] if organization else None, batch, identity))
            inserted += 1
        database.execute("INSERT INTO batches VALUES(?,?)", (batch, checksum))
    return inserted


def detail(database):
    return records(database, """SELECT a.event_id,a.school,a.day,a.people,o.stage,o.region
        FROM activity a LEFT JOIN organization o ON a.organization_id=o.id ORDER BY a.event_id"""
    )


def unresolved(database):
    return records(database, "SELECT event_id,school,day FROM activity WHERE organization_id IS NULL ORDER BY event_id")
