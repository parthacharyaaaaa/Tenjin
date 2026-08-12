from argparse import ArgumentParser, Namespace
import asyncio
import sys
from typing import Sequence

from email_worker.bootup import spawn_tasks
from email_worker.cli import get_argument_parser
from email_worker.src.email_worker.config.worker_config import WorkerCountConfig


async def main(args: Sequence[str]) -> int:
    parser: ArgumentParser = get_argument_parser()
    parsed_args: Namespace = parser.parse_args(args)

    worker_count_config: WorkerCountConfig = WorkerCountConfig.construct_from_toml(
        parsed_args.config_file
    )
    await spawn_tasks(worker_count_config)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
