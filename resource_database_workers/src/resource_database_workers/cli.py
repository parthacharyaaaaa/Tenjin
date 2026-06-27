from argparse import ArgumentParser, Namespace
from types import MappingProxyType
from typing import Final, Sequence

from resource_auxillary.strings import StreamName

from resource_database_workers.datastructures.processors import ProcessorName

_ALLOWED_STREAMS: Final[tuple[StreamName, ...]] = (
    StreamName.USER_INTERACTIONS,
    StreamName.DOWNSTREAM_DELETIONS,
    StreamName.DOWNSTREAM_COUNTER_DECREMENTS,
)

_TASK_INTEGRITY_MAPPING: Final[MappingProxyType[StreamName, ProcessorName]] = (
    MappingProxyType(
        {
            StreamName.DOWNSTREAM_COUNTER_DECREMENTS: ProcessorName.DOWNSTREAM_COUNTER_DECREMENTS,
            StreamName.DOWNSTREAM_DELETIONS: ProcessorName.DOWNSTREAM_DELETIONS,
        }
    )
)


def _parse_consumer_stream_arg(arg: str) -> dict[StreamName, int]:
    stream_data: dict[StreamName, int] = {}
    last_stream: StreamName | None = None
    for word in arg.split():
        try:
            stream_name: StreamName = StreamName(word)
        except ValueError:
            if not last_stream:
                raise ValueError(f"Unrecognized stream, {word}")
            if not word.isnumeric():
                raise ValueError(f"Non-numeric stream consumer count: {word}")
            stream_data[last_stream] = int(word)
            continue

        if stream_name in stream_data:
            raise ValueError(f"Stream {stream_name} duplicated")
        stream_data[stream_name] = 1

    return stream_data


def _parse_processing_tasks_arg(arg: str) -> dict[ProcessorName, int]:
    processor_data: dict[ProcessorName, int] = {}
    last_processor: ProcessorName | None = None
    for word in arg.split():
        try:
            processor_name: ProcessorName = ProcessorName(word)
        except ValueError:
            if not last_processor:
                raise ValueError(f"Unrecognized processor, {word}")
            if not word.isnumeric():
                raise ValueError(f"Non-numeric processor count: {word}")
            processor_data[last_processor] = int(word)
            continue

        if processor_name in processor_data:
            raise ValueError(f"Processor {processor_name} duplicated")
        processor_data[processor_name] = 1

    processor_data.setdefault(ProcessorName.DLQ, 1)
    return processor_data


def _parse_positive_int(arg: str) -> int:
    num: int = int(arg)
    if num < 1:
        raise ValueError(f"Must be positive integer, got {arg}")
    return num


def _check_pipeline_integrity(namespace: Namespace) -> None:
    for stream_name, processor_task in _TASK_INTEGRITY_MAPPING:
        if (
            stream_name in namespace.consumer_streams
            and processor_task not in namespace.processor_streams
        ):
            raise ValueError(
                " ".join(
                    (
                        "Pipeline group consuming stream:",
                        stream_name,
                        "must also have at least 1 processing task"
                        f"of type: {processor_task}",
                    )
                )
            )


def get_argument_parser() -> ArgumentParser:
    arg_parser: ArgumentParser = ArgumentParser(
        prog="consumers",
        description="CLI entrypoint for initiating an event pipelining group",
        exit_on_error=True,
    )

    arg_parser.add_argument(
        "consumer_streams",
        help=" ".join(
            (
                "Specify number of Redis consumer tasks to create",
                "Format: <stream name> <optional: number of consumers>",
                "Examples:",
                f"{StreamName.USER_INTERACTIONS} {StreamName.DOWNSTREAM_DELETIONS} 3",
            )
        ),
        nargs="+",
        type=_parse_consumer_stream_arg,
    )

    arg_parser.add_argument(
        "--processing_streams",
        "-ps",
        nargs="+",
        help=" ".join(
            (
                "Specify processing tasks to create",
                "Format: <stream name> <optional: number of consumers>",
                "Examples:",
                f"{ProcessorName.INSERTIONS} 4 {ProcessorName.DELETIONS}",
            )
        ),
        type=_parse_processing_tasks_arg,
        required=True,
    )

    arg_parser.add_argument(
        "--counters",
        "-c",
        help="Number of counter updation tasks to create",
        type=_parse_positive_int,
        default=0,
    )

    return arg_parser


def parse_args(args: Sequence[str]) -> Namespace:
    parser: ArgumentParser = get_argument_parser()
    parsed_args: Namespace = parser.parse_args(args)

    if (
        parsed_args.counters
        and ProcessorName.DLQ_COUNTERS not in parsed_args.processor_streams
    ):
        raise ValueError(
            " ".join(
                (
                    "Pipeline group updating counters:",
                    "must also have at least 1 processing task"
                    f"of type: {ProcessorName.DLQ_COUNTERS}",
                )
            )
        )

    _check_pipeline_integrity(parsed_args)

    return parsed_args
