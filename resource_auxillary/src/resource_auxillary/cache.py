from resource_auxillary.strings import NAME_SEPERATOR, Action, IntentFlag


def create_intent_flag(
    entity: str,
    action: str,
    user_identifier: str,
    resource_identifier: str,
) -> str:
    return NAME_SEPERATOR.join((entity, action, user_identifier, resource_identifier))


def derive_cache_key(resource_name: str, identifier: str | int) -> str:
    return NAME_SEPERATOR.join((resource_name, str(identifier)))


def derive_hashmap_name(resource_name: str, action: Action) -> str:
    return NAME_SEPERATOR.join((resource_name, action))
