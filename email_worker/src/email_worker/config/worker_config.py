from pydantic import BaseModel
from resource_auxillary.config_mixins.stream_worker import StreamWorkersMixin


class StreamWorkersConfig(StreamWorkersMixin, BaseModel):
    pass
