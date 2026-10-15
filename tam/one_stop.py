"""Durable one-stop checkpoints. Network effects never depend on a browser request.

Task params/results contain no credentials. The desired password is encrypted in
a private table. An interrupted password mutation is never repeated implicitly.
"""
import asyncio
import time
import threading
from contextlib import nullcontext

from .crypto import encrypt, decrypt


class OneStopWorkflow:
    change_timeout = 180.0

    def __init__(self, store, runner, manager, change, classify):
        self.store, self.runner, self.manager = store, runner, manager
        self.change, self.classify = change, classify
        self.conn = store.conn
        self._start_guard = threading.Lock()
        self._leases = {}
        self.database = self.conn.execute('PRAGMA database_list').fetchone()['file']
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS one_stop_runs (
                task_id INTEGER PRIMARY KEY, password_enc TEXT);
            CREATE TABLE IF NOT EXISTS one_stop_items (
                task_id INTEGER NOT NULL, account_id INTEGER NOT NULL,
                phase TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY(task_id, account_id));
        ''')
        self.conn.commit()
        columns = {r['name'] for r in self.conn.execute('PRAGMA table_info(one_stop_items)')}
        with self.conn:
            if 'error_code' not in columns:
                self.conn.execute("ALTER TABLE one_stop_items ADD COLUMN error_code TEXT")
            if 'retryable' not in columns:
                self.conn.execute("ALTER TABLE one_stop_items ADD COLUMN retryable INTEGER")

    def _acquire(self, tid):
        from .task_lease import TaskLease
        if tid in self._leases:
            raise ValueError('任务已由其它执行器持有')
        if self.database:
            try:
                return TaskLease(self.database, tid)
            except BlockingIOError:
                raise ValueError('任务已由其它执行器持有') from None

    def _live(self, tid):
        if self.runner.is_running(tid) or tid in self._leases:
            return True
        try:
            probe = self._acquire(tid)
        except ValueError:
            return True
        if probe:
            probe.close()
        return False

    def create(self, ids, *, concurrency, new_password, fmt, query_delay=8, poll_interval=60):
        ids = list(dict.fromkeys(ids))
        if not ids or not 1 <= concurrency <= 32 or fmt not in ('pack', 'session'):
            raise ValueError('账号、并发或导出格式无效')
        for aid in ids:
            acc = self.manager.db.get(aid)
            if not acc or not acc.phone or not (acc.session_enc or acc.code_url):
                raise ValueError(f'账号 #{aid} 缺少登录信息')
        password = new_password.strip()
        blob = encrypt(self.manager.s.master_key, password) if password else None
        task = self.store.create('one_stop', '一条龙', [str(i) for i in ids], params={
            'concurrency': concurrency, 'format': fmt, 'change_password': bool(password),
            'query_delay': query_delay, 'poll_interval': poll_interval})
        with self.conn:
            self.conn.execute('INSERT INTO one_stop_runs VALUES (?,?)', (task.id, blob))
            self.conn.executemany('INSERT INTO one_stop_items(task_id,account_id) VALUES (?,?)',
                                  [(task.id, aid) for aid in ids])
        return task

    def phase(self, tid, aid, value=None):
        if value is not None:
            with self.conn:
                self.conn.execute('UPDATE one_stop_items SET phase=? WHERE task_id=? AND account_id=?',
                                  (value, tid, aid))
            return value
        row = self.conn.execute('SELECT phase FROM one_stop_items WHERE task_id=? AND account_id=?',
                                (tid, aid)).fetchone()
        return row['phase'] if row else 'pending'

    def snapshot(self, tid):
        task = self.store.get(tid)
        if not task or task.kind != 'one_stop':
            raise ValueError('一条龙任务不存在')
        data = task.public()
        data['live'] = self._live(tid)
        data['phases'] = {r['phase']: r['n'] for r in self.conn.execute(
            'SELECT phase,COUNT(*) n FROM one_stop_items WHERE task_id=? GROUP BY phase', (tid,))}
        return data

    def results(self, tid, limit=500, offset=0):
        return [dict(r) for r in self.conn.execute('''
            SELECT t.target, COALESCE(t.account_id,i.account_id) account_id,
                   t.status,t.detail,i.phase,i.error_code,i.retryable
            FROM task_targets t JOIN one_stop_items i
              ON i.task_id=t.task_id AND CAST(t.target AS INTEGER)=i.account_id
            WHERE t.task_id=? ORDER BY t.seq LIMIT ? OFFSET ?''', (tid, limit, offset))]

    def export_ids(self, tid):
        self.snapshot(tid)
        return [r['account_id'] for r in self.conn.execute(
            "SELECT account_id FROM one_stop_items WHERE task_id=? AND phase='ready' ORDER BY account_id", (tid,))]

    @staticmethod
    def failure(exc, phase):
        name = type(exc).__name__
        if phase in ('changing', 'uncertain'):
            return ('TWOFA_UNCERTAIN', False, '改密结果待核对：保留现场，勿重复改密', None)
        if name in ('AuthKeyUnregisteredError', 'SessionRevokedError', 'SessionUnauthorizedError'):
            return ('SESSION_INVALID', True, '会话已失效：核对后重新登录', 'unauthorized')
        if name in ('UserDeactivatedBanError', 'UserDeactivatedError', 'PhoneNumberBannedError'):
            return ('ACCOUNT_BANNED', False, '账号已停用，请核对账号状态', 'banned')
        if name == 'PasswordHashInvalidError' or '2FA不匹配' in str(exc):
            return ('TWOFA_MISMATCH', False, '2FA不匹配：核对当前密码后创建新任务', None)
        if '两步验证' in str(exc) or '当前2FA' in str(exc):
            return ('TWOFA_REQUIRED', False, '需要当前2FA：保存正确密码后创建新任务', None)
        if name == 'FloodWaitError':
            return ('RATE_LIMITED', False, 'Telegram 限流：等待平台指定时间后创建新任务', None)
        if name == 'TimeoutError':
            return ('TIMEOUT', True, '连接或取码超时，请检查代理与等待时间', None)
        if name in ('URLError', 'ConnectionError', 'OSError', 'ConnectionResetError'):
            return ('NETWORK_ERROR', True, '网络连接失败：检查代理后重试', None)
        if name in ('PhoneCodeExpiredError', 'PhoneCodeInvalidError'):
            return ('CODE_INVALID', True, '验证码无效或过期：重试获取新码', None)
        return ('OPERATION_FAILED', False, '操作失败：核对账号、代理与二验后创建新任务', None)

    def start(self, tid):
        if self.runner.is_running(tid):
            return
        task = self.store.get(tid)
        self.snapshot(tid)
        secret = self.conn.execute('SELECT password_enc FROM one_stop_runs WHERE task_id=?', (tid,)).fetchone()
        if not secret:
            raise ValueError('任务凭据记录缺失，请创建新任务')
        password = decrypt(self.manager.s.master_key, secret['password_enc']) if secret['password_enc'] else ''
        # Requeue only this workflow. Ready/uncertain checkpoints are handled
        # without replaying their remote effects by the handler below.
        # Claim the task before spawning; two simultaneous resume requests must
        # never create two workers for the same account set.
        with self._start_guard:
            if self.runner.is_running(tid):
                return
            lease = self._acquire(tid)
            try:
              with self.conn:
                # Claim BEFORE touching targets; a losing process changes nothing.
                cur = self.conn.execute(
                    "UPDATE tasks SET status='running', started_at=?, finished_at=NULL, note=NULL "
                    "WHERE id=? AND status IN ('pending','failed','stopped','done')",
                    (time.time(), tid))
                if cur.rowcount != 1:
                    raise ValueError('任务状态仍在运行，请先恢复中断记录')
                self.conn.execute("UPDATE task_targets SET status='pending' "
                                  "WHERE task_id=? AND status!='ok' "
                                  "AND EXISTS (SELECT 1 FROM one_stop_items i "
                                  "WHERE i.task_id=task_targets.task_id "
                                  "AND CAST(i.account_id AS INTEGER)=CAST(task_targets.target AS INTEGER) "
                                  "AND i.phase IN ('pending','login','logged_in') "
                                  "AND COALESCE(i.retryable,1)=1)", (tid,))
              self._leases[tid] = lease
            except BaseException:
                if lease: lease.close()
                raise

        async def handle(row):
            aid = int(row['target'])
            phase = self.phase(tid, aid)
            def progress(message):
                self.store.mark_progress(row['id'], message, account_id=aid)
            try:
                if phase in ('changing', 'uncertain'):
                    self.phase(tid, aid, 'uncertain')
                    raise RuntimeError('改密结果待核对：保留现场，不重复改密；核对后创建新任务')
                if phase == 'ready':
                    return {'account_id': aid, 'detail': '已完成，未重复执行'}
                progress('验证已有会话')
                acc = self.manager.db.get(aid)
                valid = bool(acc and acc.session_enc and await asyncio.wait_for(
                    self.manager.login_session_authorized(aid), timeout=45))
                if not valid:
                    self.phase(tid, aid, 'login')
                    progress('自动登录')
                    result = await asyncio.wait_for(self.manager.auto_login(aid, progress=progress,
                        query_delay=task.params['query_delay'], poll_interval=task.params['poll_interval']),
                        timeout=max(task.params['query_delay'], task.params['poll_interval']) + 300)
                    if not isinstance(result, dict) or result.get('need_password') or result.get('ok') is not True:
                        raise RuntimeError('登录未完成：需要有效验证码或当前2FA')
                self.phase(tid, aid, 'logged_in')
                if password:
                    progress('修改2FA并保存')
                    self.phase(tid, aid, 'changing')
                    result = await asyncio.wait_for(
                        self.change(self.manager, aid, 'twofa', {'new': password}), timeout=self.change_timeout)
                    if not isinstance(result, dict) or result.get('has_password') is not True:
                        raise RuntimeError('改密结果未确认')
                self.phase(tid, aid, 'ready')
                with self.conn:
                    self.conn.execute('UPDATE one_stop_items SET error_code=NULL,retryable=NULL '
                                      'WHERE task_id=? AND account_id=?', (tid, aid))
                return {'account_id': aid, 'detail': '登录和改密已确认，可导出' if password else '登录已确认，可导出'}
            except Exception as exc:
                phase = self.phase(tid, aid)
                code, retryable, reason, status = self.failure(exc, phase)
                if phase in ('changing', 'uncertain'):
                    self.phase(tid, aid, 'uncertain')
                with self.conn:
                    self.conn.execute('UPDATE one_stop_items SET error_code=?,retryable=? '
                                      'WHERE task_id=? AND account_id=?', (code, int(retryable), tid, aid))
                try:
                    self.manager.db.update(aid, **({'status':status} if status else {}), status_note=reason,
                                           last_check_at=time.time())
                except Exception:
                    pass
                raise RuntimeError(reason) from None
        async def handle_with_connection(row):
            reuse = getattr(self.manager, 'reuse_login_client', None)
            # The lease owns the account lock and a newly verified connection.
            # Keep it only while this account is handled, including cancellation.
            async with (reuse(int(row['target'])) if reuse else nullcontext()):
                return await handle(row)

        # Fixed-size workers pipeline each account through login -> change -> ready.
        # No global wave barrier or speculative mass sends; passwords stay server-side.
        def release():
            try:
                if self.store.status(tid) in ('running', 'stopping'):
                    self.store.set_status(tid, 'failed', '执行中断，检查点已保留')
            finally:
                lease = self._leases.pop(tid, None)
                if lease: lease.close()
        try:
            spawned = self.runner.spawn(tid, handle_with_connection, concurrency=task.params['concurrency'],
                                        delay=0, target_timeout=None, on_finished=release)
            if not spawned:
                raise ValueError('spawn_failed')
        except BaseException as exc:
            try:
                self.store.set_status(tid, 'failed', '任务 worker 创建失败，请重试')
            finally:
                release()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ValueError('任务 worker 创建失败，请重试')

    def interrupt_on_startup(self):
        for task in self.store.list(limit=100000):
            if task.kind == 'one_stop' and task.status in ('pending', 'running', 'stopping'):
                try:
                    lease = self._acquire(task.id)
                except ValueError:
                    continue  # Another process still owns the running task.
                try:
                    # Ownership probe and recovery must stay inside the same lease.
                    if self.store.status(task.id) not in ('pending', 'running', 'stopping'):
                        continue
                    for row in self.store.pending(task.id):
                        self.store.mark_target(row['id'], 'fail', '服务重启，检查点已保留，可继续未完成项',
                                               code='task_cancelled')
                    self.store.set_status(task.id, 'failed', '服务重启，等待用户继续')
                finally:
                    if lease: lease.close()
