"""学校归属：主档区间、主管单位补报、争议与生效时间线。

主档 ``assign()`` 保留原有语义，只登记互不重叠的已知归属。
补报 ``claim()`` 是追加型原始事实：携带获知时间与业务生效时间，
不可变、可幂等重传、同标识异内容视为冲突；撤回通过后继补报的
``supersedes`` 显式表达，而不是按到达先后覆盖。

生效时间线 ``intervals()`` 由主档与有效补报按分段求值派生，
不做增量改写，因此部分覆盖、撤回、同期间矛盾都能用同一规则重算。
同一业务期间内内容不一致的补报一律保留为争议，绝不凭到达先后挑选。
"""
from bisect import bisect_right
from datetime import date

from .storage import atomic, fingerprint, records, require_text

DISPUTED = '争议'
UNASSIGNED = '未归属'
_EPOCH = '0000-01-01'
_FOREVER = '9999-12-31'


def assign(database, school, start, end, stage, region, known_at=None):
    """登记主档归属（互不重叠）。

    ``known_at`` 可选，用于还原"当时掌握的信息"；缺省表示一开始即已知，
    这样不带 ``as_of`` 的查询结果不受登记时刻影响。
    """
    require_text(school, "学校")
    require_text(stage, "学段")
    require_text(region, "地区")
    if date.fromisoformat(start) >= date.fromisoformat(end):
        raise ValueError("归属区间需要正长度")
    moment = date.fromisoformat(known_at).isoformat() if known_at is not None else _EPOCH
    with atomic(database):
        overlap = database.execute("SELECT 1 FROM organization WHERE school=? AND start<? AND end>?",
                                   (school, end, start)).fetchone()
        if overlap:
            raise ValueError("学校归属区间重叠")
        cursor = database.execute(
            "INSERT INTO organization(school,start,end,stage,region,known_at) VALUES(?,?,?,?,?,?)",
            (school, start, end, stage, region, moment))
        _refresh_school(database, school, f"assign:{cursor.lastrowid}")
    return cursor.lastrowid


def claim(database, claim_id, school, start, end, stage, region, known_at,
          supersedes=None, note=None, effective_from=None):
    """登记一份主管单位补报，返回 ``'inserted'`` 或 ``'duplicate'``。

    ``supersedes`` 为该补报替代的、同一学校的补报标识；被替代者整体失效，
    这也是撤回的唯一表达方式（撤回可不覆盖任何业务日期，即 ``start == end``）。
    ``known_at`` 是获知时间，``effective_from`` 是业务生效时间，两者是不同事实，
    缺省时生效时间等于获知时间，但不因此产生任何优先关系。
    """
    require_text(claim_id, "更正标识")
    require_text(school, "学校")
    require_text(stage, "学段")
    require_text(region, "地区")
    if note is not None:
        require_text(note, "来源说明")
    date.fromisoformat(start)
    date.fromisoformat(end)
    moment = date.fromisoformat(known_at)
    effective = date.fromisoformat(effective_from) if effective_from is not None else moment
    if start > end:
        raise ValueError("更正区间需要非负长度")
    recorded = {'claim_id': claim_id, 'school': school, 'start': start, 'end': end,
                'stage': stage, 'region': region, 'known_at': known_at,
                'supersedes': supersedes, 'note': note,
                'effective_from': effective.isoformat()}
    checksum = fingerprint(recorded)
    with atomic(database):
        previous = database.execute("SELECT checksum FROM claim WHERE claim_id=?",
                                    (claim_id,)).fetchone()
        if previous:
            if previous[0] != checksum:
                raise ValueError("相同更正标识内容不一致")
            return 'duplicate'
        if supersedes is not None:
            target = database.execute("SELECT 1 FROM claim WHERE claim_id=? AND school=?",
                                      (supersedes, school)).fetchone()
            if not target:
                raise ValueError("被撤回的更正不存在或不属于该学校")
        database.execute("""INSERT INTO claim(claim_id,school,start,end,stage,region,known_at,
                            supersedes,note,effective_from,checksum) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                         (claim_id, school, start, end, stage, region, known_at,
                          supersedes, note, effective.isoformat(), checksum))
        from .projection import record
        record(database, school, 'claim', cause=claim_id,
               detail={'claim': recorded,
                       'withdraws': supersedes,
                       'withdrawn_interval': _target_interval(database, supersedes)})
        _refresh_school(database, school, f"claim:{claim_id}")
    return 'inserted'


def _target_interval(database, claim_id):
    if claim_id is None:
        return None
    row = database.execute("SELECT start,end,stage,region FROM claim WHERE claim_id=?",
                           (claim_id,)).fetchone()
    return dict(row) if row else None


def _refresh_school(database, school, cause):
    # 延迟导入避免与 facts/projection 形成模块环。
    from .projection import refresh
    refresh(database, school, cause)


def resolve(database, school, occurred):
    """业务日期 ``occurred`` 的当前生效归属；无覆盖时返回 None。"""
    return view(database, school, occurred)


def history(database, school):
    return records(database, "SELECT * FROM organization WHERE school=? ORDER BY start", (school,))


def claims(database, school):
    return records(database, """SELECT claim_id,school,start,end,stage,region,known_at,
                      supersedes,note,effective_from FROM claim WHERE school=?
                      ORDER BY known_at,claim_id""", (school,))


def active_claim_ids(database, school, as_of=None):
    """在 ``as_of`` 已知信息下仍然有效的补报标识。"""
    rows = records(database, "SELECT claim_id,supersedes FROM claim WHERE school=? AND known_at<=?",
                   (school, as_of or _FOREVER))
    withdrawn = {row['supersedes'] for row in rows if row['supersedes'] is not None}
    return sorted(row['claim_id'] for row in rows if row['claim_id'] not in withdrawn)


def _load(database, school, as_of):
    masters = records(database, """SELECT rowid AS seq,start,end,stage,region,known_at
                        FROM organization WHERE school=? AND known_at<=?""",
                      (school, as_of or _FOREVER))
    claims = records(database, """SELECT rowid AS seq,claim_id,start,end,stage,region,known_at,
                        supersedes,note,effective_from FROM claim WHERE school=? AND known_at<=?""",
                     (school, as_of or _FOREVER))
    withdrawn = {c['supersedes'] for c in claims if c['supersedes'] is not None}
    claims = [c for c in claims if c['claim_id'] not in withdrawn]
    masters.sort(key=lambda r: (r['start'], r['seq']))
    claims.sort(key=lambda r: (r['effective_from'], r['seq']))
    return masters, claims


def _master_at(masters, start, end):
    for master in masters:
        if master['start'] < end and master['end'] > start:
            return {'status': 'governed', 'source': 'master', 'stage': master['stage'],
                    'region': master['region'], 'claim_ids': [], 'pending': False,
                    'effective_from': _EPOCH}
    return None


def _claims_at(claims, start, end):
    """对 ``[start,end)`` 求值。

    覆盖该期间的有效补报若内容一致即生效；内容不一致且没有任何一方显式
    替代另一方时保留为争议，不按获知时间或到达顺序挑选。
    """
    covering = [c for c in claims if c['start'] < end and c['end'] > start]
    if not covering:
        return None
    signatures = {(c['stage'], c['region']) for c in covering}
    identifiers = sorted(c['claim_id'] for c in covering)
    if len(signatures) > 1:
        # 争议从最后一份互不替代的冲突声明获知时开始；此时刻只用于说明，不用于挑选。
        return {'status': 'disputed', 'source': 'conflict', 'stage': DISPUTED,
                'region': DISPUTED, 'claim_ids': identifiers, 'pending': True,
                'effective_from': max(c['known_at'] for c in covering)}
    chosen = min(covering, key=lambda c: (c['effective_from'], c['seq']))
    return {'status': 'governed', 'source': 'claim', 'stage': chosen['stage'],
            'region': chosen['region'], 'claim_ids': [chosen['claim_id']],
            'pending': False, 'effective_from': chosen['effective_from']}


def intervals(database, school, as_of=None):
    """学校归属时间线，按业务日期升序，只包含有记录覆盖的片段。"""
    require_text(school, "学校")
    if as_of is not None:
        date.fromisoformat(as_of)
    masters, claims = _load(database, school, as_of)
    points = sorted({r['start'] for r in masters + claims} | {r['end'] for r in masters + claims})
    timeline = []
    for start, end in zip(points, points[1:]):
        if start >= end:
            continue
        row = _claims_at(claims, start, end) or _master_at(masters, start, end)
        if row is None:
            continue
        timeline.append(dict(row, start=start, end=end))
    return timeline


def view(database, school, occurred, as_of=None):
    """业务日期 ``occurred`` 的生效归属。

    ``as_of`` 缺省为当前掌握的全部信息；给出已知时间即可还原当时掌握的解释。
    返回 None 表示该学校在该日期没有可解析的归属。
    """
    require_text(school, "学校")
    date.fromisoformat(occurred)
    if as_of is not None:
        date.fromisoformat(as_of)
    timeline = intervals(database, school, as_of)
    starts = [row['start'] for row in timeline]
    index = bisect_right(starts, occurred) - 1
    if index >= 0 and timeline[index]['start'] <= occurred < timeline[index]['end']:
        return timeline[index]
    return None
