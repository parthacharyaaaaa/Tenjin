from email.message import EmailMessage

from resource_auxillary.events import StreamedEvent


def construct_email_message(event: StreamedEvent) -> EmailMessage: ...
