# -*- coding: utf-8 -*-
"""Small HTTP helper for the live feeds: timeouts, retries, a user agent and,
for feeds with sticky sessions, one cookie jar per :class:`Session`."""

import http.cookiejar
import json
import time
import urllib.error
import urllib.request

from .. import __version__

USER_AGENT = f"lynch-forecast/{__version__} (python-urllib)"
TIMEOUT_S = 30
RETRIES = 3


class FeedError(RuntimeError):
    """A feed that could not be read; the message says why in plain words.
    ``code`` is the HTTP status when the server refused (else None)."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class Session:
    """GET/POST with one cookie jar (needed where the server pins a session)."""

    def __init__(self, timeout=TIMEOUT_S, retries=RETRIES):
        self.timeout, self.retries = timeout, retries
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, url, data=None, headers=None):
        hdr = {"User-Agent": USER_AGENT}
        hdr.update(headers or {})
        body = data.encode("utf-8") if isinstance(data, str) else data
        last, code = None, None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(url, data=body, headers=hdr)
                with self._opener.open(req, timeout=self.timeout) as r:
                    return r.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                last, code = f"HTTP {exc.code} {exc.reason}", exc.code
                if exc.code < 500 and exc.code != 429:
                    break                              # a refusal: retrying won't help
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last, code = str(getattr(exc, "reason", exc)), None
            time.sleep(min(2 ** attempt, 8))
        raise FeedError(f"{url.split('?')[0]} did not answer ({last})", code=code)

    def get(self, url, headers=None):
        return self.request(url, headers=headers)

    def get_json(self, url, headers=None):
        text = self.get(url, headers=headers)
        try:
            return json.loads(text)
        except ValueError:
            raise FeedError(f"{url.split('?')[0]} sent something that is not JSON: "
                            f"{text[:120]!r}") from None

    def post(self, url, data, headers=None):
        return self.request(url, data=data, headers=headers)
