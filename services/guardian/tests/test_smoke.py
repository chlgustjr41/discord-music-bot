import guardian


def test_package_has_version() -> None:
    assert guardian.__version__ == "2.0.0"
