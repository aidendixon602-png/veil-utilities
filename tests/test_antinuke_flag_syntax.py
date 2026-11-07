"""Bleed/Greed-inspired syntax must configure the actual Veil rules without losing settings."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from discord.ext.commands.view import StringView

from cogs.antinuke import Antinuke, parse_timeframe
from core.antinukepanel import home_embed
from core.utils import UserError
from test_antinuke_guard import MOD, make_ctx as guard_ctx, run as guard_run, setup as guard_setup
from test_antinuke_undo import A, entry, feed, setup as audit_setup
from test_midtown_antinuke_and_cooldown import make_test_setup, run


def ctx_for(cog):
    return SimpleNamespace(guild=SimpleNamespace(id=44, owner_id=1), author=SimpleNamespace(id=1),
                           bot=cog.bot, clean_prefix=",", approve=AsyncMock(), neutral=AsyncMock())


async def invoke(cog, ctx, command, arguments):
    # Exercise discord.py's real argument parser, not just the callback.
    ctx.view = StringView(arguments)
    ctx.message = SimpleNamespace(attachments=[])
    await command._parse_arguments(ctx)
    await command.callback(cog, *ctx.args, **ctx.kwargs)


def test_flag_syntax_parses_and_preserves_settings_on_toggle():
    async def go():
        _, cog = await make_test_setup()
        ctx = ctx_for(cog)
        await invoke(cog, ctx, Antinuke.an_cmd_ban,
                     "on --threshold 4 --do kick --window 2m --command off")
        cfg = await cog.cfg(ctx.guild.id)
        rule = cfg["modules"]["ban"]
        assert rule == {"on": True, "threshold": 4, "punishment": "kick", "window": 120, "command": False}
        assert "antinuke is still off" in ctx.approve.await_args.args[0]
        assert "commands off" in home_embed(cfg, ",").description
        await invoke(cog, ctx, Antinuke.an_cmd_ban, "off")
        await invoke(cog, ctx, Antinuke.an_cmd_ban, "on --command on")
        assert rule == {"on": True, "threshold": 4, "punishment": "kick", "window": 120, "command": True}
        await invoke(cog, ctx, Antinuke.an_cmd_ban, "5 strip 60s")
        assert rule["threshold"] == 5 and rule["punishment"] == "strip"
    run(go)


def test_role_and_channel_shortcuts_configure_separate_rules():
    async def go():
        _, cog = await make_test_setup()
        ctx = ctx_for(cog)
        await invoke(cog, ctx, Antinuke.an_cmd_role, "on --threshold 3 --do ban")
        await invoke(cog, ctx, Antinuke.an_cmd_channel, "off --window 30s")
        cfg = await cog.cfg(ctx.guild.id)
        for key in ("rolecreate", "roledelete"):
            assert cfg["modules"][key]["on"] and cfg["modules"][key]["threshold"] == 3
        for key in ("channelcreate", "channeldelete"):
            assert not cfg["modules"][key]["on"] and cfg["modules"][key]["window"] == 30
        assert "each action has its own counter" in ctx.approve.await_args.args[0]
        await invoke(cog, ctx, Antinuke.an_cmd_unban, "on --threshold 2")
        assert cfg["modules"]["unban"]["threshold"] == 2
    run(go)


@pytest.mark.parametrize("arguments", ["on --threshold 0", "on --threshold nope", "on --do oops",
                                    "on --command maybe", "on --window watm", "on --do ban --do kick",
                                    "on --unknown x", "on --do"])
def test_bad_flags_do_not_change_rule(arguments):
    async def go():
        _, cog = await make_test_setup()
        ctx = ctx_for(cog)
        cfg = await cog.cfg(ctx.guild.id)
        before = deepcopy(cfg)
        with pytest.raises(UserError):
            await invoke(cog, ctx, Antinuke.an_cmd_ban, arguments)
        assert cfg == before
    run(go)


def test_instant_rules_do_not_accept_fake_thresholds_or_punishments():
    async def go():
        _, cog = await make_test_setup()
        ctx = ctx_for(cog)
        with pytest.raises(UserError, match="first action"):
            await invoke(cog, ctx, Antinuke.an_cmd_botadd, "on --threshold 3")
        with pytest.raises(UserError, match="always strips"):
            await invoke(cog, ctx, Antinuke.an_cmd_permissions, "on --do ban")
    run(go)


def test_command_off_only_disables_bot_guard_not_audit_detection():
    async def go():
        _, cog, guild = await guard_setup({"modules": {"ban": {"on": True, "threshold": 1, "command": False}}})
        await cog.guard(guard_ctx(MOD, guild), "ban")
        assert not cog.hits
        guild.ban.assert_not_awaited()
    guard_run(go)
    cog, guild, attacker = audit_setup()
    cog.bot.db.store["antinuke"]["modules"]["ban"]["command"] = False
    feed(cog, *(entry(guild, A.ban, SimpleNamespace(id=100 + i, name="v")) for i in range(3)))
    attacker.ban.assert_awaited_once()


def test_bad_time_units_are_user_errors():
    for value in ("badm", "bad h", "infh", "nanm"):
        with pytest.raises(UserError):
            parse_timeframe(value)


def test_custom_preset_is_a_snapshot_and_restores_command_detection_and_state():
    async def go():
        _, cog = await make_test_setup()
        ctx = ctx_for(cog)
        await invoke(cog, ctx, Antinuke.an_cmd_ban, "on --threshold 4 --command off")
        await invoke(cog, ctx, Antinuke.an_cmd_unban, "off --threshold 2")
        await Antinuke.an_preset_save.callback(cog, ctx, "mine")
        await invoke(cog, ctx, Antinuke.an_cmd_ban, "on --threshold 7 --command on")
        await invoke(cog, ctx, Antinuke.an_cmd_unban, "on --threshold 5")
        await Antinuke.an_preset_load.callback(cog, ctx, "mine")
        cfg = await cog.cfg(ctx.guild.id)
        assert cfg["modules"]["ban"]["threshold"] == 4
        assert cfg["modules"]["ban"]["command"] is False
        assert cfg["modules"]["unban"]["on"] is False
        assert cfg["modules"]["unban"]["threshold"] == 2
    run(go)


def test_toggle_explicit_state_and_legacy_module_preserve_settings():
    async def go():
        _, cog = await make_test_setup()
        ctx = ctx_for(cog)
        await Antinuke.an_toggle.callback(cog, ctx, "on")
        await invoke(cog, ctx, Antinuke.an_cmd_ban, "on --threshold 8 --do kick --command off")
        await Antinuke.an_set.callback(cog, ctx, "ban", "off")
        await Antinuke.an_module.callback(cog, ctx, "ban", "on")
        rule = (await cog.cfg(ctx.guild.id))["modules"]["ban"]
        assert rule["threshold"] == 8 and rule["punishment"] == "kick" and rule["command"] is False
        await Antinuke.an_toggle.callback(cog, ctx, "off")
        assert not (await cog.cfg(ctx.guild.id))["enabled"]
    run(go)


def test_only_owner_can_explicitly_toggle_antinuke_off():
    async def go():
        _, cog = await make_test_setup()
        ctx = ctx_for(cog)
        await Antinuke.an_toggle.callback(cog, ctx, "on")
        ctx.author = SimpleNamespace(id=2)
        cfg = await cog.cfg(ctx.guild.id)
        cfg["admins"].append(2)
        await cog.save(ctx.guild.id, cfg)
        with pytest.raises(UserError, match="server owner"):
            await Antinuke.an_toggle.callback(cog, ctx, "off")
        assert cfg["enabled"] is True
    run(go)
