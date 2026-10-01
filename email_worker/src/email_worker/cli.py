import os
from argparse import ArgumentParser


def _validate_toml_file(arg: str) -> str:
    arg = arg.strip()
    if not arg.endswith(".toml"):
        raise ValueError("Config file must be in TOML format")

    if not os.path.exists(arg):
        raise FileNotFoundError(f"No such path found: {arg}")
    return arg


def get_argument_parser() -> ArgumentParser:
    argparser: ArgumentParser = ArgumentParser(
        prog="email_worker_cli", description="CLI for starting background email workers"
    )

    argparser.add_argument(
        "worker_config_filepath",
        help="TOML filepath for worker count configurations",
        type=_validate_toml_file,
    )

    argparser.add_argument(
        "config-file",
        help="TOML filepath for general configurations",
        type=_validate_toml_file,
    )

    return argparser
