from resource_auxillary.strings import NAME_SEPERATOR, Action, IntentFlag


def create_creation_intent_flag(
    flagname: IntentFlag,
    entity: str,
    action: str,
    user_identifier: str,
    resource_identifier: str,
) -> str:
    return NAME_SEPERATOR.join(
        (flagname, entity, action, user_identifier, resource_identifier)
    )


def derive_deletion_intent_flag(resource_name: str, identifier: str | int) -> str:
    return NAME_SEPERATOR.join(
        (
            IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
            resource_name,
            str(identifier),
        )
    )


def derive_cache_key(resource_name: str, identifier: str | int) -> str:
    return NAME_SEPERATOR.join((resource_name, str(identifier)))


def derive_hashmap_name(resource_name: str, action: Action) -> str:
    return NAME_SEPERATOR.join((resource_name, action))
