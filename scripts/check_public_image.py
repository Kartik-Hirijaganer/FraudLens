"""Summary: Refuse a deploy whose backend image is not anonymously pullable. `acr_enabled = false`
means the Container App pulls from public GHCR with NO registry credential, so a package left
private fails inside Azure minutes later as an opaque image-pull error against a revision that is
already created. This gate proves pullability at build time, using no authorization header at all,
and names the fix — publish the package — rather than letting a deploy quietly switch registries.

Key classes:
- ImageReference: the registry, repository, and tag parsed from an image reference.
- PublicImageError: safe pullability failure carrying no credential and no token.

Key functions:
- parse_image_reference: split `ghcr.io/owner/name:tag` into registry, repository, and tag.
- anonymous_pull_token: request the registry's anonymous pull token for one repository.
- manifest_status: HEAD the manifest with that token and return the HTTP status.
- assert_anonymously_pullable: raise unless the manifest answers 200 without a credential.
- main: gate one image reference and print the remediation when it fails.

Notes:
- Both steps are checked because either can be the one that refuses. Verified live against GHCR
  on 2026-09-15: an absent-or-private package is refused at the TOKEN endpoint (403), while a
  public one mints a token and answers the manifest (`kartik-hirijaganer/fraudlens-base` ->
  200/200). A token alone is not treated as a pass, so a registry that hands one out for a
  package it will not serve still fails here rather than inside Azure.
- HEAD is used deliberately: the assertion needs the status, never a layer, so the check
  downloads nothing and costs nothing.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import httpx
from pydantic import BaseModel, ConfigDict, Field

_ACCEPT_MANIFESTS = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_TIMEOUT_SECONDS = 20.0
_HTTP_OK = 200


class PublicImageError(RuntimeError):
    """Raised when an image reference is malformed or is not anonymously pullable."""


class ImageReference(BaseModel):
    """One container image reference split into the parts a registry API needs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    registry: str = Field(..., min_length=1, description="Registry host, e.g. `ghcr.io`.")
    repository: str = Field(..., min_length=1, description="Repository path without the host.")
    tag: str = Field(..., min_length=1, description="Tag identifying the manifest to check.")


def parse_image_reference(image: str) -> ImageReference:
    """Split `<registry>/<repository>:<tag>` into its parts, refusing an ambiguous reference."""
    candidate = image.strip()
    registry, separator, remainder = candidate.partition("/")
    if not separator or "." not in registry:
        raise PublicImageError("image reference must be fully qualified as <registry>/<repo>:<tag>")
    repository, separator, tag = remainder.rpartition(":")
    if not separator or not repository or not tag or "/" in tag:
        raise PublicImageError("image reference must carry an explicit :<tag>")
    return ImageReference(registry=registry, repository=repository, tag=tag)


def anonymous_pull_token(client: httpx.Client, reference: ImageReference) -> str:
    """Return the registry's ANONYMOUS pull token; no credential is sent to obtain it."""
    response = client.get(
        f"https://{reference.registry}/token",
        params={"service": reference.registry, "scope": f"repository:{reference.repository}:pull"},
    )
    if response.status_code != _HTTP_OK:
        # The likeliest real failure: GHCR creates a newly pushed package PRIVATE, and refuses
        # an anonymous token for it. The remediation belongs here, not only on the manifest path.
        raise PublicImageError(
            f"{reference.registry} refused an anonymous pull token for "
            f"{reference.repository} (HTTP {response.status_code}). The package is private or "
            "absent; the Container App pulls with no credential because acr_enabled = false, so "
            "make the package public — do not switch registries to work around this."
        )
    token = str(response.json().get("token", ""))
    if not token:
        raise PublicImageError(f"{reference.registry} returned no anonymous pull token")
    return token


def manifest_status(client: httpx.Client, reference: ImageReference, token: str) -> int:
    """Return the HTTP status of an anonymous manifest HEAD for the referenced tag."""
    response = client.head(
        f"https://{reference.registry}/v2/{reference.repository}/manifests/{reference.tag}",
        headers={"Authorization": f"Bearer {token}", "Accept": _ACCEPT_MANIFESTS},
    )
    return response.status_code


def assert_anonymously_pullable(client: httpx.Client, image: str) -> ImageReference:
    """Raise unless the referenced manifest is readable with no registry credential."""
    reference = parse_image_reference(image)
    status = manifest_status(client, reference, anonymous_pull_token(client, reference))
    if status != _HTTP_OK:
        raise PublicImageError(
            f"{reference.registry}/{reference.repository}:{reference.tag} is not anonymously "
            f"pullable (manifest HTTP {status}). The Container App pulls with no credential "
            "because acr_enabled = false, so make the package public — do not switch registries "
            "to work around this."
        )
    return reference


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Assert a container image is pullable with no registry credential."
    )
    parser.add_argument("image", help="Fully qualified image reference, e.g. ghcr.io/o/n:sha")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Gate one image reference, printing the remediation instead of a registry response body."""
    args = _build_parser().parse_args(argv)
    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
            reference = assert_anonymously_pullable(client, args.image)
    except PublicImageError as error:
        print(f"public-image check failed: {error}")
        return 1
    except httpx.HTTPError:
        print("public-image check failed: the registry was unreachable")
        return 1
    target = f"{reference.registry}/{reference.repository}:{reference.tag}"
    print(f">> {target} is anonymously pullable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
