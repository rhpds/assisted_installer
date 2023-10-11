# -*- coding: utf-8 -*-
# Copyright (c) 2023, Alberto Gonzalez <alberto.gonzalez@redhat.com>
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
import requests


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
    response = requests.post(
        "https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token",
        headers=headers,
        data=params
    )
    return response


def main():
    print(_get_access_token("REPLACEME"))


if __name__ == "__main__":
    main()
