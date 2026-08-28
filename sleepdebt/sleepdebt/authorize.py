"""One-time OAuth2 authorisation. Run once, locally, to mint a refresh token.

    python -m sleepdebt.authorize

Opens Oura's consent page, catches the redirect on localhost, exchanges the
code, and prints the refresh token plus the export lines to paste into your
shell or secret store.

Nothing is written to disk. The client secret is read from the environment and
the refresh token only ever reaches stdout, so neither ends up in a file that
could be committed.

BEFORE RUNNING: the redirect URI below must be registered *exactly* on your
application in the Oura developer console, or Oura rejects the request. Default
is http://localhost:8731/callback — add that to the app, or change
oura.redirect_uri in config.yaml to whatever you registered.
"""

from __future__ import annotations

import argparse
import http.server
import secrets
import socket
import sys
import threading
import urllib.parse
import webbrowser
from typing import Optional

import requests

from . import config

AUTH_URL = "https://cloud.ouraring.com/oauth/authorize"


class _Catcher(http.server.BaseHTTPRequestHandler):
    """Single-shot handler for the redirect. Records the query, then stops."""
    result: dict = {}

    def do_GET(self):                                   # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catcher.result = {k: v[0] for k, v in q.items()}
        ok = "code" in _Catcher.result
        body = ("<h2>Authorised.</h2><p>Close this tab and return to the terminal.</p>"
                if ok else
                f"<h2>Authorisation failed.</h2><pre>{_Catcher.result}</pre>")
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_a):                         # silence access logs
        pass


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-browser", action="store_true",
                    help="print the URL instead of opening it")
    a = ap.parse_args(argv)

    cfg = config.load()
    o = cfg.raw["oura"]
    redirect = o.get("redirect_uri", "http://localhost:8731/callback")
    scope = o.get("scope", "daily personal")
    try:
        client_id = cfg.env("OURA_CLIENT_ID")
        client_secret = cfg.env("OURA_CLIENT_SECRET")
    except config.ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 1

    parsed = urllib.parse.urlparse(redirect)
    port = parsed.port or 80
    if not _port_free(port):
        print(f"port {port} is already in use — free it or change "
              f"oura.redirect_uri in config.yaml", file=sys.stderr)
        return 1

    state = secrets.token_urlsafe(24)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": redirect, "scope": scope, "state": state})

    srv = http.server.HTTPServer(("127.0.0.1", port), _Catcher)
    threading.Thread(target=srv.handle_request, daemon=True).start()

    print(f"\nredirect_uri : {redirect}")
    print(f"scope        : {scope}")
    print("\nThis exact redirect_uri must be registered on your app in the Oura")
    print("developer console, or Oura will refuse the request.\n")
    if a.no_browser:
        print(f"Open this URL:\n\n{url}\n")
    else:
        print("Opening your browser…\n")
        webbrowser.open(url)
    print("waiting for the redirect (ctrl-c to abort) …")

    try:
        while not _Catcher.result:
            threading.Event().wait(0.25)
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 1
    finally:
        srv.server_close()

    res = _Catcher.result
    if "code" not in res:
        print(f"\nauthorisation failed: {res}", file=sys.stderr)
        if res.get("error") == "invalid_scope":
            print("the scope string was rejected — check the scopes enabled on "
                  "your app and set oura.scope in config.yaml", file=sys.stderr)
        return 1
    if res.get("state") != state:
        # A mismatched state means the response did not come from the request
        # we made. Refuse it rather than exchanging a code we cannot vouch for.
        print("\nstate mismatch — refusing to exchange this code", file=sys.stderr)
        return 1

    try:
        r = requests.post(o["token_url"], timeout=30, data={
            "grant_type": "authorization_code", "code": res["code"],
            "redirect_uri": redirect, "client_id": client_id,
            "client_secret": client_secret})
    except requests.RequestException as exc:
        print(f"\ncould not reach {o['token_url']}: {type(exc).__name__}",
              file=sys.stderr)
        print("check your network, and any proxy or VPN that might be blocking "
              "api.ouraring.com", file=sys.stderr)
        return 1
    if r.status_code != 200:
        print(f"\ntoken exchange failed ({r.status_code}): {r.text[:300]}", file=sys.stderr)
        return 1
    tok = r.json()
    if "refresh_token" not in tok:
        print(f"\nno refresh_token in the response: {sorted(tok)}", file=sys.stderr)
        return 1

    print("\n" + "=" * 64)
    print("  AUTHORISED — put this in your environment, not in config.yaml")
    print("=" * 64)
    print(f'\nexport OURA_REFRESH_TOKEN="{tok["refresh_token"]}"\n')
    print(f"access token expires in {tok.get('expires_in', '?')}s; the job refreshes")
    print("it on every run, so only the refresh token needs storing.\n")
    print("Next: python -m sleepdebt.oura --verify\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
