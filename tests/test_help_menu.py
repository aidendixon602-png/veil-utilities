"""The ,help menu, greed / bleed style: a home card and one card per category, picked from a dropdown; each category
lists its commands in one code block; command cards have Aliases / Parameters / Information and a Usage block."""
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
    HOME,
    CategorySelect,
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


def _select(view: HelpView) -> CategorySelect:
    return next(c for c in view.children if isinstance(c, CategorySelect))


def _labels(view: HelpView) -> list[str]:
    return [o.label for o in _select(view).options if o.value != HOME]


def _pick(view: HelpView, value: str):
    it = _it()
    select = _select(view)
    select._values = [value]  # what Discord fills in when someone picks an option
    asyncio.run(select.callback(it))
    return it


def _it():
    return SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
                           user=SimpleNamespace(id=42))


ALL = {label: [] for label, _, _ in CATEGORIES}


# ------------------------------------------------------------------ the dropdown

def test_the_dropdown_only_lists_categories_the_person_may_open():
    public = {"Utility", "Music", "VoiceMaster", "Community", "Fun", "Images"}
    assert set(_labels(HelpView(_help(), ALL, author_rank=0))) == public
    # each level up opens one more staff category
    expected = {1: {"Moderator"}, 2: {"Moderator"}, 3: {"Moderator", "Admin", "Roles"},
                4: {"Moderator", "Admin", "Roles", "Server", "Automod"},
                6: {"Moderator", "Admin", "Roles", "Server", "Automod", "Permissions"},
                7: {"Moderator", "Admin", "Roles", "Server", "Automod", "Permissions", "Antinuke"}}
    for rank, staff in expected.items():
        assert set(_labels(HelpView(_help(), ALL, author_rank=rank))) == public | staff, rank
    assert "Developer" not in _labels(HelpView(_help(), ALL, author_rank=7))


def test_inside_a_category_commands_above_your_level_are_hidden():
    from core.help import min_rank_for_command
    kick = SimpleNamespace(callback=SimpleNamespace(__veil_perms__=["kick_members", "uperm: moderator"]),
                           cog_name="Moderation", qualified_name="kick", name="kick")
    purge = SimpleNamespace(callback=SimpleNamespace(__veil_perms__=["manage_messages"]), cog_name="Moderation",
                            qualified_name="purge", name="purge")
    admin_only = SimpleNamespace(callback=SimpleNamespace(__veil_perms__=["administrator"]), cog_name="Moderation",
                                 qualified_name="x", name="x")
    assert (min_rank_for_command(purge), min_rank_for_command(kick), min_rank_for_command(admin_only)) == (1, 2, 7)


def test_the_dropdown_starts_with_home_then_every_category_in_order():
    view = HelpView(_help(), {"Utility": [_cmd("ping"), _cmd("afk")], "Music": [_cmd("play")]}, is_owner=True)
    select = _select(view)
    assert [type(c) for c in view.children] == [CategorySelect]  # one dropdown, nothing else
    assert [o.label for o in select.options] == ["Home", "Utility", "Music", "Developer"]
    assert [o.description for o in select.options][1:3] == ["2 commands", "1 command"]
    assert select.options[0].default and select.placeholder == "Choose a category…"


def test_the_home_card():
    bot = SimpleNamespace(user=SimpleNamespace(name="Veil Utilities", display_avatar=SimpleNamespace(url="https://x/a.png")),
                          is_owner=AsyncMock(return_value=False))
    h = _help()
    h.context.bot = bot
    view = HelpView(h, {"Utility": [_cmd("a"), _cmd("b")], "Music": [_cmd("c")]})
    card = view.home
    assert card.author.name == "Veil Utilities" and card.thumbnail.url == "https://x/a.png"
    assert card.description.startswith("**information**\n> **3** commands you can use in **2** categories")
    assert "> prefix: `,`" in card.description and "`,help <command>`" in card.description
    assert card.description.endswith("choose a category from the menu below.")


def test_the_home_card_copes_with_a_bot_that_has_no_user_yet():
    card = _help().home_embed(0, {})
    assert card.description.startswith("**information**") and card.author.name is None


# ------------------------------------------------------------------ picking categories

def test_picking_a_category_then_home_swaps_the_card_in_place():
    view = HelpView(_help(), {"Utility": [_cmd("ping"), _cmd("afk")], "Music": []})
    page = _pick(view, "Utility").response.edit_message.call_args.kwargs
    assert page["embed"].title == "Utility" and page["view"] is view and view.current == "Utility"
    assert _select(view).options[1].default  # the dropdown shows where you are
    home = _pick(view, HOME).response.edit_message.call_args.kwargs
    assert view.current is None and home["embed"].description.startswith("**information**")


def test_cards_are_built_when_the_menu_opens():
    view = HelpView(_help(), {"Utility": [_cmd("ping")], "Music": [_cmd("play")]})
    assert set(view.pages) == {"Utility", "Music"}
    assert "play" in view.pages["Music"].description


def test_the_most_used_commands_come_first():
    from core.help import page_order
    names = [c.name for c in page_order("Moderator", [_cmd(n) for n in ("addrole", "kick", "warn", "zap")])]
    assert names == ["warn", "kick", "addrole", "zap"]
    assert [c.name for c in page_order("Admin", [_cmd(n) for n in ("zap", "lock", "ban")])] == ["ban", "lock", "zap"]


def test_a_commands_own_usage_text_wins():
    @commands.command(usage="<member> <role>")
    async def addrole(ctx, *, args: str):
        """Add"""
    assert short_args(addrole) == "<member> <role>"


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


# ------------------------------------------------------------------ category cards

def test_a_category_lists_each_command_with_what_it_does():
    @commands.group(aliases=["r"])
    async def role(ctx):
        """Role tools"""

    @role.command()
    async def add(ctx):
        """Add"""

    page = _help().category_embed("Roles", [_cmd("autorole", "Give a role to new members"), role, _cmd("vanity")])
    assert page.title == "Roles"
    assert page.description.splitlines() == ["`,autorole` · Give a role to new members",
                                             "`,role` · Role tools · *1 subcommand*",
                                             "`,vanity` · " + page.description.splitlines()[2].split(" · ", 1)[1]]
    assert page.footer.text == "3 commands · ,help <command> for details"


def test_long_categories_are_split_into_pages_with_buttons():
    cmds = [_cmd(f"c{i:02}", f"Command {i}") for i in range(23)]
    pages = _help().category_pages("Fun", cmds)
    assert len(pages) == 3 and len(pages[0].description.splitlines()) == 10
    assert pages[0].footer.text.startswith("page 1/3 · 23 commands")
    assert "`,c20`" in pages[2].description


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

def test_command_card_has_aliases_parameters_information_and_usage():
    from cogs.moderation import Moderation

    bot = Veil()
    cog = Moderation(bot)
    asyncio.run(bot.add_cog(cog))
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=bot, author=SimpleNamespace(id=1), guild=None)
    card = h.command_embed(cog.ban)
    fields = {f.name: f.value for f in card.fields}
    assert card.title == "Command: ban"
    assert [f.name for f in card.fields] == ["Aliases", "Parameters", "Information", "Usage"]
    assert [f.inline for f in card.fields] == [True, True, True, False]
    assert fields["Parameters"] == "user, reason"
    assert fields["Information"].startswith("⚠️ Admin+")
    assert fields["Usage"].startswith("```\nSyntax: ,ban ") and "Example: ,ban @user" in fields["Usage"]
    assert card.footer.text.startswith("Module: admin")  # ban needs Admin: it's under Admin, not Moderator


def test_public_commands_say_n_a():
    @commands.command(aliases=["pong"])
    async def ping(ctx):
        """Check latency"""

    ping.cog = None
    card = _help().command_embed(ping)
    fields = {f.name: f.value for f in card.fields}
    assert fields == {"Aliases": "pong", "Parameters": "n/a", "Information": "n/a",
                      "Usage": "```\nSyntax: ,ping\n```"}
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
    _fits(view.home)
    assert len(_select(view).options) <= 25
    for label, page in view.pages.items():
        _fits(page)
        assert "```" not in page.description and page.description.startswith("`,"), label
        for extra in view.books[label][1:]:
            _fits(extra)


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


def test_every_group_pages_through_its_subcommands_within_limits(loaded_bot):
    h = Help()
    sent = []
    ctx = MagicMock()
    ctx.clean_prefix, ctx.bot, ctx.author, ctx.guild = ",", loaded_bot, SimpleNamespace(id=1), None
    ctx.send = AsyncMock(side_effect=lambda **kw: sent.append(kw) or MagicMock())
    h.context = ctx
    h.is_owner_check = AsyncMock(return_value=True)
    groups = [c for c in loaded_bot.walk_commands() if isinstance(c, commands.Group)]

    async def go():
        for g in groups:
            await h.send_group_help(g)

    asyncio.run(go())
    assert len(sent) == len(groups) > 10
    for kw in sent:
        _fits(kw["embed"])
        pages = kw["view"].embeds if kw.get("view") is not None else [kw["embed"]]
        for page in pages:
            _fits(page)
        if len(pages) > 1:
            assert pages[0].footer.text.startswith(f"Page 1/{len(pages)} · Module: ")


# ------------------------------------------------------------------ details found while reading real cards

def test_subcommands_are_filed_under_their_group(loaded_bot):
    from core.help import category_for_command
    assert category_for_command(loaded_bot.get_command("role"), loaded_bot) == "Roles"
    assert category_for_command(loaded_bot.get_command("role add"), loaded_bot) == "Roles"


def test_rank_is_not_repeated_as_a_permission(loaded_bot):
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    perms = next(f.value for f in h.command_embed(loaded_bot.get_command("ban")).fields if f.name == "Information")
    assert perms.startswith("⚠️ Admin+") and "Uperm" not in perms


def test_placeholder_argument_names_never_become_fake_examples(loaded_bot):
    assert example_for(loaded_bot.get_command("role add"), ",") == ",role add @user @role"
    for cmd in loaded_bot.walk_commands():
        assert "arg1" not in example_for(cmd, ","), cmd.qualified_name


def test_a_group_pages_through_its_subcommands(loaded_bot):
    h = Help()
    sent = []
    h.context = MagicMock(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    h.context.send = AsyncMock(side_effect=lambda **kw: sent.append(kw) or MagicMock())
    h.is_owner_check = AsyncMock(return_value=True)
    asyncio.run(h.send_group_help(loaded_bot.get_command("role")))
    pages = sent[0]["view"].embeds
    assert pages[0].title == "Group: role" and any(p.title == "Command: role add" for p in pages)
    assert [c.emoji.name for c in sent[0]["view"].children] == ["◀", "▶", "✖"]


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


def test_parameters_come_from_a_commands_own_usage_line():
    @commands.command(usage="<member> <role>")
    async def role(ctx, *, text: str):
        """Give or take a role"""

    fields = {f.name: f.value for f in _help().command_embed(role).fields}
    assert fields["Parameters"] == "member, role"


def test_long_categories_get_page_buttons(loaded_bot):
    from core.help import PageButton
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    view = HelpView(h, category_commands(loaded_bot), is_owner=True)
    long = next(label for label, pages in view.books.items() if len(pages) > 1)
    view.current, view.page = long, 0
    view._build_items()
    back, forward = [b for b in view.children if isinstance(b, PageButton)]
    assert back.disabled and not forward.disabled
    assert view.embed() is view.books[long][0]
    view.current, view.page = None, 0  # home: just the dropdown
    view._build_items()
    assert not any(isinstance(b, PageButton) for b in view.children)
