"""The ,help menu: plain home card, one category dropdown, every category on one page of names, one card per command."""
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
from core.utils import Colors
from core.help import (
    CATEGORIES,
    CategorySelect,
    Help,
    HelpView,
    alias_hint,
    blurb,
    developer_commands,
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
    (select,) = view.children  # the dropdown is the only control
    return select


def _labels(view: HelpView) -> list[str]:
    return [o.label for o in _select(view).options]


def _it():
    return SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
                           user=SimpleNamespace(id=42))


ALL = {label: [] for label, _, _ in CATEGORIES}


# ------------------------------------------------------------------ the dropdown

def test_the_dropdown_only_lists_categories_the_person_may_open():
    assert set(_labels(HelpView(_help(), ALL, author_rank=0))) == {"Utility", "Music", "VoiceMaster", "Community"}
    helper = _labels(HelpView(_help(), ALL, author_rank=1))
    assert "Moderation" in helper and "Roles" not in helper and "Antinuke" not in helper
    admin = _labels(HelpView(_help(), ALL, author_rank=3))
    assert {"Moderation", "Roles", "Automod", "Server"} <= set(admin)
    assert "Antinuke" not in admin and "Permissions" not in admin
    head = _labels(HelpView(_help(), ALL, author_rank=4))
    assert {"Antinuke", "Permissions"} <= set(head)
    assert "Developer" not in head


def test_developers_get_one_developer_category_in_the_same_dropdown():
    labels = _labels(HelpView(_help(), ALL, author_rank=99, is_owner=True))
    assert labels == ["Utility", "Music", "Moderation", "Roles", "Permissions",
                      "Antinuke", "Automod", "Server", "VoiceMaster", "Community", "Developer"]
    assert "Secret" not in labels


def test_the_menu_is_a_single_dropdown_with_no_buttons():
    for start in (None, "Utility"):
        view = HelpView(_help(), {"Utility": [_cmd(f"c{i}") for i in range(40)]}, start=start)
        assert len(view.children) == 1 and isinstance(view.children[0], CategorySelect)
        assert not any(isinstance(c, discord.ui.Button) for c in view.children)


def test_options_carry_an_emoji_and_a_short_description():
    options = _select(HelpView(_help(), ALL, author_rank=99)).options
    assert all(o.emoji is not None for o in options)
    assert all(o.description and len(o.description) <= 100 for o in options)


def test_the_open_category_is_shown_as_selected():
    view = HelpView(_help(), {"Utility": [], "Music": []}, start="Music")
    assert [o.label for o in _select(view).options if o.default] == ["Music"]
    home = HelpView(_help(), {"Utility": [], "Music": []})
    assert not any(o.default for o in _select(home).options)


def test_choosing_a_category_swaps_the_page_in_one_edit():
    async def go():
        view = HelpView(_help(), {"Utility": [_cmd("ping", "Check latency")], "Music": []})
        it = _it()
        await view.show_category(it, "Utility")
        return view, it

    view, it = asyncio.run(go())
    assert view.current == "Utility"
    it.response.edit_message.assert_awaited_once()
    kwargs = it.response.edit_message.call_args.kwargs
    assert "ping" in kwargs["embed"].description and kwargs["view"] is view
    assert [o.label for o in _select(view).options if o.default] == ["Utility"]


def test_pages_are_built_when_the_menu_opens():
    view = HelpView(_help(), {"Utility": [_cmd("ping")], "Music": [_cmd("play")]})
    assert set(view.pages) == {"Utility", "Music"}
    assert "play" in view.pages["Music"].description


def test_a_person_cannot_open_a_category_above_their_rank():
    async def go():
        view = HelpView(_help(), {"Antinuke": [_cmd("antinuke")]}, author_rank=0)
        it = _it()
        await view.show_category(it, "Antinuke")
        return view, it

    view, it = asyncio.run(go())
    assert view.current is None
    it.response.send_message.assert_awaited_once()
    assert it.response.send_message.call_args.kwargs["ephemeral"] is True
    it.response.edit_message.assert_not_awaited()


def test_non_developers_cannot_open_the_developer_category():
    async def go():
        view = HelpView(_help(), {"Utility": []}, is_owner=False)
        it = _it()
        await view.show_category(it, "Developer")
        return it

    it = asyncio.run(go())
    it.response.send_message.assert_awaited_once()
    it.response.edit_message.assert_not_awaited()


def test_menu_message_is_deleted_on_timeout():
    view = HelpView(_help(), {"Utility": []})
    view.message = SimpleNamespace(delete=AsyncMock())
    asyncio.run(view.on_timeout())
    assert view.message.delete.called


# ------------------------------------------------------------------ the home card

def test_home_card_is_the_plain_prompt_with_the_prefix():
    bot = SimpleNamespace(user=SimpleNamespace(name="Veil Utilities", display_avatar=SimpleNamespace(url="https://x/a.png")),
                          is_owner=AsyncMock(return_value=False))
    h = _help()
    h.context.bot = bot
    card = h.home_embed(0, {"Utility": [_cmd("a")]})
    assert card.description == "Select a category below to view its commands.\nprefix: ,"
    assert card.author.name == "Veil Utilities" and card.author.icon_url == "https://x/a.png"
    assert card.colour.value == Colors.blurple
    assert not card.fields and card.title is None and card.footer.text is None


def test_home_card_copes_with_a_bot_that_has_no_user_yet():
    card = _help().home_embed(0, {})
    assert card.description.startswith("Select a category below") and card.author.name is None


# ------------------------------------------------------------------ category pages

def test_a_category_page_is_every_command_name_on_one_page():
    cmds = [_cmd(f"cmd{i:02}", "A long description that is not shown") for i in range(87)]
    embed = _help().category_embed("Community", cmds)
    assert embed.title == "✨ Community · 87 commands"
    assert embed.description == " · ".join(f"cmd{i:02}" for i in range(87))
    assert "description" not in embed.description
    assert embed.footer.text == ",help <command> for details"


def test_names_with_markdown_characters_are_escaped():
    assert _help().category_embed("Utility", [_cmd("snipe_all")]).description == "snipe\\_all"


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
    for label, page in view.pages.items():
        _fits(page)
        assert "…" not in page.description, label  # every command fits; nothing is cut off


def test_the_developer_category_lists_the_owner_tools(loaded_bot):
    names = [c.name for c in developer_commands(loaded_bot)]
    assert {"gitpull", "eval", "restart", "version", "blacklist"} <= set(names)
    assert names == sorted(names)
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    view = HelpView(h, {}, is_owner=True)
    assert "gitpull" in view.pages["Developer"].description
    assert view.pages["Developer"].title.startswith("⚡ Developer · ")


def test_dev_and_secret_open_the_developer_category(loaded_bot):
    async def go(word, owner):
        h = Help()
        sent = {}
        h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None,
                                    send=AsyncMock(side_effect=lambda **kw: sent.update(kw) or MagicMock()))
        h.is_owner_check = AsyncMock(return_value=owner)
        h.get_author_rank = AsyncMock(return_value=99 if owner else 0)
        await h.open_menu(word)
        return sent
    for word in ("dev", "secret", "tokens"):
        sent = asyncio.run(go(word, True))
        assert sent["embed"].title.startswith("⚡ Developer") and sent["view"].current == "Developer"
    stranger = asyncio.run(go("secret", False))
    assert stranger["view"].current is None and "Developer" not in _labels(stranger["view"])  # just the home card


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


@pytest.mark.parametrize("doc,expected", [
    ("Set an AFK status; people who mention you are told", "Set an AFK status"),
    ("Send a message from an embed script — see `embedhelp`", "Send a message from an embed script"),
    ("Turn an existing embed into a script (reply to it or give a link)", "Turn an existing embed into a script"),
    ("Change (or reset) a member's nickname", "Change a member's nickname"),
    ("Ban a member (or any user by ID). Add `--purge` to delete their messages", "Ban a member"),
    ("Seek to a time: `seek 1:30`", "Seek to a time"),
    ("Set volume (0–150)", "Set volume"),
])
def test_list_blurbs_are_the_first_clause_without_asides(doc, expected):
    assert blurb(_cmd("x", doc)) == expected
