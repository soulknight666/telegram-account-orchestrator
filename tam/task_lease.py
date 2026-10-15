"""Local SQLite task ownership; OS releases byte locks on process exit.

Lock files contain no account data. Do not unlink them: that would allow another
process to lock a different inode while an existing owner is still running.
"""
import os
from pathlib import Path


class TaskLease:
    def __init__(self, database, task_id):
        root = Path(str(database) + '.task-locks')
        root.mkdir(parents=True, exist_ok=True)
        self.file = (root / f'{int(task_id)}.lock').open('a+b')
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            if exc.errno in (11, 13, 35, 36):
                raise BlockingIOError('task_owned') from None
            raise

    def close(self):
        if not self.file.closed:
            self.file.close()
