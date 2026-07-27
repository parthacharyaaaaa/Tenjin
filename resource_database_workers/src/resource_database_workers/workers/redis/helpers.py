"""General CPU-bound helper/utility functions"""


def get_min_max_from_xread(xread_ids: set[str]) -> tuple[str, str]:
    min_tuple = max_tuple = tuple(map(int, next(iter(xread_ids)).split("-")))
    normalized_ids = (tuple(map(int, record.split("-"))) for record in xread_ids)
    for id_tuple in normalized_ids:
        if (id_tuple[0] < min_tuple[0]) or (
            min_tuple[0] == id_tuple[0] and id_tuple[1] < min_tuple[1]
        ):
            min_tuple = id_tuple
        elif (id_tuple[0] > max_tuple[0]) or (
            id_tuple[0] == max_tuple[0] and id_tuple[1] > max_tuple[1]
        ):
            max_tuple = id_tuple
    return "-".join(str(i for i in min_tuple)), "-".join(str(i for i in max_tuple))
