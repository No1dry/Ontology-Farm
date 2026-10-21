"""Administrator/deployment-owned fixed adapters; no URL-forwarding endpoint.

Register at application bootstrap. Credentials stay in the adapter closure;
only its exact credential-free public template is accepted in public commands.
The adapter receives integer XYZ, never a caller-provided URL/path/headers.
"""
from collections.abc import Callable
from urllib.parse import urlsplit

from lzo.errors import ApiError

_providers: dict[str, Callable[[int, int, int], bytes]] = {}


def register_online_template(template: str, adapter: Callable[[int, int, int], bytes]):
    url = urlsplit(template)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or
            url.query or url.fragment or not all('{' + p + '}' in url.path for p in ('z', 'x', 'y'))):
        raise ValueError('Provider template must be fixed, public HTTPS XYZ without credentials')
    if template in _providers:
        raise ValueError('Provider template already registered')
    _providers[template] = adapter


def provider(template):
    if template not in _providers:
        raise ApiError('MAP_PROVIDER_UNREGISTERED', 422, 'Select an administrator-registered map provider')
    return _providers[template]
