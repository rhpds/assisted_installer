# -*- coding: utf-8 -*-
# Copyright (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

SSO_TOKEN_URL = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"

# sso.redhat.com occasionally returns a transient error (429/5xx, or a
# connection failure) instead of a token response. Retry those a handful of
# times with backoff before giving up, instead of letting the caller crash
# on the first hiccup. urllib3's Retry only retries idempotent methods by
# default, so POST has to be added explicitly via allowed_methods, otherwise
# this retry configuration would silently never fire for our POST request.
_RETRY = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods={"POST"},
)


def _get_session():
    """Build a requests.Session with retry/backoff for transient HTTP errors.

    Shared by every module in this collection so that both the SSO token
    endpoint and the Assisted Installer API get the same resilience.
    """
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=_RETRY))
    return session


def safe_json(response):
    """Parse a requests.Response body as JSON without ever raising.

    requests.Response.json() raises ValueError (JSONDecodeError is a
    subclass) when the body is empty or not valid JSON, which happens
    during transient outages/rate limiting on the upstream API. Modules
    should use this helper instead of calling response.json() directly so
    a bad response body results in a clear, actionable module failure
    instead of an unhandled exception.
    """
    try:
        return response.json()
    except ValueError:
        return {
            "http_status": response.status_code,
            "raw_body": (response.text or "")[:500],
        }


def _get_access_token(offline_token):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    params = {
        "grant_type": "refresh_token",
        "client_id": "cloud-services",
        "refresh_token": offline_token
    }
    session = _get_session()
    response = session.post(
        SSO_TOKEN_URL,
        headers=headers,
        data=params
    )
    return response


def get_access_token_data(offline_token):
    """Fetch a fresh access token and safely parse the response body.

    Returns a tuple of (response, data) where data is the result of
    safe_json(response): either the parsed token payload (containing
    access_token/expires_in on success), or a {http_status, raw_body}
    dict if the response body could not be parsed as JSON at all.
    """
    response = _get_access_token(offline_token)
    return response, safe_json(response)


# Red Hat SSO access tokens are typically valid for several minutes. Refresh
# a bit before actual expiry (clock skew / a slow round-trip) instead of
# calling the token endpoint on every single polling iteration - this both
# reduces load on sso.redhat.com and reduces how often a module is exposed
# to any transient errors it returns.
TOKEN_REFRESH_SKEW_SECONDS = 60
DEFAULT_TOKEN_LIFETIME_SECONDS = 300


class AccessTokenCache:
    """Caches an SSO access token for the duration of a single module run,
    only refreshing it once it is close to expiring instead of on every
    polling iteration.

    Shared by every module in this collection that polls the Assisted
    Installer API in a loop (install_cluster, wait_for_hosts, ...), so the
    caching behavior and the "fail cleanly on a bad token response" behavior
    only need to be implemented (and tested) once.
    """

    def __init__(self, module, offline_token):
        self._module = module
        self._offline_token = offline_token
        self._token = None
        self._expires_at = 0.0

    def get(self):
        if self._token is None or time.monotonic() >= self._expires_at:
            self._refresh()
        return self._token

    def _refresh(self):
        response, data = get_access_token_data(self._offline_token)
        if response.status_code != 200 or "access_token" not in data:
            self._module.fail_json(msg='Error getting access token ', **data)
        self._token = data["access_token"]
        expires_in = data.get("expires_in", DEFAULT_TOKEN_LIFETIME_SECONDS)
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            expires_in = DEFAULT_TOKEN_LIFETIME_SECONDS
        self._expires_at = time.monotonic() + max(expires_in - TOKEN_REFRESH_SKEW_SECONDS, 0)


def main():
    print(_get_access_token("REPLACEME"))


if __name__ == "__main__":
    main()
