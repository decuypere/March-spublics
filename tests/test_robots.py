"""La garde robots.txt refuse par defaut quand elle ne peut pas conclure."""

from veille_mp.robots import RobotsGate
from tests.fakes import FakeHttpClient, FakeResponse

ROBOTS = """User-agent: *
Disallow: /api/
Allow: /public/
Crawl-delay: 5
"""


def gate(handler, **kwargs):
    return RobotsGate(FakeHttpClient(handler), **kwargs)


def serve_robots(method, url, kwargs):
    return FakeResponse(status_code=200, payload=None, text=ROBOTS,
                        headers={"Content-Type": "text/plain"})


def test_disallowed_path_is_refused_with_an_explicit_reason():
    allowed, reason = gate(serve_robots).describe("https://example.invalid/api/search")
    assert not allowed
    assert "interdit explicitement" in reason


def test_allowed_path_passes():
    allowed, reason = gate(serve_robots).describe("https://example.invalid/public/list")
    assert allowed and "autorise" in reason


def test_unreachable_robots_txt_fails_closed():
    def handler(method, url, kwargs):
        raise ConnectionError("proxy denied")

    allowed, reason = gate(handler).describe("https://example.invalid/api/search")
    assert not allowed
    assert "illisible" in reason and "par defaut" in reason


def test_forbidden_robots_txt_fails_closed():
    def handler(method, url, kwargs):
        return FakeResponse(status_code=403, payload=None, text="denied")

    assert gate(handler).allowed("https://example.invalid/anything") is False


def test_missing_robots_txt_means_everything_is_allowed():
    def handler(method, url, kwargs):
        return FakeResponse(status_code=404, payload=None, text="not found")

    assert gate(handler).allowed("https://example.invalid/api/search") is True


def test_crawl_delay_is_read():
    assert gate(serve_robots).crawl_delay("https://example.invalid/public/") == 5.0


def test_check_can_be_disabled_by_configuration():
    allowed, reason = gate(serve_robots, enabled=False).describe("https://example.invalid/api/x")
    assert allowed and "desactivee" in reason


def test_robots_txt_is_fetched_once_per_origin():
    calls = []

    def handler(method, url, kwargs):
        calls.append(url)
        return serve_robots(method, url, kwargs)

    guard = gate(handler)
    guard.allowed("https://example.invalid/public/a")
    guard.allowed("https://example.invalid/public/b")
    assert len(calls) == 1
