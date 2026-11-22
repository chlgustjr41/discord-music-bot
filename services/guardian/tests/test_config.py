"""Settings.from_env: the Docker endpoint selection is validated at startup."""

import pytest

from guardian.config import Settings


def test_docker_api_url_unset_keeps_the_unix_socket(base_env):
    settings = Settings.from_env()
    assert settings.docker_api_url == ""
    assert settings.docker_socket == "/var/run/docker.sock"


@pytest.mark.parametrize("blank", ["", "   "])
def test_docker_api_url_blank_counts_as_unset(base_env, blank):
    # compose passes optional vars through as ${VAR:-}, i.e. SET but empty.
    base_env.setenv("DOCKER_API_URL", blank)
    assert Settings.from_env().docker_api_url == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://socket-proxy:2375", "http://socket-proxy:2375"),
        ("http://socket-proxy:2375/", "http://socket-proxy:2375"),
        ("  http://socket-proxy:2375\n", "http://socket-proxy:2375"),
        ("HTTP://Socket-Proxy:2375", "http://socket-proxy:2375"),
        ("http://socket-proxy", "http://socket-proxy"),
        ("http://127.0.0.1:2375", "http://127.0.0.1:2375"),
        ("http://[::1]:2375", "http://[::1]:2375"),
    ],
)
def test_docker_api_url_accepts_plain_http_host(base_env, raw, expected):
    base_env.setenv("DOCKER_API_URL", raw)
    assert Settings.from_env().docker_api_url == expected


@pytest.mark.parametrize(
    "bad",
    [
        "https://socket-proxy:2375",        # TLS is not what the proxy speaks
        "tcp://socket-proxy:2375",          # DOCKER_HOST spelling, not a base URL
        "unix:///var/run/docker.sock",      # that is DOCKER_SOCKET's job
        "socket-proxy:2375",                # no scheme
        "//socket-proxy:2375",              # no scheme
        "http://",                          # no host
        "http://:2375",                     # no host
        "http:///containers",               # no host
        "http://socket-proxy:notaport",     # unparseable port
        "http://socket-proxy:99999",        # port out of range
        "http://socket proxy:2375",         # not a hostname
        "http://socket-proxy:2375/v1.43",   # a path prefix would defeat the allowlist
        "http://socket-proxy:2375?x=1",     # query
        "http://socket-proxy:2375#frag",    # fragment
        "http://user:pw@socket-proxy:2375", # credentials in a plain-HTTP URL
    ],
)
def test_docker_api_url_rejects_anything_else(base_env, bad):
    base_env.setenv("DOCKER_API_URL", bad)
    with pytest.raises(ValueError, match="DOCKER_API_URL"):
        Settings.from_env()
