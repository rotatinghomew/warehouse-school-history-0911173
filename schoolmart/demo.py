from .releases import Warehouse, publish
from .organizations import assign
from .facts import ingest


def run(root):
    warehouse = Warehouse(root)
    try:
        if not warehouse.db.execute("SELECT 1 FROM organization").fetchone():
            assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, '报名批次A', [
            {'event_id':'E1','school':'S1','day':'2026-03-01','people':4},
            {'event_id':'E2','school':'S9','day':'2026-03-01','people':2}])
        return publish(warehouse.db, '三月摘录')
    finally:
        warehouse.close()
