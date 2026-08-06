from argparse import ArgumentParser


def _validate_positive_task_count(arg: str) -> int:
    count = int(arg)
    if count <= 0:
        raise ValueError("Invalid task count, must be positive integer")
    return count


def get_argument_parser() -> ArgumentParser:
    argparser: ArgumentParser = ArgumentParser(
        prog="email_worker_cli", description="CLI for starting background email workers"
    )

    argparser.add_argument(
        "--task_count", default=1, type=_validate_positive_task_count
    )

    return argparser
