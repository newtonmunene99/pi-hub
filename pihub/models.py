"""Typed shapes of the JSON documents pi-hub reads and writes.

These are ``TypedDict``s rather than dataclasses on purpose: the config is
JSON on disk and in the API, and it is merged key-by-key when the settings
page saves. TypedDicts give the type checker the exact keys while the values
stay plain dicts that ``json`` can read and write directly.
"""

from typing import Any, NotRequired, Required, TypedDict


class Service(TypedDict, total=False):
    """One service after ``config.normalise``.

    The ``Required`` keys are always present once normalised. The rest are
    only present when set; ``apiKey`` and ``password`` hold either a literal
    secret or an ``env:``/``file:`` reference.
    """

    id: Required[str]
    name: Required[str]
    kind: Required[str]
    category: Required[str]
    scheme: Required[str]
    host: Required[str]
    port: Required[str]
    basePath: Required[str]
    linkPath: Required[str]
    url: Required[str]
    description: Required[str]
    icon: Required[str]
    pinned: Required[bool]
    apiKey: str
    password: str
    username: str
    log: str


class Config(TypedDict):
    """A whole normalised config.json."""

    title: str
    pollSeconds: int
    categories: list[str]
    system: dict[str, Any]
    schedule: list[dict[str, Any]]
    services: list[Service]


class Info(TypedDict):
    """Slow-changing details an integration fetches every few minutes.

    ``at`` is added by the hub: the time the info was fetched.
    """

    version: str | None
    extra: dict[str, Any]
    at: NotRequired[float]
