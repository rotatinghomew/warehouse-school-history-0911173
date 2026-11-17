import tempfile
import threading
import unittest

from schoolmart.facts import activity_history, detail, ingest, unresolved
from schoolmart.organizations import assign, claim, claims, history, intervals, view
from schoolmart.projection import audit_log, rebuild
from schoolmart.releases import (Warehouse, publish, read_release, snapshot,
                                 summary, summary_as_of)


class Base(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = self.temporary.name
        self.resources = []

    def open(self, factory=Warehouse):
        resource = factory(self.directory)
        self.resources.append(resource)
        return resource

    def tearDown(self):
        for resource in reversed(self.resources):
            resource.close()
        self.temporary.cleanup()


class CorrectionTest(Base):
    def test_correction_changes_current_summary_but_not_release(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, 'B1', [{'event_id': 'E1', 'school': 'S1', 'day': '2026-03-01', 'people': 4}])
        frozen = publish(warehouse.db, '三月')
        claim(warehouse.db, 'C1', 'S1', '2026-02-01', '2026-04-01', '初中', '甲地',
              '2026-04-10', note='年初已调整学段')
        self.assertEqual(summary(warehouse.db), [{'day': '2026-03-01', 'stage': '初中', 'people': 4}])
        self.assertEqual(detail(warehouse.db)[0]['stage'], '初中')
        self.assertEqual(read_release(warehouse.db, '三月'), frozen)
        self.assertEqual(frozen['summary'][0]['stage'], '小学')

    def test_view_by_business_date_and_by_knowledge_time(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        claim(warehouse.db, 'C1', 'S1', '2026-02-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        self.assertEqual(view(warehouse.db, 'S1', '2026-03-01')['stage'], '初中')
        self.assertEqual(view(warehouse.db, 'S1', '2026-01-15')['stage'], '小学')
        self.assertEqual(view(warehouse.db, 'S1', '2026-05-01')['stage'], '小学')
        # 还原 2026-03-15 当时的解释：更正尚未获知，仍是小学。
        earlier = view(warehouse.db, 'S1', '2026-03-01', as_of='2026-03-15')
        self.assertEqual(earlier['stage'], '小学')
        self.assertEqual(earlier['source'], 'master')
        self.assertEqual(view(warehouse.db, 'S1', '2026-03-01', as_of='2026-04-11')['stage'], '初中')

    def test_partial_coverage_keeps_neighbouring_business_days(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        rows = {(r['start'], r['end'], r['stage']) for r in intervals(warehouse.db, 'S1')}
        self.assertEqual(rows, {('2026-01-01', '2026-03-01', '小学'),
                                ('2026-03-01', '2026-04-01', '初中'),
                                ('2026-04-01', '2027-01-01', '小学')})
        self.assertEqual(view(warehouse.db, 'S1', '2026-02-28')['stage'], '小学')
        self.assertEqual(view(warehouse.db, 'S1', '2026-04-01')['stage'], '小学')

    def test_conflict_preserved_not_resolved_by_arrival(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        claim(warehouse.db, 'C2', 'S1', '2026-03-15', '2026-03-20', '高中', '乙地', '2026-04-12')
        disputed = view(warehouse.db, 'S1', '2026-03-18')
        self.assertEqual(disputed['status'], 'disputed')
        self.assertEqual(disputed['stage'], '争议')
        self.assertEqual(disputed['claim_ids'], ['C1', 'C2'])
        self.assertTrue(disputed['pending'])
        # 冲突期间之外仍按各自覆盖解释。
        self.assertEqual(view(warehouse.db, 'S1', '2026-03-02')['stage'], '初中')
        self.assertEqual(view(warehouse.db, 'S1', '2026-04-02')['stage'], '小学')

    def test_conflict_cleared_only_by_explicit_supersession(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        claim(warehouse.db, 'C2', 'S1', '2026-03-01', '2026-04-01', '高中', '甲地', '2026-04-11')
        self.assertEqual(view(warehouse.db, 'S1', '2026-03-05')['status'], 'disputed')
        claim(warehouse.db, 'C3', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地',
              '2026-04-12', supersedes='C2')
        resolved = view(warehouse.db, 'S1', '2026-03-05')
        self.assertEqual((resolved['status'], resolved['stage']), ('governed', '初中'))
        self.assertEqual(resolved['claim_ids'], ['C1'])

    def test_retraction_restores_previous_interpretation(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        claim(warehouse.db, 'C2', 'S1', '2026-03-01', '2026-04-01', '小学', '甲地',
              '2026-05-01', supersedes='C1', note='撤回上月补报')
        self.assertEqual(view(warehouse.db, 'S1', '2026-03-05')['stage'], '小学')
        self.assertEqual(view(warehouse.db, 'S1', '2026-03-05', as_of='2026-04-20')['stage'], '初中')
        self.assertEqual([c['claim_id'] for c in claims(warehouse.db, 'S1')], ['C1', 'C2'])

    def test_supersession_is_append_only_so_no_cycle(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        claim(warehouse.db, 'C2', 'S1', '2026-03-01', '2026-04-01', '高中', '甲地',
              '2026-04-11', supersedes='C1')
        # 补报不可变且只能引用已存在的标识，成环在结构上不可能出现。
        with self.assertRaises(ValueError):
            claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '高中', '甲地',
                  '2026-04-11', supersedes='C2')
        self.assertEqual(view(warehouse.db, 'S1', '2026-03-05')['stage'], '高中')

    def test_empty_claim_is_a_pure_retraction(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        claim(warehouse.db, 'C2', 'S1', '2026-03-01', '2026-03-01', '小学', '甲地',
              '2026-05-01', supersedes='C1', note='只撤回，不新增覆盖')
        self.assertEqual(view(warehouse.db, 'S1', '2026-03-05')['stage'], '小学')

    def test_supersession_of_unknown_or_foreign_claim_rejected(self):
        warehouse = self.open()
        with self.assertRaises(ValueError):
            claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地',
                  '2026-04-10', supersedes='NOPE')
        claim(warehouse.db, 'C2', 'S2', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        with self.assertRaises(ValueError):
            claim(warehouse.db, 'C3', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地',
                  '2026-04-11', supersedes='C2')

    def test_unknown_school_stays_unassigned_until_material_arrives(self):
        warehouse = self.open()
        ingest(warehouse.db, 'B1', [{'event_id': 'E1', 'school': 'S9', 'day': '2026-03-01', 'people': 2}])
        self.assertEqual([r['event_id'] for r in unresolved(warehouse.db)], ['E1'])
        self.assertEqual(summary(warehouse.db), [{'day': '2026-03-01', 'stage': '未归属', 'people': 2}])
        assign(warehouse.db, 'S9', '2026-01-01', '2027-01-01', '小学', '甲地')
        self.assertEqual(unresolved(warehouse.db), [])
        self.assertEqual(summary(warehouse.db), [{'day': '2026-03-01', 'stage': '小学', 'people': 2}])

    def test_historical_summary_reconstructs_what_was_known(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, 'B1', [{'event_id': 'E1', 'school': 'S1', 'day': '2026-03-01', 'people': 4},
                                    {'event_id': 'E2', 'school': 'S9', 'day': '2026-03-01', 'people': 2}])
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        self.assertEqual(summary(warehouse.db), [{'day': '2026-03-01', 'stage': '初中', 'people': 4},
                                                 {'day': '2026-03-01', 'stage': '未归属', 'people': 2}])
        self.assertEqual(summary_as_of(warehouse.db, '2026-03-15'),
                         [{'day': '2026-03-01', 'stage': '小学', 'people': 4},
                          {'day': '2026-03-01', 'stage': '未归属', 'people': 2}])
        historical = snapshot(warehouse.db, as_of='2026-03-15')
        self.assertEqual(historical['details'][0]['stage'], '小学')
        self.assertEqual(sum(row['people'] for row in historical['summary']),
                         sum(row['people'] for row in historical['details']))

    def test_original_facts_never_rewritten(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, 'B1', [{'event_id': 'E1', 'school': 'S1', 'day': '2026-03-01', 'people': 4}])
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        record = activity_history(warehouse.db, 'E1')
        self.assertEqual((record['day'], record['people'], record['school']), ('2026-03-01', 4, 'S1'))
        self.assertEqual(record['assignment']['stage'], '初中')
        self.assertEqual(history(warehouse.db, 'S1')[0]['stage'], '小学')


class AuditTest(Base):
    def test_audit_names_changed_facts_and_reason(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, 'B1', [{'event_id': 'E1', 'school': 'S1', 'day': '2026-03-01', 'people': 4}])
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10',
              note='主管单位补报')
        log = audit_log(warehouse.db, 'S1')
        claim_entry = next(entry for entry in log if entry['action'] == 'claim')
        self.assertEqual(claim_entry['cause'], 'C1')
        self.assertEqual(claim_entry['detail']['claim']['note'], '主管单位补报')
        recompute = next(entry for entry in log if entry['cause'] == 'claim:C1')
        self.assertEqual(recompute['detail']['reclassified'],
                         [{'event_id': 'E1',
                           'before': {'status': 'governed', 'source': 'master',
                                      'stage': '小学', 'region': '甲地'},
                           'after': {'status': 'governed', 'source': 'claim',
                                     'stage': '初中', 'region': '甲地'}}])
        self.assertIn('2026-03-01', [row['start'] for row in recompute['detail']['intervals']])

    def test_audit_records_retraction_as_new_fact(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        claim(warehouse.db, 'C2', 'S1', '2026-03-01', '2026-04-01', '小学', '甲地',
              '2026-05-01', supersedes='C1', note='撤回')
        entry = next(e for e in audit_log(warehouse.db, 'S1') if e['cause'] == 'C2')
        self.assertEqual(entry['detail']['withdraws'], 'C1')
        self.assertEqual(entry['detail']['withdrawn_interval']['stage'], '初中')
        # 原始补报仍保留，历史解释可回溯。
        self.assertEqual(len(claims(warehouse.db, 'S1')), 2)


class IdempotencyTest(Base):
    def cleanup_warehouse(self, warehouse):
        # 每个线程创建自己的连接；关闭也必须在同一线程内完成。
        pass

    def test_duplicate_claim_delivery_is_idempotent(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        first = claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        second = claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        self.assertEqual((first, second), ('inserted', 'duplicate'))
        self.assertEqual(warehouse.db.execute("SELECT COUNT(*) FROM assignment").fetchone()[0], 3)
        with self.assertRaises(ValueError):
            claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '高中', '甲地', '2026-04-10')

    def test_redelivery_after_confining_crash_does_not_duplicate(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        warehouse.close()
        reopened = self.open()
        again = claim(reopened.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        self.assertEqual(again, 'duplicate')
        self.assertEqual(reopened.db.execute("SELECT COUNT(*) FROM claim").fetchone()[0], 1)
        self.assertEqual(reopened.db.execute(
            "SELECT COUNT(*) FROM assignment WHERE status='governed' AND source='claim'").fetchone()[0], 1)

    def test_failed_claim_leaves_no_partial_state(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, 'B1', [{'event_id': 'E1', 'school': 'S1', 'day': '2026-03-01', 'people': 4}])
        with self.assertRaises(ValueError):
            claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地',
                  '2026-04-10', supersedes='MISSING')
        self.assertEqual(claims(warehouse.db, 'S1'), [])
        self.assertEqual(detail(warehouse.db)[0]['stage'], '小学')

    def test_crash_mid_claim_rolls_back_and_restart_applies_once(self):
        from unittest import mock
        import schoolmart.projection as projection

        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, 'B1', [{'event_id': 'E1', 'school': 'S1', 'day': '2026-03-01', 'people': 4}])
        before = snapshot(warehouse.db)
        with mock.patch.object(projection, '_refresh', side_effect=RuntimeError('进程中断')):
            with self.assertRaises(RuntimeError):
                claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        # 原始补报与派生投影一起回滚，重启后不会留下半份归属。
        self.assertEqual(claims(warehouse.db, 'S1'), [])
        self.assertEqual(snapshot(warehouse.db), before)
        self.assertEqual(view(warehouse.db, 'S1', '2026-03-01')['stage'], '小学')
        self.assertEqual(claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01',
                               '初中', '甲地', '2026-04-10'), 'inserted')
        self.assertEqual(claim(warehouse.db, 'C1', 'S1', '2026-03-01', '2026-04-01',
                               '初中', '甲地', '2026-04-10'), 'duplicate')
        self.assertEqual(warehouse.db.execute(
            "SELECT COUNT(*) FROM assignment WHERE source='claim'").fetchone()[0], 1)

    def test_rebuild_is_idempotent_and_matches_incremental(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, 'B1', [{'event_id': 'E1', 'school': 'S1', 'day': '2026-03-01', 'people': 4}])
        claim(warehouse.db, 'C1', 'S1', '2026-02-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        before = snapshot(warehouse.db)
        self.assertEqual(rebuild(warehouse.db), 0)
        self.assertEqual(snapshot(warehouse.db), before)

    def test_consistent_snapshot_of_detail_and_summary(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, 'B1', [{'event_id': 'E1', 'school': 'S1', 'day': '2026-03-01', 'people': 4}])
        observed = []
        errors = []
        ready = threading.Barrier(2)
        done = threading.Barrier(2)

        def read_loop():
            # 读取方使用独立连接，与写方并发读取同一快照。
            local = Warehouse(self.directory)
            try:
                ready.wait()
                for _ in range(200):
                    data = snapshot(local.db)
                    total = sum(row['people'] for row in data['details'])
                    observed.append((total, sum(row['people'] for row in data['summary'])))
                done.wait()
            except BaseException as error:  # pragma: no cover - 诊断用
                errors.append(error)
            finally:
                local.close()

        reader = threading.Thread(target=read_loop)
        reader.start()
        ready.wait()
        try:
            for index in range(20):
                claim(warehouse.db, f'C{index}', 'S1', '2026-03-01', '2026-03-02',
                      '初中' if index % 2 else '小学', '甲地', '2026-04-10')
        finally:
            done.wait()
            reader.join()
        self.assertEqual(errors, [])
        self.assertTrue(observed)
        self.assertEqual({pair for pair in observed}, {(4, 4)})

    def test_concurrent_writers_do_not_duplicate_a_claim(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        warehouse.close()
        self.resources.remove(warehouse)
        results = []
        barrier = threading.Barrier(3)

        def deliver():
            local = Warehouse(self.directory)
            barrier.wait()  # 三个线程同时尝试登记同一标识
            try:
                results.append(claim(local.db, 'C1', 'S1', '2026-03-01', '2026-04-01',
                                     '初中', '甲地', '2026-04-10'))
            finally:
                local.close()

        threads = [threading.Thread(target=deliver) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), ['duplicate', 'duplicate', 'inserted'])
        check = self.open()
        self.assertEqual(check.db.execute("SELECT COUNT(*) FROM claim").fetchone()[0], 1)
        self.assertEqual(check.db.execute(
            "SELECT COUNT(*) FROM assignment WHERE source='claim'").fetchone()[0], 1)


class MigrationTest(Base):
    def test_existing_state_is_preserved_and_projection_backfilled(self):
        warehouse = self.open()
        assign(warehouse.db, 'S1', '2026-01-01', '2027-01-01', '小学', '甲地')
        ingest(warehouse.db, 'B1', [{'event_id': 'E1', 'school': 'S1', 'day': '2026-03-01', 'people': 4}])
        frozen = publish(warehouse.db, '三月')
        warehouse.db.execute("PRAGMA foreign_keys=OFF")
        warehouse.db.execute("DROP TABLE assignment")
        warehouse.db.execute("ALTER TABLE activity DROP COLUMN assignment_id")
        warehouse.db.commit()
        warehouse.db.execute("PRAGMA foreign_keys=ON")
        warehouse.close()
        reopened = self.open()
        self.assertEqual(detail(reopened.db)[0]['stage'], '小学')
        self.assertEqual(read_release(reopened.db, '三月'), frozen)
        claim(reopened.db, 'C1', 'S1', '2026-03-01', '2026-04-01', '初中', '甲地', '2026-04-10')
        self.assertEqual(detail(reopened.db)[0]['stage'], '初中')
        self.assertEqual(read_release(reopened.db, '三月'), frozen)
