"""Offline scheduling regressions; no accounts, credentials or network required."""
import asyncio
import sqlite3
import unittest

from tam.tasks import TaskRunner, TaskStore


class CountingConnection(sqlite3.Connection):
    commits = 0

    def commit(self):
        self.commits += 1
        return super().commit()


class TaskPerformanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:', factory=CountingConnection)
        self.conn.row_factory = sqlite3.Row
        self.store = TaskStore(self.conn)
        self.addCleanup(self.conn.close)

    def make_rows(self, n):
        task = self.store.create('fixture', 'offline', [str(i) for i in range(n)])
        return task, self.store.targets(task.id, limit=n)

    async def test_stop_checks_do_not_aggregate_all_targets(self):
        task, _ = self.make_rows(100)
        queries = []
        self.conn.set_trace_callback(queries.append)
        async def handler(row):
            return 'ok'
        result = await TaskRunner(self.store).run(task.id, handler, concurrency=4)
        self.assertEqual(result['ok_count'], 100)
        aggregations = [q for q in queries if 'GROUP BY' in q.upper()]
        self.assertLessEqual(len(aggregations), 4)

    async def test_waiting_coroutines_are_bounded_by_concurrency(self):
        task, _ = self.make_rows(1000)
        baseline = len(asyncio.all_tasks())
        peak = 0
        active = 0
        async def handler(row):
            nonlocal peak, active
            active += 1
            self.assertLessEqual(active, 4)
            peak = max(peak, len(asyncio.all_tasks()) - baseline)
            await asyncio.sleep(0)
            active -= 1
        result = await TaskRunner(self.store).run(task.id, handler, concurrency=4)
        self.assertEqual(result['ok_count'], 1000)
        self.assertLessEqual(peak, 8)

    async def test_progress_coalesces_latest_values_in_one_transaction(self):
        _, rows = self.make_rows(1000)
        for row in rows:
            self.store.mark_target(row['id'], 'running')
        before = self.conn.commits
        for tick in range(3):
            for row in rows:
                self.store.mark_progress(row['id'], f'waiting {tick}', account_id=42)
        self.assertEqual(self.conn.commits, before)
        self.store.flush_progress()
        self.assertEqual(self.conn.commits - before, 1)
        values = self.conn.execute('SELECT detail,account_id FROM task_targets').fetchall()
        self.assertTrue(all(tuple(r) == ('waiting 2', 42) for r in values))
        self.assertIsNone(self.store._progress_handle)

    async def test_progress_flushes_automatically(self):
        task, rows = self.make_rows(1)
        self.store.mark_target(rows[0]['id'], 'running')
        self.store.mark_progress(rows[0]['id'], 'latest')
        await asyncio.sleep(.6)
        self.assertEqual(self.store.targets(task.id)[0]['detail'], 'latest')

    async def test_delayed_progress_never_overwrites_terminal_result(self):
        task, rows = self.make_rows(2)
        for row in rows:
            self.store.mark_target(row['id'], 'running')
            self.store.mark_progress(row['id'], 'waiting', account_id=42)
        for row, status in zip(rows, ['ok', 'fail']):
            self.store.mark_target(row['id'], status, 'final')
        self.store.flush_progress()
        self.assertEqual([r['detail'] for r in self.store.targets(task.id)], ['final', 'final'])
        self.assertIsNone(self.store._progress_handle)
        self.assertFalse(self.store._progress_pending)
        self.store.mark_progress(rows[0]['id'], 'late callback')
        self.store.flush_progress()
        self.assertEqual(self.store.targets(task.id)[0]['detail'], 'final')

    async def test_cancel_awaits_active_workers(self):
        task, _ = self.make_rows(100)
        started = asyncio.Event()
        active = set()
        async def handler(row):
            active.add(row['id'])
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active.remove(row['id'])
        run = asyncio.create_task(TaskRunner(self.store).run(task.id, handler, concurrency=4))
        await started.wait()
        run.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await run
        self.assertFalse(active)


if __name__ == '__main__':
    unittest.main()
