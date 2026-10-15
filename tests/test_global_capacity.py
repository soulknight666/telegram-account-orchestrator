import unittest
from tam.db import Database, Account
from tam.importer import import_accounts

class GlobalCapacityTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(':memory:')
        self.addCleanup(self.db.conn.close)
        self.pool = ['socks5://a.invalid:1080', 'socks5://b.invalid:1080']
    def run_import(self, text, **kw):
        return import_accounts(self.db, text, proxy_pool=self.pool, links_per_proxy=2, **kw)
    def test_global_counts_across_imports_and_all_statuses(self):
        self.run_import('+12025550100\n+12025550101')
        self.db.update(self.db.list()[0].id, status='error')
        r = self.run_import('+12025550102\n+12025550103\n+12025550104')
        self.assertEqual(len(r['added']), 2)
        self.assertEqual(r['capacity_blocked'], 1)
        self.assertEqual([x['used'] for x in r['proxy_usage']], [2,2])
        self.assertEqual(len(self.db.list()), 4)
    def test_preview_reserves_capacity_and_deduplicates_without_writes(self):
        self.run_import('+12025550100')
        text = '+12025550101\n+12025550101\n+12025550102\n+12025550103\n+12025550104'
        preview = self.run_import(text, dry_run=True)
        self.assertEqual(len(self.db.list()), 1)
        actual = self.run_import(text)
        self.assertEqual(len(preview['added']), 3)
        self.assertEqual(preview['proxy_usage'], actual['proxy_usage'])
        self.assertEqual(preview['capacity_blocked'], 1)
    def test_existing_accounts_keep_proxy_at_full_capacity(self):
        self.run_import('+12025550100\n+12025550101\n+12025550102\n+12025550103')
        r = self.run_import('+12025550100|https://example.invalid/new')
        self.assertEqual(len(r['updated']), 1)
        self.assertEqual(self.db.get_by_label('+12025550100').proxy, self.pool[0])
    def test_default_proxy_usage_counts_and_does_not_join_nonempty_pool(self):
        self.db.add_account(Account(label='no-phone', proxy=None))
        r = import_accounts(self.db, '+12025550101\n+12025550102', proxy='socks5://fallback.invalid:1080',
                            proxy_pool=[self.pool[0], self.pool[0]], links_per_proxy=2,
                            default_proxy=self.pool[0])
        self.assertEqual(len(r['added']), 1)
        self.assertEqual(r['proxy_pool_size'], 1)
    def test_capacity_freed_when_account_deleted(self):
        self.run_import('+12025550100\n+12025550101\n+12025550102\n+12025550103')
        self.db.delete(self.db.get_by_label('+12025550100').id)
        r = self.run_import('+12025550104')
        self.assertEqual(len(r['added']), 1)

if __name__ == '__main__': unittest.main()
