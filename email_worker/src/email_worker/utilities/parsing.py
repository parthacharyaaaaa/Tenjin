from resource_auxillary.datastructures.payloads.emails import UserEmailPayload
from resource_auxillary.events import StreamedEvent


def parse_user_email_payload(
    event: StreamedEvent,
) -> UserEmailPayload:
    return UserEmailPayload(
        recipient=event.payload["recipient"],
        sender=event.payload["sender"],
        body_kwargs=event.payload["body_kwargs"],
    )
