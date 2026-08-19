from collections import OrderedDict
from dataclasses import dataclass
import uuid


MAX_MEDIA_REQUESTS = 1000


@dataclass(frozen=True)
class MediaRequest:
    url: str
    title: str = ""


_requests: OrderedDict[str, MediaRequest] = OrderedDict()


def save_media_request(url: str, title: str = "") -> str:
    """Сохраняет источник конкретной клавиатуры и возвращает callback-safe ID."""
    request_id = uuid.uuid4().hex[:12]
    _requests[request_id] = MediaRequest(url=url, title=title)
    _requests.move_to_end(request_id)

    while len(_requests) > MAX_MEDIA_REQUESTS:
        _requests.popitem(last=False)

    return request_id


def get_media_request(request_id: str | None) -> MediaRequest | None:
    if not request_id:
        return None

    request = _requests.get(request_id)
    if request:
        _requests.move_to_end(request_id)
    return request
