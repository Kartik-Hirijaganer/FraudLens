"""Behavioral tests for the anonymous-pullability gate that fronts the Container Apps deploy.

The failure this gate exists to prevent is specific: a package the Container App cannot pull
anonymously fails later, inside Azure, as an opaque image-pull error against a revision that
already exists. A registry can refuse at either step — GHCR refuses an absent-or-private package
at the token endpoint — so both are exercised here and neither counts as a pass on its own.
"""

from __future__ import annotations

import httpx
import pytest

import check_public_image
from check_public_image import (
    PublicImageError,
    assert_anonymously_pullable,
    parse_image_reference,
)

_IMAGE = "ghcr.io/kartik-hirijaganer/fraudlens-backend:0123456789abcdef"
_TOKEN_BODY = {"token": "anonymous-token"}


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def _routes(*, manifest_status: int, token_status: int = 200, token_body: object = _TOKEN_BODY):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(token_status, json=token_body)
        return httpx.Response(manifest_status)

    return handler


def test_a_public_package_passes_and_reports_what_it_checked() -> None:
    with _client(_routes(manifest_status=200)) as client:
        reference = assert_anonymously_pullable(client, _IMAGE)
    assert reference.registry == "ghcr.io"
    assert reference.repository == "kartik-hirijaganer/fraudlens-backend"
    assert reference.tag == "0123456789abcdef"


def test_a_private_package_fails_even_though_the_token_endpoint_answered() -> None:
    # Either step can be the one that refuses, so neither may be trusted alone: a token that is
    # granted for a package the registry will not actually serve must still fail the gate.
    with _client(_routes(manifest_status=401)) as client, pytest.raises(PublicImageError) as error:
        assert_anonymously_pullable(client, _IMAGE)
    assert "not anonymously pullable" in str(error.value)


def test_the_failure_tells_the_operator_to_publish_rather_than_change_registry() -> None:
    with _client(_routes(manifest_status=403)) as client, pytest.raises(PublicImageError) as error:
        assert_anonymously_pullable(client, _IMAGE)
    message = str(error.value)
    assert "make the package public" in message
    assert "do not switch registries" in message


def test_the_manifest_is_requested_with_no_layer_download_and_every_media_type() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            assert "authorization" not in request.headers, "the pull token must be anonymous"
            return httpx.Response(200, json=_TOKEN_BODY)
        seen["method"] = request.method
        seen["accept"] = request.headers["accept"]
        return httpx.Response(200)

    with _client(handler) as client:
        assert_anonymously_pullable(client, _IMAGE)
    assert seen["method"] == "HEAD"
    assert "application/vnd.oci.image.index.v1+json" in str(seen["accept"])
    assert "application/vnd.docker.distribution.manifest.v2+json" in str(seen["accept"])


def test_a_token_refusal_carries_the_same_remediation_as_a_manifest_refusal() -> None:
    # GHCR creates a newly pushed package PRIVATE and refuses an anonymous token for it, so this
    # is the likeliest real failure — the operator must get the fix on this path too.
    with (
        _client(_routes(manifest_status=200, token_status=403)) as client,
        pytest.raises(PublicImageError) as error,
    ):
        assert_anonymously_pullable(client, _IMAGE)
    message = str(error.value)
    assert "refused an anonymous pull token" in message
    assert "make the package public" in message
    assert "do not switch registries" in message


def test_a_token_response_without_a_token_is_not_treated_as_success() -> None:
    with (
        _client(_routes(manifest_status=200, token_body={})) as client,
        pytest.raises(PublicImageError, match="no anonymous pull token"),
    ):
        assert_anonymously_pullable(client, _IMAGE)


@pytest.mark.parametrize(
    "image",
    ["fraudlens-backend:latest", "ghcr.io/owner/name", "ghcr.io/owner/name:", "localhost/name:tag"],
)
def test_an_ambiguous_reference_is_refused_before_any_request(image: str) -> None:
    with pytest.raises(PublicImageError):
        parse_image_reference(image)


def test_the_cli_exits_non_zero_when_the_registry_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unreachable(*args: object, **kwargs: object) -> httpx.Client:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(check_public_image.httpx, "Client", unreachable)
    assert check_public_image.main([_IMAGE]) == 1


def _patch_client(monkeypatch: pytest.MonkeyPatch, *, manifest_status: int) -> None:
    """Bind the CLI's client to a mock transport, keeping a reference to the real class."""
    real_client = httpx.Client
    monkeypatch.setattr(
        check_public_image.httpx,
        "Client",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(_routes(manifest_status=manifest_status))
        ),
    )


def test_the_cli_returns_zero_for_a_public_package(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, manifest_status=200)
    assert check_public_image.main([_IMAGE]) == 0


def test_the_cli_returns_non_zero_for_a_private_package(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, manifest_status=401)
    assert check_public_image.main([_IMAGE]) == 1
