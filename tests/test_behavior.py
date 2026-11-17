import tempfile
import unittest


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.directory=self.temporary.name
        self.resources=[]

    def open(self, factory):
        resource=factory(self.directory)
        self.resources.append(resource)
        return resource

    def tearDown(self):
        for resource in reversed(self.resources):
            resource.close()
        self.temporary.cleanup()

from schoolmart.releases import Warehouse, publish, read_release, summary
from schoolmart.organizations import assign, history, resolve
from schoolmart.facts import ingest, detail, unresolved


class Behavior(WorkspaceTest):
    def test_half_open_and_overlap(self):
        warehouse = self.open(Warehouse)
        assign(warehouse.db, 'S1', '2026-01-01', '2026-07-01', '小学', '甲地')
        assign(warehouse.db, 'S1', '2026-07-01', '2027-01-01', '初中', '甲地')
        self.assertEqual(resolve(warehouse.db, 'S1', '2026-07-01')['stage'], '初中')
        with self.assertRaises(ValueError):
            assign(warehouse.db, 'S1', '2026-06-01', '2026-08-01', '高中', '甲地')
        self.assertEqual(len(history(warehouse.db, 'S1')), 2)

    def test_ingest_idempotency_and_unknown(self):
        warehouse = self.open(Warehouse)
        events = [{'event_id':'E1','school':'S1','day':'2026-03-01','people':4}]
        self.assertEqual(ingest(warehouse.db, 'B1', events), 1)
        self.assertEqual(ingest(warehouse.db, 'B1', events), 0)
        self.assertEqual(unresolved(warehouse.db)[0]['event_id'], 'E1')
        with self.assertRaises(ValueError):
            ingest(warehouse.db, 'B1', [dict(events[0], people=5)])
        self.assertEqual(detail(warehouse.db)[0]['people'], 4)

    def test_atomic_conflict_and_reopen(self):
        warehouse = self.open(Warehouse)
        events = [{'event_id':'E1','school':'S1','day':'2026-03-01','people':4}]
        ingest(warehouse.db, 'B1', events)
        with self.assertRaises(ValueError):
            ingest(warehouse.db, 'B2', [dict(events[0], event_id='E2'), dict(events[0], people=10)])
        self.assertEqual(len(detail(warehouse.db)), 1)
        second = self.open(Warehouse)
        self.assertEqual(len(detail(second.db)), 1)

    def test_frozen_month(self):
        warehouse = self.open(Warehouse)
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, 'B1', [{'event_id':'E1','school':'S1','day':'2026-03-01','people':4}])
        frozen = publish(warehouse.db, '三月')
        ingest(warehouse.db, 'B2', [{'event_id':'E2','school':'S1','day':'2026-03-01','people':2}])
        self.assertEqual(summary(warehouse.db)[0]['people'], 6)
        self.assertEqual(read_release(warehouse.db, '三月'), frozen)
        with self.assertRaises(ValueError):
            publish(warehouse.db, '三月')
