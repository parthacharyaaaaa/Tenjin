import asyncio
import sys
from argparse import ArgumentParser, Namespace
from typing import Sequence

from email_worker.cli import get_argument_parser
from email_worker.config.config import AppConfig
from email_worker.dependencies.injections import get_app_config


async def main(args: Sequence[str]) -> int:
    parser: ArgumentParser = get_argument_parser()
    parsed_args: Namespace = parser.parse_args(args)

    if parsed_args.config_file is not None:
        AppConfig.model_config["toml_file"] = parsed_args.config_file

    from email_worker.bootup import spawn_tasks

    await spawn_tasks(get_app_config().WORKER_COUNT)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
