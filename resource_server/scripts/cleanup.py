'''Hard deletes resources from the database that are past their audit/recovery period
Resources deleted:
- Non-RTFB users
- forums
- forum_admins
- comments
- posts
- post_saves, post_votes, post_reports (Cascaded from post deletion)

Note: This can be quite a heavy action depending on how many cascades it ends up in, based on the RTBF flag and the amount of posts+comments per deleted user, as well as the amount of comments for them too.

This script is not meant to be run continuously like its batch worker counterparts, but rather once a day only
'''
import os
from dotenv import load_dotenv
import psycopg2 as pg
from datetime import datetime, timedelta
from traceback import format_exc

loaded = load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
if not loaded:
    raise FileNotFoundError()

CONNECTION_KWARGS : dict[str, int | str] = {
    "user" : os.environ["POSTGRES_USERNAME"],
    "password" : os.environ["POSTGRES_PASSWORD"],
    "host" : os.environ["POSTGRES_HOST"],
    "port" : int(os.environ["POSTGRES_PORT"]),
    "database" : os.environ["POSTGRES_DATABASE"]
}


CONNECTION : pg.extensions.connection = pg.connect(**CONNECTION_KWARGS)
now: datetime = datetime.now()
CURRENT_DAY: datetime = datetime(now.year, now.month, now.day)
AUDIT_THRESHOLD: datetime = CURRENT_DAY - timedelta(days=int(os.environ['AUDIT_THRESHOLD']))
USER_AUDIT_THRESHOLD: datetime = CURRENT_DAY - timedelta(days=int(os.environ['ACCOUNT_RECOVERY_PERIOD']))
DELETION_SQL: str = 'DELETE FROM {tablename} WHERE id IN ({placeholder});'

with CONNECTION:
    with CONNECTION.cursor() as dbCursor:
        dbCursor.execute('BEGIN TRANSACTION')
        for idx, table in enumerate(('posts', 'comments', 'forums')):
            dbCursor.execute(f'SAVEPOINT {idx}')
            try:
                dbCursor.execute(f'SELECT id FROM {table} WHERE time_deleted < %s FOR UPDATE NOWAIT SKIP LOCKED;', (AUDIT_THRESHOLD,))
                _res: tuple[tuple[int]] = dbCursor.fetchall()
                if not _res:
                    continue
                _res: tuple[str] = tuple(map(lambda x : str(x[0]), _res))

                placeholder_string: str = ','.join(['%s'] * len(_res))
                dbCursor.execute(DELETION_SQL.format(table=table, placeholder=placeholder_string),
                                 vars=(*_res,))
                CONNECTION.commit()
            except pg.Error:
                dbCursor.execute(f'ROLLBACK TO {idx}')
                print(f'[HARD DELETION JOB]: Exception encountered for table {table}. Details:\n{format_exc()}')
        
        dbCursor.execute('END TRANSACTION')
        
        # NOTE: This would delete forum admin records where the user is NOT an owner, since those records where deleted through a cascade on hard deletion on forums
        dbCursor.execute('''SELECT user_id, forum_id FROM forum_admins fa
                         INNER JOIN users ON
                         users.id = fa.user_id
                         WHERE users.deleted_at < %s
                         FOR UPDATE NOWAIT SKIP LOCKED;
                         ''', vars=(AUDIT_THRESHOLD,))
        
        _res: tuple[tuple[int, int]] = dbCursor.fetchall()
        _res: tuple[tuple[str, str]] = tuple(map(lambda x : (str(x[0]), str(x[1])), _res))

        if _res:
            placeholder_string = ','.join(['%s', '%s'] * len(_res))
            _flattend_res : tuple[str] = [val for pairs in _res for val in pairs]
            dbCursor.execute('DELETE FROM forum_admins WHERE (user_id, forum_id) IN ({placeholder});'.format(placeholder_string),
                            vars=(*_flattend_res,))
            CONNECTION.commit()

        # Hard delete non-RTFB users past the recovery period. NOTE: Ideally a non-RTFB user's posts and comments will be soft deleted like usual resources anyways
        dbCursor.execute('''
                         SELECT id FROM users
                         WHERE rtfb IS true AND deleted_at < %s
                         FOR UPDATE NOWAIT SKIP LOCKED;
                         ''', vars=(USER_AUDIT_THRESHOLD,))
        _res: tuple[tuple[int]] = dbCursor.fetchall()
        if _res:
            _res: tuple[int] = tuple(map(lambda x: x[0], _res))
            placeholder_string = ','.join(["%s" * len(_res)])
            dbCursor.execute("DELETE FROM users WHERE id IN ({})".format(placeholder_string),
                             vars=(*_res,))

            CONNECTION.commit()