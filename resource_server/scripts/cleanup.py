'''Hard deletes resources from the database that are past their audit/recovery period
Resources deleted:
- Non-RTBF users
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
from redis import Redis

if __name__ == "__main__":
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

 
    # Connect to Postgres
    CONNECTION : pg.extensions.connection = pg.connect(**CONNECTION_KWARGS)
    del CONNECTION_KWARGS

    # Connect to Redis
    interface: Redis = Redis(os.environ['REDIS_HOST'], os.environ['REDIS_PORT'])

    # Initialize other constants
    now: datetime = datetime.now()
    CURRENT_DAY: datetime = datetime(now.year, now.month, now.day)
    AUDIT_THRESHOLD: datetime = CURRENT_DAY - timedelta(days=int(os.environ['AUDIT_THRESHOLD']))
    USER_AUDIT_THRESHOLD: datetime = CURRENT_DAY - timedelta(days=int(os.environ['ACCOUNT_RECOVERY_PERIOD']))
    DELETION_SQL: str = 'DELETE FROM {tablename} WHERE id IN ({placeholder});'

    with CONNECTION:
        with CONNECTION.cursor() as dbCursor:
            dbCursor.execute('BEGIN TRANSACTION')
            for idx, table in enumerate(('posts', 'comments')):
                dbCursor.execute(f'SAVEPOINT {idx}')
                try:
                    # The reason for the inner join on users is to distinguish normally deleted resources from those resources that are marked for deletion because of RTBF account deletions. The condition for time remains the same: Anything past the audit period is lunch
                    dbCursor.execute(f'''
                                     SELECT id FROM {table}
                                     WHERE time_deleted < %s
                                     INNER JOIN users on author_id = users.id AND ((users.rtbf IS false) OR (users.rtbf IS true AND users.deleted IS true AND users.time_deleted < %s))
                                     FOR UPDATE NOWAIT SKIP LOCKED;
                                     ''', (AUDIT_THRESHOLD, USER_AUDIT_THRESHOLD))
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

            # Delete forums with deleted users as owners, deletion past audit period. FK anime is selected to decrement Redis counters after deletion
            dbCursor.execute('''
                             SELECT id FROM forums
                             INNER JOIN forum_admins fa ON fa.forum_id = id AND fa.role = 'owner'
                             INNER JOIN users ON users.id = fa.user_id AND users.time_deleted < %s 
                             ''', vars=(USER_AUDIT_THRESHOLD,))
            _res: tuple[tuple[int, int]] = dbCursor.fetchall()
            
            placeholder_string: str = ','.join(["%s"] * len(_res))
            # This deletion will also cascade to forum_admins
            dbCursor.execute('''
                             DELETE FROM forums
                             WHERE id IN ({placeholder})
                             '''.format(placeholder=placeholder_string), vars=(_res,))
            CONNECTION.commit()


            #NOTE: Remove forum_admins records for admins who have deleted their account and are past the audit period
            dbCursor.execute('''
                             SELECT user_id, forum_id FROM forum_admins fa
                             INNER JOIN users ON
                             users.id = fa.user_id
                             WHERE users.deleted_at < %s
                             FOR UPDATE NOWAIT SKIP LOCKED;
                             ''', vars=(USER_AUDIT_THRESHOLD,))
            
            _res: tuple[tuple[int, int]] = dbCursor.fetchall()
            _res: tuple[tuple[str, str]] = tuple(map(lambda x : (str(x[0]), str(x[1])), _res))

            # Prepare decrement counters for forums:admins in Redis
            counter_name_template: str = 'forum:{forum_id}:admins'
            hashmap_name: str = 'forums:admins'
            forum_decrement_mapping: dict[int, int] = {}
            for entry in _res:
                if entry[1] in forum_decrement_mapping:
                    forum_decrement_mapping[entry[1]] -= 1
                else:
                    forum_decrement_mapping[entry[1]] = -1

            if _res:
                placeholder_string = ','.join(['%s', '%s'] * len(_res))
                _flattend_res : tuple[str] = [val for pairs in _res for val in pairs]
                dbCursor.execute('DELETE FROM forum_admins WHERE (user_id, forum_id) IN ({placeholder});'.format(placeholder_string),
                                vars=(*_flattend_res,))
                CONNECTION.commit()
            
            # Reflect in Redis
            with interface.pipeline() as pipe:
                for forumID, delta in forum_decrement_mapping.items():
                    counter_name: str = counter_name_template.format(forum_id=forumID)
                    pipe.hsetnx(hashmap_name, forumID, counter_name)    # Set new entry in forum:admins if not present
                    pipe.incrby(counter_name, delta)    # Decrement by delta
                pipe.execute()

            # Hard delete RTBF users past the recovery period
            dbCursor.execute('''
                            SELECT id FROM users
                            WHERE rtbf IS true AND deleted_at < %s
                            FOR UPDATE NOWAIT SKIP LOCKED;
                            ''', vars=(USER_AUDIT_THRESHOLD,))
            _res: tuple[tuple[int]] = dbCursor.fetchall()
            if _res:
                _res: tuple[int] = tuple(map(lambda x: x[0], _res))
                placeholder_string = ','.join(["%s" * len(_res)])
                dbCursor.execute("DELETE FROM users WHERE id IN ({})".format(placeholder_string),
                                vars=(*_res,))

                CONNECTION.commit()
