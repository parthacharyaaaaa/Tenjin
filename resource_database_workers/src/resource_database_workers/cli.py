from argparse import ArgumentParser
import os


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

    return arg_parser
