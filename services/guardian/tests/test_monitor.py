"""Guardian policy: which probe outcomes trigger which actions/alerts."""

import pytest

import guardian.monitor as monitor_module
from guardian.monitor import Guardian
from guardian.probe import BotHealth, CanaryResult


class FakeActor:
    def __init__(self):
        self.restarts: list[str] = []
        self.outcome = "restarted"

    async def restart(self, service):
        self.restarts.append(service)
        return self.outcome


class FakeAlerter:
    def __init__(self):
        self.alerts: list[tuple[str, str]] = []
        self.resolutions: list[str] = []

    async def alert(self, playbook_id, detail=""):
        self.alerts.append((playbook_id, detail))
        return True

    async def resolved(self, playbook_id):
        self.resolutions.append(playbook_id)


class FakeSettings:
    lavalink_url = "http://lavalink:2333"
    lavalink_password = "pw"
    bot_health_url = "http://bot:8080/health"
    canary_query = "ytsearch:x"


@pytest.fixture
def guardian(monkeypatch):
    g = Guardian(FakeSettings(), session=None, actor=FakeActor(), alerter=FakeAlerter())

    def prime(canary: CanaryResult, bot: BotHealth):
        async def fake_canary(*args, **kwargs):
            return canary

        async def fake_bot(*args, **kwargs):
            return bot

        monkeypatch.setattr(monitor_module, "probe_canary", fake_canary)
        monkeypatch.setattr(monitor_module, "probe_bot", fake_bot)

    g.prime = prime
    return g


HEALTHY_BOT = BotHealth(ok=True, players={})
OK_CANARY = CanaryResult(reachable=True, ok=True)


async def test_all_healthy_no_action(guardian):
    guardian.prime(OK_CANARY, HEALTHY_BOT)
    await guardian.tick()
    assert guardian.actor.restarts == []
    assert guardian.alerter.alerts == []


async def test_lavalink_unreachable_restarts_and_alerts_f4(guardian):
    guardian.prime(CanaryResult(reachable=False, ok=False, error="conn refused"), HEALTHY_BOT)
    await guardian.tick()
    assert "lavalink" in guardian.actor.restarts
    assert guardian.alerter.alerts[0][0] == "F4"


async def test_potoken_failure_restarts_minter_and_alerts_f1(guardian):
    guardian.prime(
        CanaryResult(reachable=True, ok=False, error="Sign in to confirm you're not a bot"),
        HEALTHY_BOT,
    )
    await guardian.tick()
    assert guardian.actor.restarts == ["token-minter"]
    assert guardian.alerter.alerts[0][0] == "F1"


async def test_oauth_failure_alerts_f2_without_restart(guardian):
    guardian.prime(
        CanaryResult(reachable=True, ok=False, error="requires login: invalid_grant"),
        HEALTHY_BOT,
    )
    await guardian.tick()
    assert guardian.actor.restarts == []  # F2 needs a human (make reauth)
    assert guardian.alerter.alerts[0][0] == "F2"


async def test_recovery_emits_resolved(guardian):
    guardian.prime(
        CanaryResult(reachable=True, ok=False, error="requires login"), HEALTHY_BOT
    )
    await guardian.tick()
    guardian.prime(OK_CANARY, HEALTHY_BOT)
    await guardian.tick()
    assert guardian.alerter.resolutions == ["F2"]


async def test_bot_down_two_strikes_then_restart_f5(guardian):
    guardian.prime(OK_CANARY, BotHealth(ok=False))
    await guardian.tick()
    assert guardian.actor.restarts == []  # first strike: wait
    await guardian.tick()
    assert guardian.actor.restarts == ["bot"]
    assert guardian.alerter.alerts[0][0] == "F5"


async def test_frozen_position_two_strikes_then_restart_f6(guardian):
    playing = {"123": {"position": 5000, "playing": True, "connected": True}}
    guardian.prime(OK_CANARY, BotHealth(ok=True, players=dict(playing)))
    await guardian.tick()   # baseline
    await guardian.tick()   # frozen strike 1
    assert guardian.actor.restarts == []
    await guardian.tick()   # frozen strike 2 -> restart
    assert guardian.actor.restarts == ["bot"]
    assert guardian.alerter.alerts[0][0] == "F6"


async def test_advancing_position_resets_frozen_strikes(guardian):
    p1 = {"123": {"position": 5000, "playing": True, "connected": True}}
    guardian.prime(OK_CANARY, BotHealth(ok=True, players=p1))
    await guardian.tick()
    await guardian.tick()  # strike 1 (same position)
    p2 = {"123": {"position": 9000, "playing": True, "connected": True}}
    guardian.prime(OK_CANARY, BotHealth(ok=True, players=p2))
    await guardian.tick()  # advanced: strikes reset
    guardian.prime(OK_CANARY, BotHealth(ok=True, players=p2))
    await guardian.tick()  # frozen again: strike 1 only
    assert guardian.actor.restarts == []
