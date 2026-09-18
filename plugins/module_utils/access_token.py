# -*- coding: utf-8 -*-
# Copyright (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
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


def main():
    print(_get_access_token("REPLACEME"))


if __name__ == "__main__":
    main()
