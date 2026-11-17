"""派生投影：归属时间线 -> 活动归属 -> 汇总视图，全在同一事务内刷新。

``assignment`` 按（学校，业务开始，业务结束）保存当前生效解释，是**派生**结果，
不是原始事实；原始事实仍是 ``organization`` 主档、``claim`` 补报和 ``activity``。
``refresh()`` 只重算受影响的学校：删除过期片段、更新尚存片段、按业务日期
重新挂接活动，并把"哪些区间、哪些活动因此改变、由哪条事实触发"写入 ``audit``。

刷新与写原始事实处在同一个 ``BEGIN IMMEDIATE`` 事务里，因此崩溃或重传要么
整块生效、要么整块不生效，查询也不会看到明细已变而汇总未变的中间状态。
"""
import json
from datetime import datetime, timezone

from .organizations import active_claim_ids, intervals
from .storage import atomic, reading

def record(database, scope, action, cause=None, detail=None):
    database.execute("INSERT INTO audit(recorded_at,scope,action,cause,detail) VALUES(?,?,?,?,?)",
                     (datetime.now(timezone.utc).isoformat(timespec='seconds'),
                      scope, action, cause,
                      json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)))


def _snapshot(row):
    identifiers = row['claim_ids']
    if isinstance(identifiers, str):
        identifiers = json.loads(identifiers or '[]')
    return {'status': row['status'], 'source': row['source'], 'stage': row['stage'],
            'region': row['region'], 'claim_ids': identifiers,
            'pending': bool(row['pending'])}


def _values(row):
    return (row['status'], row['source'], row['stage'], row['region'],
            row['claim_ids'] if isinstance(row['claim_ids'], str)
            else json.dumps(row['claim_ids'], ensure_ascii=False, sort_keys=True),
            int(row['pending']))


def _interpretation(database, school):
    """活动当前的生效解释，用于刷新前后对比。"""
    keys = ('status', 'source', 'stage', 'region')
    return {row['event_id']: dict(zip(keys, (row['status'], row['source'],
                                             row['stage'], row['region'])))
            for row in database.execute("""SELECT a.event_id,s.status,s.source,s.stage,s.region
                FROM activity a LEFT JOIN assignment s ON s.id=a.assignment_id
                WHERE a.school=?""", (school,))}


def _plan(database, school):
    """只读地计算所需变更；没有变更时不产生任何写入。"""
    timeline = intervals(database, school)
    desired = {(row['start'], row['end']): row for row in timeline}
    existing = {(row['start'], row['end']): dict(row) for row in database.execute(
        "SELECT * FROM assignment WHERE school=?", (school,))}
    changed = {key for key, row in desired.items()
               if key not in existing or _values(existing[key]) != _values(row)}
    removed = set(existing) - set(desired)
    starts = sorted(desired)
    links = []
    for row in database.execute(
            "SELECT event_id,day,assignment_id FROM activity WHERE school=?", (school,)):
        key = None
        for candidate in starts:
            if candidate[0] <= row['day'] < candidate[1]:
                key = candidate
                break
        if key is None:
            expected = None
        elif key not in existing:
            expected = 'new'
        else:
            expected = existing[key]['id']
        if expected != row['assignment_id']:
            links.append(row['event_id'])
    return timeline, desired, existing, changed, removed, links


def refresh(database, school, cause=None):
    """重算一所学校的派生投影，返回审计条目或 None。

    先只读探测，确认确有变更才升级为写事务；进入写事务后重新计算，
    因此并发的重复登记不会各自写出一份归属。
    """
    with reading(database):
        if not _needs_work(database, school):
            return None
    with atomic(database):
        return _refresh(database, school, cause)


def _needs_work(database, school):
    timeline, desired, existing, changed, removed, relink = _plan(database, school)
    return bool(changed or removed or relink)


def _refresh(database, school, cause):
    timeline, desired, existing, changed, removed, relink = _plan(database, school)
    if not changed and not removed and not relink:
        return None
    before = _interpretation(database, school)
    database.execute("UPDATE activity SET assignment_id=NULL WHERE school=?", (school,))
    entries = []
    for key, row in desired.items():
        values = _values(row)
        previous = existing.get(key)
        if previous is None:
            entries.append({'start': key[0], 'end': key[1], 'before': None,
                            'after': _snapshot(row)})
            database.execute("""INSERT INTO assignment
                (school,start,end,status,source,stage,region,claim_ids,pending)
                VALUES(?,?,?,?,?,?,?,?,?)""", (school, key[0], key[1]) + values)
        elif key in changed:
            entries.append({'start': key[0], 'end': key[1],
                            'before': _snapshot(previous), 'after': _snapshot(row)})
            database.execute("""UPDATE assignment SET status=?,source=?,stage=?,region=?,
                claim_ids=?,pending=? WHERE id=?""", values + (previous['id'],))
    for key in removed:
        database.execute("DELETE FROM assignment WHERE id=?", (existing[key]['id'],))
        entries.append({'start': key[0], 'end': key[1],
                        'before': _snapshot(existing[key]), 'after': None})
    # 活动按业务日期重新挂接；没有匹配片段即未归属，绝不沿用旧解释。
    database.execute("""UPDATE activity SET assignment_id=(
            SELECT s.id FROM assignment s
            WHERE s.school=? AND s.start<=activity.day AND s.end>activity.day)
        WHERE school=?""", (school, school))
    after = _interpretation(database, school)
    reclassified = [{'event_id': event_id, 'before': before.get(event_id),
                     'after': after.get(event_id)}
                    for event_id in sorted(set(before) | set(after))
                    if before.get(event_id) != after.get(event_id)]
    entries.sort(key=lambda entry: (entry['start'], entry['end']))
    detail = {'school': school, 'intervals': entries, 'reclassified': reclassified,
              'active_claims': active_claim_ids(database, school)}
    record(database, school, 'recompute', cause=cause, detail=detail)
    return detail


def refresh_all(database, cause=None):
    """重算全部学校，用于迁移回填和显式重建；整体处在一个事务内。"""
    schools = {row[0] for row in database.execute("""SELECT school FROM organization
        UNION SELECT school FROM claim UNION SELECT school FROM activity""")}
    audits = 0
    for school in sorted(schools):
        if refresh(database, school, cause=cause) is not None:
            audits += 1
    return audits


def stale(database):
    """只读探测：派生投影是否已与原始事实一致。"""
    schools = {row[0] for row in database.execute("""SELECT school FROM organization
        UNION SELECT school FROM claim UNION SELECT school FROM activity""")}
    for school in schools:
        timeline, desired, existing, changed, removed, relink = _plan(database, school)
        if changed or removed or relink:
            return True
    return False


def rebuild(database):
    """显式全量重建：由原始事实重算派生投影，幂等且不制造重复归属。"""
    audits = refresh_all(database, cause='rebuild')
    if audits:
        with atomic(database):
            record(database, 'warehouse', 'rebuild', cause='rebuild',
                   detail={'schools_recomputed': audits})
    return audits


def audit_log(database, scope=None):
    rows = database.execute("""SELECT seq,recorded_at,scope,action,cause,detail FROM audit
        WHERE (? IS NULL OR scope=?) ORDER BY seq""", (scope, scope)).fetchall()
    result = []
    for row in rows:
        entry = dict(row)
        entry['detail'] = json.loads(entry['detail'])
        result.append(entry)
    return result
