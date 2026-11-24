"""Protected people: full command access, no operator surfaces, and one refusal for every moderation attempt."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from core import perms
from core.utils import UserError, antinuke_admin, check_hierarchy_async, check_target

WEBA, SIMI, THIRD = 1277709272041394187, 843909457616044092, 1062243262909390988
PROTECTED = (WEBA, SIMI, THIRD)
STRANGER = 5
OWNER = 10
OPERATOR = 999


def test_all_three_are_protected_and_hold_the_developer_rank():
    for uid in PROTECTED:
        assert perms.is_whitelisted_person(uid)
        assert perms.is_bot_developer(uid), f"{uid} should pass ordinary command checks"
        # ...but never the strict operator check that guards secrets, logs and the dashboard
        assert not perms.is_app_operator(uid)
    assert perms.PROTECTED_RANKS == frozenset({"developer"})
    assert set(PROTECTED) <= set(perms.BAN_BUTTON_USERS)


def test_a_stranger_and_the_server_owner_are_unchanged():
    assert not perms.is_whitelisted_person(STRANGER)
    assert not perms.is_bot_developer(STRANGER)
    assert not perms.is_whitelisted_person(OWNER)


@pytest.mark.parametrize("uid", PROTECTED)
def test_every_moderation_target_check_refuses_with_one_message(uid):
    """check_target/check_hierarchy are used by kick, ban, timeout, warn, strip, jail and quarantine."""
    async def go():
        guild = SimpleNamespace(id=1, owner_id=OWNER, me=SimpleNamespace(id=4, top_role=SimpleNamespace(position=1)))
        for actor in (OWNER, OPERATOR, STRANGER):  # the owner and developers are refused too
            ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=actor),
                                  bot=SimpleNamespace(user=SimpleNamespace(id=4)), command=None)
            with pytest.raises(UserError) as caught:
                check_target(ctx, discord.Object(uid), "ban")
            assert str(caught.value) == perms.PROTECTED_MODERATION_MESSAGE
            member = SimpleNamespace(id=uid, mention=f"<@{uid}>", top_role=SimpleNamespace(position=0))
            hierarchy_ctx = SimpleNamespace(guild=guild, command=None, bot=SimpleNamespace(
                user=SimpleNamespace(id=4),
                db=SimpleNamespace(get_config=AsyncMock(return_value={}))))
            hierarchy_ctx.author = SimpleNamespace(id=actor, guild_permissions=discord.Permissions.none())
            with pytest.raises(UserError) as caught:
                await check_hierarchy_async(hierarchy_ctx, member, "kick")
            assert str(caught.value) == perms.PROTECTED_MODERATION_MESSAGE
    asyncio.run(go())


def test_the_message_is_shared_and_unchanged():
    assert perms.PROTECTED_MODERATION_MESSAGE == "You can't moderate this user or these users"


@pytest.mark.parametrize("uid", PROTECTED)
def test_a_protected_person_passes_staff_only_disabled_and_ignored_channel_checks(uid):
    """Real bot, real commands: staff-only mode, a disabled command and an ignored channel must not stop them."""
    from core.bot import Veil

    async def go():
        bot = Veil()
        bot.owner_ids = frozenset({OPERATOR})
        bot.auth_required = False
        store = {"settings": {"staff_only": True, "disabled": ["purge"], "ignored_channels": [500]}}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda gid, m, d=None: store.get(m, {})),
                                 execute=AsyncMock(), fetchone=AsyncMock(return_value={"c": 1}))
        await bot.add_cog(__import__("cogs.moderation", fromlist=["Moderation"]).Moderation(bot))
        guild = SimpleNamespace(id=1, owner_id=OWNER, me=SimpleNamespace(id=4, top_role=SimpleNamespace(position=99)))
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=uid, roles=[],
                                                                 guild_permissions=discord.Permissions.none()),
                              bot=bot, channel=SimpleNamespace(id=500), command=bot.get_command("purge"),
                              message=SimpleNamespace(), prefix=",", clean_prefix=",",
                              bot_permissions=discord.Permissions.all(), permissions=discord.Permissions.all())
        assert await bot._global_check(ctx)  # staff-only + ignored channel + disabled command
    asyncio.run(go())


@pytest.mark.parametrize("uid", PROTECTED)
def test_operator_surfaces_stay_closed(uid):
    """Hidden commands, the Owner/DevTools cogs and antinuke configuration never open to protected people."""
    from core.bot import Veil

    async def go():
        bot = Veil()
        bot.owner_ids = frozenset({OPERATOR})
        bot.db = SimpleNamespace(get_config=AsyncMock(return_value={}))
        await bot.add_cog(__import__("cogs.owner", fromlist=["Owner"]).Owner(bot))
        await bot.add_cog(__import__("cogs.devtools", fromlist=["DevTools"]).DevTools(bot))
        guild = SimpleNamespace(id=1, owner_id=OWNER, me=SimpleNamespace(id=4, top_role=SimpleNamespace(position=99)))
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=uid, roles=[],
                                                                 guild_permissions=discord.Permissions.all()),
                              bot=bot, channel=SimpleNamespace(id=5), command=bot.get_command("sys"),
                              message=SimpleNamespace(), prefix=",", clean_prefix=",",
                              bot_permissions=discord.Permissions.all(), permissions=discord.Permissions.all())
        with pytest.raises(commands.CommandNotFound):
            await bot._global_check(ctx)
        for cog_name in ("Owner", "DevTools"):
            with pytest.raises(commands.NotOwner):
                await bot.get_cog(cog_name).cog_check(ctx)
        ctx.command = bot.get_command("ban")
        with pytest.raises(UserError, match="server owner"):
            await antinuke_admin(ctx)
    asyncio.run(go())


def test_a_real_developer_keeps_every_operator_surface():
    from core.bot import Veil

    async def go():
        bot = Veil()
        bot.owner_ids = frozenset({OPERATOR})
        bot.db = SimpleNamespace(get_config=AsyncMock(return_value={}))
        await bot.add_cog(__import__("cogs.owner", fromlist=["Owner"]).Owner(bot))
        guild = SimpleNamespace(id=1, owner_id=OWNER, me=SimpleNamespace(id=4, top_role=SimpleNamespace(position=99)))
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=OPERATOR, roles=[],
                                                                 guild_permissions=discord.Permissions.all()),
                              bot=bot, channel=SimpleNamespace(id=5), command=bot.get_command("sys"),
                              message=SimpleNamespace(), prefix=",", clean_prefix=",",
                              bot_permissions=discord.Permissions.all(), permissions=discord.Permissions.all())
        assert await bot._global_check(ctx)
        assert await bot.get_cog("Owner").cog_check(ctx)
        assert await antinuke_admin(ctx)
    asyncio.run(go())


@pytest.mark.parametrize("uid", PROTECTED)
def test_help_shows_commands_but_not_the_antinuke_category(uid):
    """They get the full command list, but `,help` never offers a category they cannot configure."""
    from cogs.antinuke import Antinuke
    from core.help import Help, category_labels, visible_command

    async def go():
        bot = commands.Bot(command_prefix=",", intents=discord.Intents.default())
        bot.owner_ids = {OPERATOR}
        store = {"antinuke": {"admins": []}, "permlevels": {}, "fakeperms": {}, "settings": {},
                 "command_restrictions": {}}
        bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda g, m, d=None: store.get(m, {})))
        await bot.add_cog(Antinuke(bot))
        h = Help()
        h.context = SimpleNamespace(bot=bot, guild=SimpleNamespace(id=1, owner_id=OWNER),
                                    author=SimpleNamespace(id=uid, roles=[]), channel=None, clean_prefix=",")
        access = await h.help_access(0)
        rank = await h.get_author_rank()
        antinuke = bot.get_command("an")
        assert not visible_command(antinuke, bot, False, rank, access)
        assert "Antinuke" not in category_labels({"Antinuke": [antinuke]}, False, rank, access)
        assert not await h.is_owner_check()  # no Developer/Secret groups
        await bot.close()
    asyncio.run(go())
