from bisect import bisect_right
from datetime import date

from .organizations import intervals
from .storage import atomic, fingerprint, records, require_text


def ingest(database, batch, events):
    """接入一批活动。同批次重复传送幂等，内容不同则拒绝整批。"""
    require_text(batch, "批次")
    checksum = fingerprint(events)
    with atomic(database):
        previous = database.execute("SELECT checksum FROM batches WHERE batch=?", (batch,)).fetchone()
        if previous:
            if previous[0] != checksum:
                raise ValueError("相同批次内容不一致")
            return 0
        inserted = 0
        changed_schools = set()
        for event in events:
            require_text(event['event_id'], "活动标识")
            date.fromisoformat(event['day'])
            if not isinstance(event['people'], int) or isinstance(event['people'], bool) or event['people'] < 0:
                raise ValueError("参与人数需要非负整数")
            require_text(event['school'], "学校")
            identity = fingerprint(event)
            previous = database.execute("SELECT checksum FROM activity WHERE event_id=?", (event['event_id'],)).fetchone()
            if previous:
                if previous[0] != identity:
                    raise ValueError("活动标识冲突")
                continue
            assignment = database.execute("""SELECT id FROM assignment
                WHERE school=? AND start<=? AND end>?""",
                                          (event['school'], event['day'], event['day'])).fetchone()
            database.execute("""INSERT INTO activity(event_id,school,day,people,organization_id,
                                assignment_id,batch,checksum) VALUES(?,?,?,?,?,?,?,?)""",
                             (event['event_id'], event['school'], event['day'], event['people'],
                              None, assignment[0] if assignment else None, batch, identity))
            changed_schools.add(event['school'])
            inserted += 1
        database.execute("INSERT INTO batches VALUES(?,?)", (batch, checksum))
        if inserted:
            from .projection import record
            for school in sorted(changed_schools):
                events_for_school = sorted(event['event_id'] for event in events
                                           if event['school'] == school)
                record(database, school, 'ingest', cause=f"batch:{batch}",
                       detail={'batch': batch, 'events': events_for_school})
    return inserted


def detail(database):
    return records(database, """SELECT a.event_id,a.school,a.day,a.people,a.batch,s.status,
        COALESCE(s.stage,'未归属') stage,COALESCE(s.region,'未归属') region
        FROM activity a LEFT JOIN assignment s ON s.id=a.assignment_id
        ORDER BY a.event_id""")


def detail_as_of(database, as_of):
    """还原 ``as_of`` 当时掌握的信息下每条活动的归属，与当前明细同构。"""
    date.fromisoformat(as_of)
    schools = {row[0] for row in database.execute("SELECT DISTINCT school FROM activity")}
    timelines = {}
    for school in schools:
        rows = intervals(database, school, as_of=as_of)
        timelines[school] = ([row['start'] for row in rows], rows)
    result = []
    for row in database.execute("""SELECT event_id,school,day,people,batch
                                   FROM activity ORDER BY event_id"""):
        starts, rows = timelines[row['school']]
        index = bisect_right(starts, row['day']) - 1
        match = rows[index] if index >= 0 and row['day'] < rows[index]['end'] else None
        result.append({'event_id': row['event_id'], 'school': row['school'], 'day': row['day'],
                       'people': row['people'], 'batch': row['batch'],
                       'status': match['status'] if match else None,
                       'stage': match['stage'] if match else '未归属',
                       'region': match['region'] if match else '未归属'})
    return result


def unresolved(database):
    return records(database, """SELECT a.event_id,a.school,a.day,a.batch
        FROM activity a LEFT JOIN assignment s ON s.id=a.assignment_id
        WHERE s.status IS NULL OR s.status<>'governed'
        ORDER BY a.event_id""")


def activity_history(database, event_id):
    """活动的原始事实及其当前解释：原始日活动与人数永不因更正而改写。"""
    row = database.execute("SELECT * FROM activity WHERE event_id=?", (event_id,)).fetchone()
    if not row:
        raise KeyError(event_id)
    result = dict(row)
    result['assignment'] = dict(database.execute(
        "SELECT * FROM assignment WHERE id=?", (row['assignment_id'],)).fetchone() or {})
    return result
