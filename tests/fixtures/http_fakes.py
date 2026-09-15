"""Shared minimal HTTP response fake for backend adapter tests."""


class _HttpResponse:
    """Small context-manager response double for urllib.request.urlopen tests."""

    status = 204

    def __enter__(self) -> "_HttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b"ok"
