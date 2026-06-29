from argparse import ArgumentParser, Namespace
import asyncio
import sys
from typing import Final, Sequence

from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from resource_database_workers.bootup import spawn_tasks
from resource_database_workers.cli import get_argument_parser
from resource_database_workers.config.config import AppConfig
from resource_database_workers.config.worker_config import WorkerSettings
from resource_database_workers.datastructures.queues import QueueRegistry
from resource_database_workers.dependencies import (
    get_app_redis,
    get_internal_redis,
    get_config,
    get_queue_registry,
    get_connection_pool,
    get_worker_settings,
)


async def main(args: Sequence[str]) -> None:
    parser: ArgumentParser = get_argument_parser()
    parsed_args: Namespace = parser.parse_args(args)

    worker_settings: WorkerSettings = get_worker_settings()
    worker_settings.update_toml_file(parsed_args.worker_config_filepath)

    app_redis: Final[Redis] = get_app_redis()
    internal_redis: Final[Redis] = get_internal_redis()
    app_config: Final[AppConfig] = get_config()
    queue_registry: Final[QueueRegistry] = get_queue_registry()
    pg_connection_pool: Final[AsyncConnectionPool] = get_connection_pool()

    await spawn_tasks(
        app_config,
        worker_settings,
        app_redis,
        internal_redis,
        queue_registry,
        pg_connection_pool,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
