"""Voice dispatch onto PlayerService, and voice command-history logging."""

from tests.conftest import FakeRepo


async def test_fake_repo_records_source_and_transcript():
    repo = FakeRepo()
    await repo.log_command("1", "play", "x", "Me", "42")
    await repo.log_command(
        "1", "play", "x", "Me", "42", source="voice", transcript="play x"
    )
    assert repo.command_log[0][4:] == ("discord", "")
    assert repo.command_log[1][4:] == ("voice", "play x")
