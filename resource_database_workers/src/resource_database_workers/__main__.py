from argparse import ArgumentParser, Namespace
import asyncio
import sys
from typing import Final, Sequence

from resource_database_workers.bootup import spawn_tasks
from resource_database_workers.cli import get_argument_parser, parse_args
from resource_database_workers.config.worker_config import (
    StreamWorkersConfig,
    CounterWorkersConfig,
)
from resource_database_workers.config.config import AppConfig
from resource_database_workers.dependencies.injections import get_config


async def main(args: Sequence[str]) -> None:
    parser: ArgumentParser = get_argument_parser()
    parsed_args: Namespace = parse_args(parser, args)

    app_config: Final[AppConfig] = get_config()

    stream_workers_config: StreamWorkersConfig | None = None
    counter_workers_config: CounterWorkersConfig | None = None
    if parsed_args.stream:
        stream_workers_config = StreamWorkersConfig.construct_from_toml(
            parsed_args.worker_config_filepath
        )
    if parsed_args.counter:
        counter_workers_config = CounterWorkersConfig.construct_from_toml(
            parsed_args.worker_config_filepath
        )
    await spawn_tasks(app_config, stream_workers_config, counter_workers_config)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
