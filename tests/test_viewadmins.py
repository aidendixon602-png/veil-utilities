"""The permission overview reports role bits, not inferred member access."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from cogs.moderation import Moderation, VIEW_ADMIN_PERMISSIONS, viewadmins_pages


def role(rid, position, **permissions):
    return SimpleNamespace(id=rid, position=position, mention=f"<@&{rid}>",
                           permissions=discord.Permissions(**permissions))


def test_groups_explicit_permissions_in_role_order_without_inventing_admin_bits():
    roles = [role(1, 0, manage_roles=True), role(2, 10, administrator=True),
             role(3, 5, manage_roles=True, moderate_members=True)]
    pages = viewadmins_pages(roles)
    assert len(pages) == 1
    text = pages[0].description
    assert text.split("**Manage Server**")[0].endswith("<@&2>\n\n")
    assert "**Manage Server**\nNone" in text
    assert "**Manage Roles**\n<@&3>\n<@&1>" in text
    assert "**Timeout Members**\n<@&3>" in text
    assert "Administrator grants all permissions" in pages[0].footer.text
    assert all(f"**{label}**" in text for label, _ in VIEW_ADMIN_PERMISSIONS)


def test_no_matching_roles_and_default_role_are_included():
    empty = viewadmins_pages([role(1, 0)])[0].description
    assert empty.count("\nNone") == len(VIEW_ADMIN_PERMISSIONS)
    everyone = role(1, 0, mention_everyone=True)
    everyone.mention = "@everyone"
    assert "**Mention Everyone**\n@everyone" in viewadmins_pages([everyone])[0].description


def test_many_roles_fit_page_limits_without_losing_any():
    flags = {name: True for _, name in VIEW_ADMIN_PERMISSIONS}
    roles = [role(10**18 + i, i, **flags) for i in range(250)]
    pages = viewadmins_pages(roles)
    assert len(pages) > 1
    assert all(len(page.description) <= 3800 and len(page) <= 6000 for page in pages)
    text = "\n".join(page.description for page in pages)
    for item in roles:
        assert text.count(item.mention) == len(VIEW_ADMIN_PERMISSIONS)
    assert all(f"**{label}**" in text for label, _ in VIEW_ADMIN_PERMISSIONS)


def test_command_is_guild_only_restricted_and_paginates():
    async def run():
        ctx = SimpleNamespace(guild=SimpleNamespace(roles=[role(42, 1, administrator=True)]), paginate=AsyncMock())
        command = Moderation.viewadmins
        await command.callback(Moderation(SimpleNamespace()), ctx)
        pages = ctx.paginate.await_args.args[0]
        assert "**Administrator**\n<@&42>" in pages[0].description
        assert "manage_guild" in command.callback.__veil_perms__
        assert len(command.checks) >= 2

    asyncio.run(run())
