"""The ,help menu: a plain home card with a button per category; each category is one page of commands with their
arguments and descriptions, the aliases, and a Back button."""
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
    BackButton,
    CategoryButton,
    Help,
    HelpView,
    alias_hint,
    blurb,
    description,
    developer_commands,
    example_for,
    sentence,
    short_args,
)


def _help(prefix: str = ","):
    h = Help()
    h.context = SimpleNamespace(guild=SimpleNamespace(), clean_prefix=prefix, author=SimpleNamespace(id=42),
                                bot=SimpleNamespace(user=None, is_owner=AsyncMock(return_value=False)))
    return h


def _cmd(name: str, doc: str = "Does a thing", aliases=(), sig: str = ""):
    return SimpleNamespace(name=name, qualified_name=name, short_doc=doc, aliases=list(aliases), signature=sig,
                           clean_params={})


def _buttons(view: HelpView) -> list[CategoryButton]:
    return [c for c in view.children if isinstance(c, CategoryButton)]


def _labels(view: HelpView) -> list[str]:
    return [b.label for b in _buttons(view)]


def _it():
    return SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
                           user=SimpleNamespace(id=42))


ALL = {label: [] for label, _, _ in CATEGORIES}


# ------------------------------------------------------------------ the home card and its buttons

def test_buttons_only_list_categories_the_person_may_open():
    assert set(_labels(HelpView(_help(), ALL, author_rank=0))) == {"Utility", "Music", "VoiceMaster", "Community",
                                                                     "Fun", "Images"}
    helper = _labels(HelpView(_help(), ALL, author_rank=1))
    assert "Moderation" in helper and "Roles" not in helper and "Antinuke" not in helper
    admin = _labels(HelpView(_help(), ALL, author_rank=3))
    assert {"Moderation", "Roles", "Automod", "Server"} <= set(admin)
    assert "Antinuke" not in admin and "Permissions" not in admin
    head = _labels(HelpView(_help(), ALL, author_rank=4))
    assert {"Antinuke", "Permissions"} <= set(head)
    assert "Developer" not in head


def test_buttons_are_plain_grey_text_in_rows_of_five():
    view = HelpView(_help(), ALL, author_rank=99, is_owner=True)
    assert _labels(view) == ["Utility", "Music", "Moderation", "Roles", "Permissions",
                             "Antinuke", "Automod", "Server", "VoiceMaster", "Community",
                             "Fun", "Images", "Developer"]
    assert all(b.emoji is None and b.style == discord.ButtonStyle.secondary for b in _buttons(view))
    assert [b.row for b in _buttons(view)] == [0] * 5 + [1] * 5 + [2] * 3


def test_home_card_matches_the_reference_layout():
    bot = SimpleNamespace(user=SimpleNamespace(name="Veil Utilities", display_avatar=SimpleNamespace(url="https://x/a.png")),
                          is_owner=AsyncMock(return_value=False))
    h = _help()
    h.context.bot = bot
    card = h.home_embed(0, {"Utility": [_cmd("a")]})
    assert card.description == "select a category below to view its commands."
    assert card.author.name == "Veil Utilities" and card.author.icon_url == "https://x/a.png"
    assert [f.name for f in card.fields] == ["prefix: ,"]
    assert card.colour is None and card.title is None and card.footer.text is None


def test_home_card_copes_with_a_bot_that_has_no_user_yet():
    card = _help().home_embed(0, {})
    assert card.description.startswith("select a category below") and card.author.name is None


# ------------------------------------------------------------------ opening a category and going back

def test_a_category_page_has_only_a_back_button():
    view = HelpView(_help(), {"Utility": [_cmd("ping")]}, start="Utility")
    assert [type(c) for c in view.children] == [BackButton]
    assert view.children[0].label == "← Back"


def test_clicking_a_category_then_back_swaps_the_page_in_place():
    async def go():
        view = HelpView(_help(), {"Utility": [_cmd("ping", "Check latency")], "Music": []})
        it = _it()
        await _buttons(view)[0].callback(it)
        page = it.response.edit_message.call_args.kwargs
        back = _it()
        await view.children[0].callback(back)
        return view, page, back.response.edit_message.call_args.kwargs

    view, page, home = asyncio.run(go())
    assert "`,ping` - check latency." in page["embed"].description and page["view"] is view
    assert view.current is None and home["embed"].description.startswith("select a category")
    assert _labels(view) == ["Utility", "Music"]


def test_pages_are_built_when_the_menu_opens():
    view = HelpView(_help(), {"Utility": [_cmd("ping")], "Music": [_cmd("play")]})
    assert set(view.pages) == {"Utility", "Music"}
    assert "`,play`" in view.pages["Music"].description


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


# ------------------------------------------------------------------ category pages

def test_a_category_page_lists_usage_and_what_each_command_does_then_the_aliases():
    @commands.command(aliases=["p"])
    async def play(ctx, *, query: str):
        """Play a YouTube URL, search query, or Spotify URL"""

    @commands.command(aliases=["q"])
    async def queue(ctx):
        """Show the current playback queue"""

    page = _help().category_embed("Music", [play, queue])
    assert page.title == "Music"
    assert page.description == ("`,play <query>` - play a YouTube URL, search query, or Spotify URL.\n"
                                "`,queue` - show the current playback queue.\n\n"
                                "Aliases: play → p • queue → q")
    assert [f.name for f in page.fields] == ["prefix: ,"] and page.colour is None


def test_arguments_read_like_the_reference():
    @commands.command()
    async def ban(ctx, user: discord.User, *, reason: str = None):
        """Ban"""

    @commands.command()
    async def addrole(ctx, arg1: discord.Member, arg2: discord.Role):
        """Add"""

    assert short_args(ban) == "<user> [reason]"
    assert short_args(addrole) == "<member> <role>"  # placeholder names read as what they are


@pytest.mark.parametrize("doc,expected", [
    ("Ban a member (or any user by ID). Add `--purge` to delete their messages", "ban a member (or any user by ID)."),
    ("Set a personal prefix that works everywhere (`none` to remove)", "set a personal prefix that works everywhere."),
    ("Seek to a time: `seek 1:30`", "seek to a time."),
    ("Blur an image — attach, reply to, or mention an image/user/emoji", "blur an image."),
    ("AI summary of the recent chat", "AI summary of the recent chat."),
])
def test_descriptions_are_the_first_sentence_without_examples(doc, expected):
    assert sentence(description(_cmd("x", doc))) == expected


def test_commands_without_aliases_leave_the_aliases_line_out():
    assert "Aliases" not in _help().category_embed("Utility", [_cmd("ping")]).description


def test_empty_category_says_so():
    assert "no commands" in _help().category_embed("Utility", []).description


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
    assert len(_buttons(view)) <= 25
    for label, page in view.pages.items():
        _fits(page)
        assert "…and" not in page.description, label  # every command fits; nothing is left out


def test_the_developer_category_lists_the_owner_tools(loaded_bot):
    names = [c.name for c in developer_commands(loaded_bot)]
    assert {"gitpull", "eval", "restart", "version", "blacklist"} <= set(names)
    assert names == sorted(names)
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    view = HelpView(h, {}, is_owner=True)
    assert "gitpull" in view.pages["Developer"].description
    assert view.pages["Developer"].title == "Developer"


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
        assert sent["embed"].title == "Developer" and sent["view"].current == "Developer"
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
