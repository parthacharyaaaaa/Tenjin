import asyncio
import sys
from argparse import ArgumentParser, Namespace
from typing import Final, Sequence

from email_worker.bootup import spawn_tasks
from email_worker.cli import get_argument_parser
from email_worker.config.config import AppConfig
from email_worker.config.worker_config import StreamWorkersConfig


async def main(args: Sequence[str]) -> int:
    parser: ArgumentParser = get_argument_parser()
    parsed_args: Namespace = parser.parse_args(args)

    AppConfig.model_config["toml_file"] = parsed_args.config_file
    stream_workers_config: Final[StreamWorkersConfig] = (
        StreamWorkersConfig.construct_from_toml(parsed_args.worker_config_filepath)
    )

    await spawn_tasks(stream_workers_config)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
