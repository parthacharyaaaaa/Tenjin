from argparse import ArgumentParser, Namespace
import os
from typing import Iterable

from resource_auxillary.strings import StreamName


def _check_file_existence(arg: str) -> str:
    if not os.path.exists(arg):
        raise FileNotFoundError(f"No such file: {arg}")
    if not (ext := arg.split(".")[-1]).endswith("toml"):
        raise ValueError(f"Config file should be a TOML file, got {ext}")
    return arg


def get_argument_parser() -> ArgumentParser:
    arg_parser: ArgumentParser = ArgumentParser(
        prog="consumers",
        description="CLI entrypoint for initiating an event pipelining group",
        exit_on_error=True,
    )

    arg_parser.add_argument(
        "worker_type", help="type of worker", choices=("counter", "stream")
    )

    arg_parser.add_argument(
        "worker_config_filepath",
        help="TOML filepath for worker count config",
        type=_check_file_existence,
    )

    arg_parser.add_argument(
        "--stream", help="Name of stream, if worker is a stream worker", type=StreamName
    )

    return arg_parser


def parse_args(argparser: ArgumentParser, args: Iterable[str]) -> Namespace:
    parsed_args: Namespace = argparser.parse_args(args)

    if parsed_args.worker_type == "stream" and not parsed_args.stream:
        raise ValueError("Missing stream name")
    elif parsed_args.worker_type != "stream" and parsed_args.stream:
        print("Ignoring irrelevant argument: ", parsed_args.stream)

    return parsed_args
