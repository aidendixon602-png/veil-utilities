"""Antinuke / antiraid / antialt replies: short lower-case lines that say what changed."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.antinuke import Antinuke
from test_midtown_antinuke_and_cooldown import make_test_setup, run


def make_ctx(guild_id=999):
    guild = SimpleNamespace(id=guild_id, owner_id=1, me=SimpleNamespace(top_role=10))
    author = SimpleNamespace(id=1, guild_permissions=SimpleNamespace(administrator=True))
    return SimpleNamespace(guild=guild, author=author, clean_prefix="!", approve=AsyncMock(), neutral=AsyncMock(),
                           warn=AsyncMock())


def said(ctx, method="approve"):
    return getattr(ctx, method).await_args.args[0]


def user(uid=7):
    return SimpleNamespace(id=uid, mention=f"<@{uid}>")


def test_on_off_and_rule_lines():
    async def go():
        _, cog = await make_test_setup()
        ctx = make_ctx()
        await Antinuke.an_enable.callback(cog, ctx)
        assert said(ctx) == "turned antinuke **on**"
        await Antinuke.an_disable.callback(cog, ctx)
        assert said(ctx) == "turned antinuke **off**"
        await Antinuke.an_toggle.callback(cog, ctx)
        assert said(ctx) == "turned antinuke **on**"
        await cog._update_rule(ctx, "ban", 5, "kick", "30s")
        assert said(ctx) == "set the **Ban** rule to **5 in 30s** → kick"
        await cog._show_rule(ctx, "ban")
        assert said(ctx, "neutral").startswith("**Ban** rule: **5 in 30s** → kick")
    run(go)


def test_module_toggle_line():
    async def go():
        _, cog = await make_test_setup()
        ctx = make_ctx()
        await Antinuke.an_set.callback(cog, ctx, "ban", "off")
        assert said(ctx) == "turned `ban` **off** (threshold **3**)"
    run(go)


def test_whitelist_lines_use_mentions_and_tell_apart_repeats():
    async def go():
        _, cog = await make_test_setup()
        ctx = make_ctx()
        await Antinuke.an_whitelist.callback(cog, ctx, user())
        assert said(ctx) == "whitelisted <@7>"
        await Antinuke.an_whitelist.callback(cog, ctx, user())
        assert said(ctx, "warn") == "<@7> is already whitelisted"
        await Antinuke.an_unwhitelist.callback(cog, ctx, user())
        assert said(ctx) == "removed <@7> from the whitelist"
        await Antinuke.an_unwhitelist.callback(cog, ctx, user())
        assert said(ctx, "warn") == "<@7> isn't whitelisted"
    run(go)


def test_reset_and_preset_lines():
    async def go():
        _, cog = await make_test_setup()
        ctx = make_ctx()
        await Antinuke.an_reset.callback(cog, ctx, "ban")
        assert said(ctx) == "reset the **Ban** rule to its default"
        await Antinuke.an_reset.callback(cog, ctx)
        assert said(ctx) == "reset every antinuke rule to its default"
        await Antinuke.an_preset_save.callback(cog, ctx, "Raid")
        assert said(ctx) == "saved the current rules as preset **raid**"
        await Antinuke.an_preset_load.callback(cog, ctx, "strict")
        assert said(ctx) == "loaded preset **strict**"
    run(go)


def test_preset_help_uses_the_server_prefix():
    async def go():
        _, cog = await make_test_setup()
        ctx = make_ctx()
        await Antinuke.an_preset.callback(cog, ctx)
        text = said(ctx, "neutral")
        assert "`!an preset save <name>`" in text and ",an" not in text
    run(go)


def test_defaults_admin_and_log_lines():
    async def go():
        _, cog = await make_test_setup()
        ctx = make_ctx()
        await Antinuke.an_punishment.callback(cog, ctx, "KICK")
        assert said(ctx) == "set the default punishment to **kick**"
        await Antinuke.an_window.callback(cog, ctx, 90)
        assert said(ctx) == "set the default window to **90s**"
        await Antinuke.an_admin.callback(cog, ctx, user())
        assert said(ctx) == "made <@7> an antinuke admin"
        await Antinuke.an_admin.callback(cog, ctx, user())
        assert said(ctx) == "removed <@7> as an antinuke admin"
        channel = SimpleNamespace(id=5, mention="<#5>")
        await Antinuke.an_log.callback(cog, ctx, channel)
        assert said(ctx) == "antinuke alerts will go to <#5>"
        await Antinuke.an_log.callback(cog, ctx, None)
        assert said(ctx) == "cleared the antinuke alert channel"
    run(go)


def test_usage_hints_follow_the_server_prefix():
    async def go():
        import pytest

        from core.utils import UserError
        _, cog = await make_test_setup()
        ctx = make_ctx()
        with pytest.raises(UserError) as caught:
            await Antinuke.an_set.callback(cog, ctx, "ban", "3")
        assert "`!an set" in str(caught.value) and "`,an" not in str(caught.value)
        with pytest.raises(UserError) as caught:
            await Antinuke.an_cmd_ban.callback(cog, ctx, 3, None, None)
        assert "`!an ban" in str(caught.value)
    run(go)


def test_antiraid_and_antialt_lines():
    async def go():
        _, cog = await make_test_setup()
        ctx = make_ctx()
        await Antinuke.ar_massjoin.callback(cog, ctx, 10, 10)
        assert said(ctx) == "mass join filter set to **10 joins in 10s**"
        await Antinuke.ar_massjoin.callback(cog, ctx, 0)
        assert said(ctx) == "turned off the mass join filter"
        await Antinuke.ar_age.callback(cog, ctx, 3)
        assert said(ctx) == "account age filter set to **under 3 days**"
        await Antinuke.ar_avatar.callback(cog, ctx, "on")
        assert said(ctx) == "turned the avatar filter **on**"
        await Antinuke.ar_punishment.callback(cog, ctx, "ban")
        assert said(ctx) == "antiraid punishment set to **ban**"
        await Antinuke.aa_minage.callback(cog, ctx, 7)
        assert said(ctx) == "anti-alt minimum account age set to **7 days**"
        await Antinuke.aa_avatar.callback(cog, ctx, "off")
        assert said(ctx) == "turned the anti-alt avatar requirement **off**"
    run(go)


def test_whitelist_toggles_say_which_way_they_went():
    async def go():
        _, cog = await make_test_setup()
        ctx = make_ctx()
        await Antinuke.ar_whitelist.callback(cog, ctx, user())
        assert said(ctx) == "added <@7> to the antiraid whitelist"
        await Antinuke.ar_whitelist.callback(cog, ctx, user())
        assert said(ctx) == "removed <@7> from the antiraid whitelist"
        await Antinuke.aa_whitelist.callback(cog, ctx, user())
        assert said(ctx) == "added <@7> to the anti-alt whitelist"
        await Antinuke.aa_whitelist.callback(cog, ctx, user())
        assert said(ctx) == "removed <@7> from the anti-alt whitelist"
    run(go)
