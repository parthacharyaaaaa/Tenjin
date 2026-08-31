from argparse import ArgumentParser, Namespace
import os
from typing import Iterable


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
        "worker_config_filepath",
        help="TOML filepath for worker count config",
        type=_check_file_existence,
    )

    arg_parser.add_argument(
        "--stream",
        "-s",
        help="Read stream config and boot-up stream workers",
        action="store_true",
    )

    arg_parser.add_argument(
        "--counter",
        "-c",
        help="Read counter config and boot-up counter workers",
        action="store_true",
    )

    return arg_parser


def parse_args(argparser: ArgumentParser, args: Iterable[str]) -> Namespace:
    """
    Abstraction wrapping around argparse.parse_args to fit
    additional validation/conversion logic
    """
    parsed_args: Namespace = argparser.parse_args(args)

    if not (parsed_args.stream or parsed_args.counter):
        raise ValueError(
            "At least one of the worker-type arguments must be provided, see --help for more"
        )

    return parsed_args
