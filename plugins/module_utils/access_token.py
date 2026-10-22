# -*- coding: utf-8 -*-
# Copyright (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

SSO_TOKEN_URL = "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token"

# sso.redhat.com and api.openshift.com occasionally return a transient
# error (429/5xx, or a connection failure) instead of a real response.
# Retry those a handful of times with backoff before giving up, instead of
# letting the caller crash on the first hiccup. This session is shared by
# every HTTP verb used by this collection (GET for polling, POST for the
# SSO token exchange/install actions, PATCH for host updates). urllib3's
# Retry only retries idempotent methods by default (GET/HEAD/PUT/DELETE/
# OPTIONS/TRACE), so POST and PATCH have to be added explicitly via
# allowed_methods - passing allowed_methods as a plain set instead of
# extending the default would otherwise silently *drop* GET/HEAD/etc.
# from the retryable set rather than adding to it.
#
# raise_on_status=False: with the default (True), once retries are used up
# on a status in status_forcelist, urllib3 raises MaxRetryError (which
# requests turns into requests.exceptions.RetryError) instead of returning
# the final response. During a sustained outage that means the caller gets
# an unhandled traceback and safe_json() never runs - exactly the crash
# class this module exists to prevent. With raise_on_status=False, urllib3
# returns the final (failed) response instead, so safe_json()/the
# "http_status" in data guard can report it as a clean module failure.
# Connection-level failures (DNS, reset, timeout) still raise after
# retries are exhausted regardless of this setting - callers should catch
# requests.exceptions.RequestException around their HTTP calls.
_RETRY = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=Retry.DEFAULT_ALLOWED_METHODS | frozenset({"POST", "PATCH"}),
    raise_on_status=False,
)

# Used for calls where the HTTP verb is not idempotent and retrying a
# timed-out/5xx request risks double-applying the operation (e.g. POST
# /clusters, which has no uniqueness constraint on name and will happily
# create a second cluster if retried after the first attempt already
# succeeded server-side but the response was lost/delayed). This only
# retries the methods urllib3 considers safe to retry by default
# (GET/HEAD/PUT/DELETE/OPTIONS/TRACE) - POST and PATCH are deliberately
# left out. Connection-level retries (the request never reached the
# server) still happen for every method regardless of allowed_methods.
_RETRY_SAFE = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=Retry.DEFAULT_ALLOWED_METHODS,
    raise_on_status=False,
)


def _get_session(retry_non_idempotent=True):
    """Build a requests.Session with retry/backoff for transient HTTP errors.

    Shared by every module in this collection so that both the SSO token
    endpoint and the Assisted Installer API get the same resilience.

    Set retry_non_idempotent=False for calls where retrying a non-idempotent
    method (POST/PATCH) after a transient status error risks double-applying
    the operation server-side (e.g. POST /clusters). GET/HEAD/PUT/DELETE/
    OPTIONS/TRACE are always retried on a transient status either way.
    """
    session = requests.Session()
    retry = _RETRY if retry_non_idempotent else _RETRY_SAFE
    session.mount("https://", HTTPAdapter(max_retries=retry))
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
