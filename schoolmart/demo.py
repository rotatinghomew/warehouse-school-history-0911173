from .releases import Warehouse, publish, snapshot, summary
from .organizations import assign, claim, intervals, view
from .facts import ingest, detail, unresolved
from .projection import audit_log


def run(root):
    warehouse = Warehouse(root)
    try:
        if not warehouse.db.execute("SELECT 1 FROM organization").fetchone():
            assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, '报名批次A', [
            {'event_id':'E1','school':'S1','day':'2026-03-01','people':4},
            {'event_id':'E2','school':'S9','day':'2026-03-01','people':2}])
        release = publish(warehouse.db, '三月摘录')
        # 主管单位补报：三月学段从小学更正为初中，且只覆盖 2026-02-01 至 2026-04-01。
        claim(warehouse.db, '补报-三月学段', 'S1', '2026-02-01', '2026-04-01', '初中', '甲地',
              known_at='2026-04-10', note='年初调整学段，主管单位本月补报')
        current = snapshot(warehouse.db)
        return {
            'release': release,
            'detail': current['details'],
            'summary': current['summary'],
            'summary_before_correction': release['summary'],
            'view_business_day': view(warehouse.db, 'S1', '2026-03-01'),
            'view_as_known_then': view(warehouse.db, 'S1', '2026-03-01', as_of='2026-04-01'),
            'timeline': intervals(warehouse.db, 'S1'),
            'unresolved': unresolved(warehouse.db),
            'audit': audit_log(warehouse.db),
        }
    finally:
        warehouse.close()
