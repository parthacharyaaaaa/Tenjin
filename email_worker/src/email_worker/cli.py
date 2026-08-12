from argparse import ArgumentParser
import os


def _validate_file_existence(arg: str) -> str:
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

    argparser.add_argument("--config-file", "-c", type=_validate_file_existence)

    return argparser
