"""HTTPS origins that may appear as a prompt's Learn more link.

The client duplicates this list in config.js. A host that is not here is
stripped before it is saved, served, or assigned to an anchor.
"""

from urllib.parse import urlparse

ALLOWED_LEARN_MORE_HOSTS = frozenset({
    "investigating-search-interface.onrender.com",
})


def sanitise_learn_more_url(value):
    """Return the URL if it is https on an approved host, otherwise None."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme != "https":
        return None
    if parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_LEARN_MORE_HOSTS:
        return None
    return text
