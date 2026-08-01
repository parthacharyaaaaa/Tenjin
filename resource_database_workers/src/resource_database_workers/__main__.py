from argparse import ArgumentParser, Namespace
import asyncio
import sys
from typing import Sequence

from resource_database_workers.bootup import spawn_tasks
from resource_database_workers.cli import get_argument_parser, parse_args
from resource_database_workers.config.worker_config import (
    StreamWorkersConfig,
    CounterWorkersConfig,
)


async def main(args: Sequence[str]) -> None:
    parser: ArgumentParser = get_argument_parser()
    parsed_args: Namespace = parse_args(parser, args)
    config: StreamWorkersConfig | CounterWorkersConfig | None = None
    if parsed_args.worker_type == "stream":
        config = StreamWorkersConfig.construct_from_toml(
            parsed_args.worker_config_filepath, parsed_args.strean
        )
    else:
        config = CounterWorkersConfig.construct_from_toml(
            parsed_args.worker_config_filepath
        )
    await spawn_tasks(config)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
