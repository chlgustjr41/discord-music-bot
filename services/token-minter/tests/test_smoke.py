import minter


def test_package_has_version() -> None:
    assert minter.__version__ == "2.0.0"
