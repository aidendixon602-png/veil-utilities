"""The ,help menu: slim home card, category dropdown, numbered paged lists and one card per command."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Literal, Optional
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands, tasks

from core.bot import EXTENSIONS, Veil
from core.help import (
    CATEGORIES,
    PAGE_SIZE,
    CategorySelect,
    CloseButton,
    Help,
    HelpView,
    HomeButton,
    NextButton,
    PageIndicator,
    PrevButton,
    alias_hint,
    blurb,
    example_for,
)


def _help(prefix: str = ","):
    h = Help()
    h.context = SimpleNamespace(guild=SimpleNamespace(), clean_prefix=prefix, author=SimpleNamespace(id=42),
                                bot=SimpleNamespace(user=None, is_owner=AsyncMock(return_value=False)))
    return h


def _cmd(name: str, doc: str = "Does a thing", aliases=(), sig: str = ""):
    return SimpleNamespace(name=name, qualified_name=name, short_doc=doc, aliases=list(aliases), signature=sig)


def _select(view: HelpView) -> CategorySelect:
    return next(c for c in view.children if isinstance(c, CategorySelect))


def _labels(view: HelpView) -> list[str]:
    return [o.label for o in _select(view).options]


ALL = {label: [] for label, _, _ in CATEGORIES}


# ------------------------------------------------------------------ the dropdown

def test_dropdown_only_lists_categories_the_person_may_open():
    assert set(_labels(HelpView(_help(), ALL, author_rank=0))) == {"Utility", "Music", "VoiceMaster", "Community"}
    helper = _labels(HelpView(_help(), ALL, author_rank=1))
    assert "Moderation" in helper and "Roles" not in helper and "Antinuke" not in helper
    admin = _labels(HelpView(_help(), ALL, author_rank=3))
    assert {"Moderation", "Roles", "Automod", "Server"} <= set(admin)
    assert "Antinuke" not in admin and "Permissions" not in admin
    head = _labels(HelpView(_help(), ALL, author_rank=4))
    assert {"Antinuke", "Permissions"} <= set(head)
    assert "Developer" not in head and "Secret" not in head


def test_developers_get_the_extra_developer_entries():
    labels = _labels(HelpView(_help(), ALL, is_owner=True))
    assert labels[-2:] == ["Developer", "Secret"]
    assert "Antinuke" in labels


def test_options_describe_count_and_purpose_within_discords_limits():
    cats = {"Utility": [_cmd("a"), _cmd("b")], "Music": [_cmd("c")]}
    view = HelpView(_help(), cats)
    utility = next(o for o in _select(view).options if o.label == "Utility")
    assert utility.description.startswith("2 commands · ")
    assert all(len(o.description) <= 100 for o in _select(view).options)
    assert len(_select(view).options) <= 25


def test_current_category_is_marked_selected():
    view = HelpView(_help(), {"Utility": [], "Music": []}, start="Music")
    assert [o.label for o in _select(view).options if o.default] == ["Music"]
    assert not any(o.default for o in _select(HelpView(_help(), {"Utility": []})).options)


# ------------------------------------------------------------------ buttons and paging

def test_home_has_only_the_close_button_and_no_pager():
    view = HelpView(_help(), {"Utility": []})
    assert [type(c) for c in view.children if not isinstance(c, CategorySelect)] == [CloseButton]


def test_category_view_adds_home_and_pages_only_when_the_list_is_long():
    short = HelpView(_help(), {"Utility": [_cmd("a")]}, start="Utility")
    kinds = [type(c) for c in short.children]
    assert HomeButton in kinds and PrevButton not in kinds and PageIndicator not in kinds

    many = [_cmd(f"c{i:02}") for i in range(PAGE_SIZE * 2 + 1)]
    view = HelpView(_help(), {"Utility": many}, start="Utility")
    assert [c.label for c in view.children if isinstance(c, PageIndicator)] == ["1/3"]
    prev = next(c for c in view.children if isinstance(c, PrevButton))
    nxt = next(c for c in view.children if isinstance(c, NextButton))
    assert prev.disabled and not nxt.disabled


def test_paging_moves_between_pages_and_clamps_at_the_ends():
    async def go():
        many = [_cmd(f"c{i:02}") for i in range(PAGE_SIZE + 3)]
        h = _help()
        view = HelpView(h, {"Utility": many}, start="Utility")
        it = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()), user=SimpleNamespace(id=42))
        await view.change_page(it, 1)
        assert view.page == 1
        embed = it.response.edit_message.call_args.kwargs["embed"]
        assert embed.footer.text.startswith("Page 2/2")
        assert f"`{PAGE_SIZE + 1}` **c{PAGE_SIZE:02}**" in embed.description  # numbering continues across pages
        await view.change_page(it, 1)
        assert view.page == 1
        await view.change_page(it, -5)
        assert view.page == 0
    asyncio.run(go())


def test_home_button_returns_to_the_overview():
    async def go():
        view = HelpView(_help(), {"Utility": [_cmd("a")]}, start="Utility")
        home = next(c for c in view.children if isinstance(c, HomeButton))
        it = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()), user=SimpleNamespace(id=42))
        await home.callback(it)
        return view, it

    view, it = asyncio.run(go())
    assert view.current is None
    assert it.response.edit_message.called


def test_choosing_a_category_swaps_the_embed_in_place():
    async def go():
        view = HelpView(_help(), {"Utility": [_cmd("ping", "Check latency")], "Music": []})
        it = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()), user=SimpleNamespace(id=42))
        await view.show_category(it, "Utility")
        return view, it

    view, it = asyncio.run(go())
    assert view.current == "Utility"
    embed = it.response.edit_message.call_args.kwargs["embed"]
    assert "ping" in embed.description and "Check latency" in embed.description


def test_a_person_cannot_open_a_category_above_their_rank():
    async def go():
        view = HelpView(_help(), {"Antinuke": [_cmd("antinuke")]}, author_rank=0)
        it = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
                             user=SimpleNamespace(id=42))
        await view.show_category(it, "Antinuke")
        return view, it

    view, it = asyncio.run(go())
    assert view.current is None
    it.response.send_message.assert_awaited_once()
    assert it.response.send_message.call_args.kwargs["ephemeral"] is True


def test_non_developers_cannot_open_the_developer_menus():
    async def go():
        view = HelpView(_help(), {"Utility": []}, is_owner=False)
        it = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
                             user=SimpleNamespace(id=42))
        await view.show_category(it, "Secret")
        return it

    it = asyncio.run(go())
    it.response.send_message.assert_awaited_once()
    it.response.edit_message.assert_not_called()


def test_close_button_deletes_the_menu():
    async def go():
        view = HelpView(_help(), {"Utility": []})
        close = next(c for c in view.children if isinstance(c, CloseButton))
        it = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()), delete_original_response=AsyncMock())
        await close.callback(it)
        return view, it

    view, it = asyncio.run(go())
    it.delete_original_response.assert_awaited_once()
    assert view.is_finished()


def test_menu_message_is_deleted_on_timeout():
    view = HelpView(_help(), {"Utility": []})
    view.message = SimpleNamespace(delete=AsyncMock())
    asyncio.run(view.on_timeout())
    assert view.message.delete.called


# ------------------------------------------------------------------ the home card

def test_home_card_is_slim_and_counts_only_what_the_person_can_see():
    h = _help()
    cats = {"Utility": [_cmd("a"), _cmd("b")], "Moderation": [_cmd("ban")], "Antinuke": [_cmd("an")]}
    public = h.home_embed(0, cats)
    assert "**Prefix** `,` · 1 category · 2 commands" in public.description
    assert "`,`" in public.description and ",help <command>" in public.description
    assert not public.fields and public.title is None
    staff = h.home_embed(1, cats)
    assert "2 categories · 3 commands" in staff.description
    dev = h.home_embed(0, cats, is_owner=True)
    assert "3 categories · 4 commands" in dev.description


# ------------------------------------------------------------------ category lists

def test_list_lines_are_numbered_with_short_aliases_and_blurbs():
    h = _help()
    cmds = [_cmd("userinfo", "show profile info.", ["ui", "info"]),
            _cmd("role", "Toggle a role", ["r", "cr", "changerole", "x"])]
    embed = h.category_embed("Utility", cmds)
    lines = embed.description.splitlines()
    assert "`" in embed.title or "Utility" in embed.title
    assert any(line == "` 1` **userinfo** *(ui, info)* — Show profile info" for line in lines)
    assert any(line == "` 2` **role** *(r, cr +2)* — Toggle a role" for line in lines)
    assert embed.footer.text == "Page 1/1 · 2 commands · ,help <command> for details"


def test_groups_show_how_many_subcommands_they_hold():
    @commands.group()
    async def role(ctx):
        """Manage roles"""

    @role.command()
    async def add(ctx):
        """Add"""

    @role.command()
    async def remove(ctx):
        """Remove"""

    line = _help().category_embed("Roles", [role]).description.splitlines()[-1]
    assert line.endswith("— Manage roles · 2 subcommands")


def test_empty_category_says_so():
    assert "No commands" in _help().category_embed("Utility", []).description


def test_blurb_and_alias_hint_edge_cases():
    assert blurb(_cmd("x", "")) == "No description yet"
    assert blurb(_cmd("x", "first line\nsecond line")) == "First line"
    assert len(blurb(_cmd("x", "word " * 40))) <= 70
    assert alias_hint(_cmd("x")) == ""


# ------------------------------------------------------------------ examples

def test_examples_are_built_from_required_arguments():
    @commands.command()
    async def ban(ctx, member: discord.Member, *, reason: str = "none"):
        pass

    @commands.command()
    async def give(ctx, member: discord.Member, role: discord.Role):
        pass

    @commands.command()
    async def mode(ctx, setting: Literal["on", "off"], where: Optional[discord.TextChannel] = None):
        pass

    @commands.command()
    async def remind(ctx, duration: str, *, text: str):
        pass

    @commands.command()
    async def ping(ctx):
        pass

    assert example_for(ban, ",") == ",ban @user"
    assert example_for(give, ",") == ",give @user @role"
    assert example_for(mode, ",") == ",mode on"
    assert example_for(remind, ",") == ",remind 10m hello"
    assert example_for(ping, ",") == ""


def test_a_commands_own_example_wins():
    @commands.command(extras={"example": "@user 7d being rude"})
    async def timeout(ctx, member: discord.Member, duration: str):
        pass

    assert example_for(timeout, ",") == ",timeout @user 7d being rude"


# ------------------------------------------------------------------ command cards

def test_command_card_has_usage_aliases_permissions_and_module():
    from cogs.moderation import Moderation

    bot = Veil()
    cog = Moderation(bot)
    asyncio.run(bot.add_cog(cog))
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=bot, author=SimpleNamespace(id=1), guild=None)
    card = h.command_embed(cog.ban)
    fields = {f.name: f.value for f in card.fields}
    assert card.title == "ban"
    assert fields["Usage"].startswith("```\nSyntax: ,ban ")
    assert "Example: ,ban @user" in fields["Usage"]
    assert "Admin+" in fields["Permissions"]
    assert card.footer.text.startswith("Module: Moderation")


def test_public_commands_say_everyone_can_use_them():
    @commands.command(aliases=["pong"])
    async def ping(ctx):
        """Check latency"""

    ping.cog = None
    card = _help().command_embed(ping)
    fields = {f.name: f.value for f in card.fields}
    assert fields["Permissions"] == "Everyone"
    assert fields["Aliases"] == "`pong`"
    assert fields["Usage"] == "```\nSyntax: ,ping\n```"
    assert card.description == "Check latency"


# ------------------------------------------------------------------ typos

def test_a_typo_suggests_real_commands_the_person_can_use():
    from cogs.moderation import Moderation

    async def go():
        bot = Veil()
        await bot.add_cog(Moderation(bot))
        bot.db = SimpleNamespace(get_config=AsyncMock(return_value={"users": {"7": "admin"}}))
        h = Help()
        h.context = SimpleNamespace(clean_prefix=",", bot=bot, guild=SimpleNamespace(id=1, owner_id=0),
                                    author=SimpleNamespace(id=7, roles=[], guild_permissions=discord.Permissions.none()))
        return await h.command_not_found("bna")

    text = asyncio.run(go())
    assert text.startswith('No command called "bna" found.')
    assert "`ban`" in text


def test_a_typo_never_leaks_staff_commands_to_strangers():
    from cogs.moderation import Moderation

    async def go():
        bot = Veil()
        await bot.add_cog(Moderation(bot))
        bot.db = SimpleNamespace(get_config=AsyncMock(return_value={}))
        h = Help()
        h.context = SimpleNamespace(clean_prefix=",", bot=bot, guild=SimpleNamespace(id=1, owner_id=0),
                                    author=SimpleNamespace(id=7, roles=[], guild_permissions=discord.Permissions.none()))
        return await h.command_not_found("bna")

    assert asyncio.run(go()) == 'No command called "bna" found.'


# ------------------------------------------------------------------ every real command renders

@pytest.fixture(scope="module")
def loaded_bot():
    """The real bot with every cog loaded (no login). One loop for setup and teardown so the db thread can close."""
    logging.disable(logging.CRITICAL)
    loop = asyncio.new_event_loop()

    async def build():
        bot = Veil()
        await bot.db.connect()
        for ext in EXTENSIONS:
            await bot.load_extension(ext)
        for cog in bot.cogs.values():  # background loops can't run without a login; stop them
            for attr in list(vars(cog).values()):
                if isinstance(attr, tasks.Loop):
                    attr.cancel()
        return bot

    bot = loop.run_until_complete(build())
    yield bot
    loop.run_until_complete(bot.db.close())
    loop.close()
    logging.disable(logging.NOTSET)


def _fits(embed: discord.Embed):
    assert len(embed) <= 6000, embed.title
    assert len(embed.description or "") <= 4096
    for f in embed.fields:
        assert len(f.name) <= 256 and len(f.value) <= 1024, (embed.title, f.name)
    if embed.footer.text:
        assert len(embed.footer.text) <= 2048


def test_every_category_page_fits_discords_limits(loaded_bot):
    from core.help import category_commands
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    categories = category_commands(loaded_bot)
    view = HelpView(h, categories, is_owner=True)
    _fits(h.home_embed(99, categories, True))
    assert len(_select(view).options) <= 25
    for label, cmds in categories.items():
        view.current = label
        for page in range(view.pages()):
            _fits(h.category_embed(label, cmds, page))


def test_every_command_card_fits_discords_limits(loaded_bot):
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    cards = 0
    for cmd in loaded_bot.walk_commands():
        _fits(h.command_embed(cmd))
        cards += 1
    assert cards > 100


def test_every_group_card_lists_its_subcommands_within_limits(loaded_bot):
    h = Help()
    sent = []
    ctx = MagicMock()
    ctx.clean_prefix, ctx.bot, ctx.author, ctx.guild = ",", loaded_bot, SimpleNamespace(id=1), None
    ctx.send = AsyncMock(side_effect=lambda **kw: sent.append(kw["embed"]))
    h.context = ctx
    h.is_owner_check = AsyncMock(return_value=True)
    groups = [c for c in loaded_bot.walk_commands() if isinstance(c, commands.Group)]

    async def go():
        for g in groups:
            await h.send_group_help(g)

    asyncio.run(go())
    assert len(sent) == len(groups) > 10
    for embed in sent:
        _fits(embed)


# ------------------------------------------------------------------ details found while reading real cards

def test_subcommands_are_filed_under_their_group(loaded_bot):
    from core.help import category_for_command
    assert category_for_command(loaded_bot.get_command("role"), loaded_bot) == "Roles"
    assert category_for_command(loaded_bot.get_command("role add"), loaded_bot) == "Roles"


def test_rank_is_not_repeated_as_a_permission(loaded_bot):
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    perms = next(f.value for f in h.command_embed(loaded_bot.get_command("ban")).fields if f.name == "Permissions")
    assert perms.startswith("Admin+") and "Uperm" not in perms


def test_placeholder_argument_names_never_become_fake_examples(loaded_bot):
    assert example_for(loaded_bot.get_command("role add"), ",") == ",role add @user @role"
    for cmd in loaded_bot.walk_commands():
        assert "arg1" not in example_for(cmd, ","), cmd.qualified_name


def test_group_card_has_no_trailing_blank_line(loaded_bot):
    h = Help()
    sent = []
    h.context = MagicMock(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    h.context.send = AsyncMock(side_effect=lambda **kw: sent.append(kw["embed"]))
    h.is_owner_check = AsyncMock(return_value=True)
    asyncio.run(h.send_group_help(loaded_bot.get_command("role")))
    subs = next(f.value for f in sent[0].fields if f.name == "Subcommands")
    assert subs == subs.rstrip() and "**add**" in subs and "**remove**" in subs
