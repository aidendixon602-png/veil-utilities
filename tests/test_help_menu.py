"""Permission-aware Veil prefix help: cards, navigation, pagination and visibility."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Literal, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands, tasks

from core.bot import EXTENSIONS, Veil
from core.help import (
    CATEGORIES,
    CategoryButton,
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


def _labels(view: HelpView) -> list[str]:
    select = next((c for c in view.children if isinstance(c, CategorySelect)), None)
    if select:
        return [o.label for o in select.options]
    return [c.category for c in view.children if isinstance(c, CategoryButton)]


def _pick(view: HelpView, value: str):
    it = _it()
    button = next(c for c in view.children if isinstance(c, CategoryButton) and c.category == value)
    asyncio.run(button.callback(it))
    return it


def _it():
    return SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()),
                           user=SimpleNamespace(id=42))


ALL = {label: [] for label, _, _ in CATEGORIES}


# ------------------------------------------------------------------ the category buttons

def test_category_buttons_only_list_categories_the_person_may_open():
    public = {"Utility", "Music", "VoiceMaster", "Community", "Fun", "Images", "Ghost Vanguard"}
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
    assert (min_rank_for_command(purge), min_rank_for_command(kick), min_rank_for_command(admin_only)) == (2, 2, 7)


def test_the_home_buttons_list_categories_in_order():
    view = HelpView(_help(), {"Utility": [_cmd("ping"), _cmd("afk")], "Music": [_cmd("play")]}, is_owner=True)
    assert _labels(view) == ["Utility", "Music", "Developer"]
    assert all(isinstance(c, CategoryButton) and c.style == discord.ButtonStyle.secondary for c in view.children)
    assert [c.row for c in view.children] == [0, 0, 0]


def test_the_home_card():
    bot = SimpleNamespace(user=SimpleNamespace(name="Veil Utilities", display_avatar=SimpleNamespace(url="https://x/a.png")),
                          is_owner=AsyncMock(return_value=False))
    h = _help()
    h.context.bot = bot
    view = HelpView(h, {"Utility": [_cmd("a"), _cmd("b")], "Music": [_cmd("c")]})
    card = view.home
    assert card.author.name == "Veil Utilities" and card.thumbnail.url is None
    assert card.title is None and card.footer.text is None
    assert "**Prefix:** `,`" in card.description
    assert "Select a category" in card.description


def test_the_home_card_copes_with_a_bot_that_has_no_user_yet():
    card = _help().home_embed(0, {})
    assert card.title is None and card.author.name is None


# ------------------------------------------------------------------ picking categories

def test_picking_a_category_then_home_swaps_the_card_in_place():
    view = HelpView(_help(), {"Utility": [_cmd("ping"), _cmd("afk")], "Music": []})
    page = _pick(view, "Utility").response.edit_message.call_args.kwargs
    assert page["embed"].title == "Utility commands" and page["view"] is view and view.current == "Utility"
    from core.help import HelpHomeButton
    assert not any(isinstance(c, (CategorySelect, CategoryButton)) for c in view.children)
    it = _it()
    asyncio.run(next(c for c in view.children if isinstance(c, HelpHomeButton)).callback(it))
    assert view.current is None and it.response.edit_message.call_args.kwargs["embed"] is view.home


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


def test_an_expired_menu_stays_and_only_loses_its_controls():
    view = HelpView(_help(), {"Utility": []})
    view.message = SimpleNamespace(delete=AsyncMock(), edit=AsyncMock())
    asyncio.run(view.on_timeout())
    view.message.delete.assert_not_called()
    view.message.edit.assert_awaited_once_with(view=None)


# ------------------------------------------------------------------ category cards

def test_a_category_lists_each_command_with_what_it_does():
    @commands.group(aliases=["r"])
    async def role(ctx):
        """Role tools"""

    @role.command()
    async def add(ctx):
        """Add"""

    page = _help().category_embed("Roles", [_cmd("autorole", "Give a role to new members"), role, _cmd("vanity")])
    assert page.title == "Roles commands"
    assert page.description.splitlines()[3:] == ["`,autorole` — Give a role to new members",
                                                "`,role` — Role tools · *group*",
                                                "`,vanity` — Does a thing"]
    assert page.footer.text == "Page 1/1 · 3 commands · Select a command below"


def test_long_categories_are_split_into_pages_with_buttons():
    cmds = [_cmd(f"c{i:02}", f"Command {i}") for i in range(23)]
    pages = _help().category_pages("Fun", cmds)
    assert len(pages) == 3 and len(pages[0].description.splitlines()[3:]) == 10
    assert pages[0].footer.text.startswith("Page 1/3 · 23 commands")
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

    assert example_for(ban, ",") == ",ban @user spamming"  # a reason makes it read like real use
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
    lines = card.description.splitlines()
    assert card.title == ",ban" and not card.fields
    assert lines[0] == "**About**" and lines[1] == "Ban a member, or anyone by user ID"
    assert lines[2] == "" and lines[3].startswith("Add `--purge`") and lines[4] == ""
    assert lines[5] == "**Usage** `,ban <user> [reason]`"  # no default values in the syntax
    assert lines[6] == "**Example** `,ban @user spamming`"
    assert lines[7].startswith("**Requires** Admin level")
    assert card.footer.text == "Admin · <required> [optional]"  # ban needs Admin: it's under Admin, not Moderator


def test_a_card_only_shows_what_applies():
    @commands.command(aliases=["pong"])
    async def ping(ctx):
        """Check latency"""

    ping.cog = None
    card = _help().command_embed(ping)
    assert card.description == "**About**\nCheck latency\n\n**Usage** `,ping`\n**Aliases** `pong`"  # no "n/a" anywhere
    assert "n/a" not in card.description


def test_default_values_never_show_in_the_syntax(loaded_bot):
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    for cmd in loaded_bot.walk_commands():
        usage_line = next(line for line in h.command_embed(cmd).description.splitlines() if line.startswith("**Usage**"))
        assert "=" not in usage_line, cmd.qualified_name


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
        # A no-login fixture must not launch production background loops.
        with patch.object(tasks.Loop, "start"):
            for ext in EXTENSIONS:
                await bot.load_extension(ext)
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
    assert len(view.children) <= 25
    assert set(_labels(view)) == set(view.pages)
    for label, page in view.pages.items():
        _fits(page)
        assert "```" not in page.description and "**Commands**" in page.description, label
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
    assert view.pages["Developer"].title == "Developer commands"


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
        assert sent["embed"].title == "Developer commands" and sent["view"].current == "Developer"
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
            assert pages[0].footer.text.startswith(f"page 1/{len(pages)} · ")
            assert "**Subcommands**" in pages[0].description


# ------------------------------------------------------------------ details found while reading real cards

def test_subcommands_are_filed_under_their_group(loaded_bot):
    from core.help import category_for_command
    assert category_for_command(loaded_bot.get_command("role"), loaded_bot) == "Roles"
    assert category_for_command(loaded_bot.get_command("role add"), loaded_bot) == "Roles"


def test_rank_is_not_repeated_as_a_permission(loaded_bot):
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    line = next(x for x in h.command_embed(loaded_bot.get_command("ban")).description.splitlines() if x.startswith("**Requires**"))
    assert line.startswith("**Requires** Admin level") and "Uperm" not in line


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
    assert pages[0].title == ",role" and any(p.title == ",role add" for p in pages)
    assert "`,role add` · " in pages[0].description  # the group's card lists its subcommands
    assert [(str(c.emoji) if c.emoji else c.label) for c in sent[0]["view"].children] == ["◀", f"1 / {len(pages)}", "▶", "✖"]


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

    assert "**Usage** `,role <member> <role>`" in _help().command_embed(role).description


def test_long_categories_get_page_buttons(loaded_bot):
    from core.help import PageButton, category_commands
    h = Help()
    h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
    view = HelpView(h, category_commands(loaded_bot), is_owner=True)
    long = next(label for label, pages in view.books.items() if len(pages) > 1)
    view.current, view.page = long, 0
    view._build_items()
    back, forward = [b for b in view.children if isinstance(b, PageButton)]
    assert back.disabled and not forward.disabled
    assert view.embed() is view.books[long][0]
    view.current, view.page = None, 0  # home: category buttons
    view._build_items()
    assert not any(isinstance(b, PageButton) for b in view.children)


def test_no_command_asks_the_user_for_ctx_or_self(loaded_bot):
    # Commands made by a factory inside a cog once listed `ctx` as an argument, so `,blur` and `,hug` failed with
    # "ctx is a required argument". Every command's arguments must be things a person types.
    wrong = {c.qualified_name: list(c.clean_params) for c in loaded_bot.walk_commands()
             if {"ctx", "self"} & set(c.clean_params)}
    assert not wrong, wrong


def test_every_description_reads_cleanly(loaded_bot):
    # The first line is the summary shown in lists: short, no code, no full stop. Details and examples go below it.
    bad = {}
    for c in loaded_bot.walk_commands():
        first = (c.help or "").strip().split("\n")[0]
        if not first or "`" in first or len(first) > 70 or first.endswith("."):
            bad[c.qualified_name] = first
    assert not bad, bad


# ------------------------------------------------------------------ interactive navigation and privacy

def test_command_dropdown_pagination_back_and_home_callbacks():
    from core.help import CategoryBackButton, CommandSelect, HelpHomeButton, PageButton

    async def callback(ctx):
        """Test command"""

    async def go():
        cmds = [commands.Command(callback, name=f"c{i:02}") for i in range(21)]
        view = HelpView(_help(), {"Utility": cmds})
        _pick_result = _it()
        category = next(c for c in view.children if isinstance(c, CategoryButton) and c.category == "Utility")
        await category.callback(_pick_result)
        assert _pick_result.response.edit_message.call_args.kwargs["embed"].title == "Utility commands"
        next_button = next(b for b in view.children if isinstance(b, PageButton) and b.step == 1)
        await next_button.callback(_it())
        assert view.page == 1
        select = next(c for c in view.children if isinstance(c, CommandSelect))
        assert [o.value for o in select.options] == [f"c{i:02}" for i in range(10, 20)]
        select._values = ["c12"]
        it = _it()
        await select.callback(it)
        assert it.response.edit_message.call_args.kwargs["embed"].title == ",c12"
        assert not any(isinstance(c, CommandSelect) for c in view.children)
        await next(b for b in view.children if isinstance(b, CategoryBackButton)).callback(_it())
        assert view.page == 1 and view.selected is None and view.embed() is view.books["Utility"][1]
        await next(b for b in view.children if isinstance(b, HelpHomeButton)).callback(_it())
        assert view.current is None and view.page == 0 and view.selected is None
        assert len(view.children) == 1

    asyncio.run(go())


def test_nested_subcommands_page_and_back_preserves_position():
    from core.help import CategoryBackButton, CommandSelect, SubcommandSelect, SubPageButton

    @commands.group()
    async def toolbox(ctx):
        """Tools"""

    async def callback(ctx):
        """A tool"""

    for i in range(17):
        toolbox.add_command(commands.Command(callback, name=f"tool{i:02}"))

    async def go():
        view = HelpView(_help(), {"Utility": [toolbox]}, start="Utility")
        select = next(c for c in view.children if isinstance(c, CommandSelect))
        select._values = ["toolbox"]
        await select.callback(_it())
        assert len(next(c for c in view.children if isinstance(c, SubcommandSelect)).options) == 15
        assert "subcommands 1/2" in view.embed().footer.text
        await next(b for b in view.children if isinstance(b, SubPageButton) and b.step == 1).callback(_it())
        assert view.subpage == 1 and "toolbox tool15" in view.embed().description
        select = next(c for c in view.children if isinstance(c, SubcommandSelect))
        assert [o.value for o in select.options] == ["toolbox tool15", "toolbox tool16"]
        select._values = ["toolbox tool16"]
        await select.callback(_it())
        assert view.embed().title == ",toolbox tool16"
        assert not any(isinstance(c, CommandSelect) for c in view.children)
        await next(b for b in view.children if isinstance(b, CategoryBackButton)).callback(_it())
        assert view.selected is toolbox and view.subpage == 1
        await next(b for b in view.children if isinstance(b, CategoryBackButton)).callback(_it())
        assert view.selected is None and view.current == "Utility"

    asyncio.run(go())


def test_visibility_filters_hidden_restricted_ancestors_and_developer_commands():
    from core.help import allowed, visible_command

    @commands.group()
    async def toolbox(ctx):
        """Tools"""

    @toolbox.command()
    async def public(ctx):
        """Public"""

    @toolbox.command(hidden=True)
    async def hidden(ctx):
        """Hidden"""

    @toolbox.group()
    async def restricted(ctx):
        """Restricted"""

    restricted.callback.__veil_perms__ = ["uperm: admin"]

    @restricted.command()
    async def child(ctx):
        """Inherits restriction"""

    @toolbox.group(hidden=True)
    async def secret(ctx):
        """Secret"""

    @secret.command()
    async def secret_child(ctx):
        """Inherits hidden status"""

    dev = SimpleNamespace(cog_name="Owner", hidden=False, parents=[], qualified_name="private")
    assert not visible_command(dev, None, False, 99)
    assert visible_command(dev, None, True, 0)
    assert allowed(toolbox.walk_commands(), None, False, 0) == [public]
    assert set(allowed(toolbox.walk_commands(), None, False, 3)) == {public, restricted, child}
    assert set(allowed(toolbox.walk_commands(), None, True, 0)) == {public, hidden, restricted, child, secret, secret_child}

    async def go():
        view = HelpView(_help(), {"Utility": [toolbox]}, start="Utility")
        await view.show_command(_it(), "toolbox")
        assert "toolbox public" in view.embed().description
        assert "toolbox hidden" not in view.embed().description
        assert "toolbox restricted" not in view.embed().description
        assert [c.name for c in view.current_subcommands()] == ["public"]
        rejected = _it()
        await view.show_subcommand(rejected, "toolbox hidden")
        rejected.response.send_message.assert_awaited_once()
        assert rejected.response.send_message.call_args.kwargs["ephemeral"]

        h = _help()
        h.context.send = AsyncMock(return_value=MagicMock())
        h.is_owner_check = AsyncMock(return_value=False)
        h.get_author_rank = AsyncMock(return_value=0)
        await h.send_group_help(toolbox)
        sent = h.context.send.call_args.kwargs
        pages = sent["view"].embeds
        assert len(pages) == 2
        assert "toolbox public" in pages[0].description
        for page in pages:
            assert all(word not in page.description for word in ("hidden", "restricted", "secret"))

    asyncio.run(go())


def test_invalid_and_stale_navigation_is_rejected_and_menu_is_owned():
    @commands.command()
    async def ping(ctx):
        """Ping"""

    async def go():
        view = HelpView(_help(), {"Utility": [ping]}, start="Utility")
        await view.show_home(_it())
        it = _it()
        await view.show_command(it, "ping")
        it.response.send_message.assert_awaited_once()
        assert view.current is None and view.selected is None
        outsider = SimpleNamespace(user=SimpleNamespace(id=7), client=SimpleNamespace(is_owner=AsyncMock(return_value=False)),
                                   response=SimpleNamespace(send_message=AsyncMock()))
        assert not await view.interaction_check(outsider)
        assert outsider.response.send_message.call_args.kwargs["ephemeral"]

    asyncio.run(go())


def test_real_menu_component_and_embed_limits(loaded_bot):
    from core.help import category_commands

    async def go():
        h = Help()
        h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=SimpleNamespace(id=1), guild=None)
        for owner, rank in ((False, 0), (False, 7), (True, 99)):
            view = HelpView(h, category_commands(loaded_bot), is_owner=owner, author_rank=rank)
            for label in view.pages:
                await view.show_category(_it(), label)
                for page in range(len(view.books[label])):
                    view.page = page
                    view._build_items()
                    for cmd in view.current_items():
                        await view.show_command(_it(), cmd.qualified_name)
                        for subpage in range(view.subpages()):
                            view.subpage = subpage
                            view._build_items()
                            _fits(view.embed())
                            widths = {}
                            for component in view.children:
                                widths[component.row] = widths.get(component.row, 0) + component.width
                                if isinstance(component, discord.ui.Select):
                                    assert 1 <= len(component.options) <= 25
                                    assert all(1 <= len(o.value) <= 100 and 1 <= len(o.label) <= 100 and
                                               (o.description is None or len(o.description) <= 100)
                                               for o in component.options)
                            assert all(width <= 5 for width in widths.values())
                        view.selected = None

    asyncio.run(go())


def test_nested_groups_can_be_opened_and_back_tracks_each_level():
    from core.help import CategoryBackButton, SubcommandSelect

    @commands.group()
    async def tools(ctx):
        """Tools"""

    @tools.group()
    async def image(ctx):
        """Image tools"""

    @image.command()
    async def crop(ctx):
        """Crop an image"""

    async def go():
        view = HelpView(_help(), {"Utility": [tools]}, start="Utility")
        await view.show_command(_it(), "tools")
        for value, expected in (("tools image", ",tools image"), ("tools image crop", ",tools image crop")):
            select = next(c for c in view.children if isinstance(c, SubcommandSelect))
            select._values = [value]
            await select.callback(_it())
            assert view.embed().title == expected
        await next(c for c in view.children if isinstance(c, CategoryBackButton)).callback(_it())
        assert view.selected is image
        await next(c for c in view.children if isinstance(c, CategoryBackButton)).callback(_it())
        assert view.selected is tools
        await next(c for c in view.children if isinstance(c, CategoryBackButton)).callback(_it())
        assert view.selected is None

    asyncio.run(go())


def test_developer_commands_are_only_visible_to_bot_developers(loaded_bot):
    from core.help import visible_command
    dev_commands = developer_commands(loaded_bot)
    assert dev_commands
    assert all(not visible_command(cmd, loaded_bot, False, 99) for cmd in dev_commands)
    assert all(visible_command(cmd, loaded_bot, True, 0) for cmd in dev_commands)


def test_example_only_command_card_has_fallback_description():
    @commands.command()
    async def example(ctx):
        """Example: `,example`"""

    assert "No description yet." in _help().command_embed(example).description


def test_helper_help_respects_actual_level_overrides_fakeperms_and_disabled_commands():
    from core.help import CommandSelect
    from core.utils import has_perms, requires_uperm

    @commands.group()
    @has_perms(manage_messages=True)
    async def tools(ctx):
        """Message tools"""

    @tools.command()
    @requires_uperm("moderator")
    async def restricted(ctx):
        """Higher level"""

    @commands.command()
    @has_perms(manage_messages=True)
    async def clear(ctx):
        """Clear messages"""

    @commands.command()
    async def public(ctx):
        """Public tool"""

    async def go():
        role = SimpleNamespace(id=88, name="staff", mention="<@&88>")
        author = SimpleNamespace(id=42, roles=[role], guild_permissions=discord.Permissions.none())
        cfg = {"users": {"42": "helper"}, "overrides": {"helper": {"perms": []}}}
        store = {"permlevels": cfg, "fakeperms": {}, "settings": {}}
        h = _help()
        h.context.author = author
        h.context.guild = SimpleNamespace(id=1, owner_id=99)
        h.context.channel = SimpleNamespace(permissions_for=lambda member: discord.Permissions.all())
        h.context.bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda guild, key: store.get(key, {})))
        h.context.bot.walk_commands = lambda: [tools, restricted, clear, public]
        access = await h.help_access(1)
        view = HelpView(h, {"Utility": [tools, clear, public]}, start="Utility", author_rank=1, access=access)
        assert [o.value for o in next(c for c in view.children if isinstance(c, CommandSelect)).options] == ["public"]
        assert not await h._can_see(clear)
        assert "clear" not in await h._suggest("clea")

        # An explicit fake permission still works, even when the assigned level lacks it.
        store["fakeperms"] = {"88": ["manage_messages"]}
        access = await h.help_access(1)
        view = HelpView(h, {"Utility": [tools, clear, public]}, start="Utility", author_rank=1, access=access)
        assert access.permits(clear) and access.permits(tools)
        assert {c.name for c in view.current_items()} == {"clear", "public", "tools"}
        await view.show_command(_it(), "tools")
        assert not view.current_subcommands()  # moderator subcommand is still restricted
        store["settings"] = {"disabled": ["clear", "tools"]}
        access = await h.help_access(1)
        view = HelpView(h, {"Utility": [tools, clear, public]}, start="Utility", author_rank=1, access=access)
        assert [c.name for c in view.current_items()] == ["public"]
        assert not await h._can_see(restricted)

        # Native Administrator does not bypass disabled commands.
        author.guild_permissions = discord.Permissions(administrator=True)
        store["settings"] = {"disabled": ["clear"]}
        assert not (await h.help_access(1)).permits(clear)

    asyncio.run(go())


def test_help_respects_custom_target_rank_server_owner_and_storage_failure():
    from core.utils import has_perms, requires_uperm

    @commands.command()
    @requires_uperm("helper")
    @has_perms(manage_messages=True)
    async def clear(ctx):
        """Clear messages"""

    async def go():
        h = _help()
        h.context.author = SimpleNamespace(id=42, roles=[], guild_permissions=discord.Permissions.none())
        h.context.guild = SimpleNamespace(id=1, owner_id=99)
        h.context.channel = SimpleNamespace(permissions_for=lambda member: discord.Permissions.none())
        cfg = {"users": {"42": "helper"}, "overrides": {"helper": {"rank": 2, "perms": ["manage_messages"]}}}
        store = {"permlevels": cfg, "fakeperms": {}, "settings": {}}
        h.context.bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda guild, key: store.get(key, {})))
        assert not (await h.help_access(1)).permits(clear)
        assert (await h.help_access(2)).permits(clear)
        h.context.guild.owner_id = 42
        assert (await h.help_access(1000)).permits(clear)
        h.context.guild.owner_id = 99
        h.context.bot.db.get_config = AsyncMock(side_effect=RuntimeError("storage unavailable"))
        assert not (await h.help_access(1)).permits(clear)

    asyncio.run(go())


def test_commands_shown_at_each_level_pass_their_user_permission_checks(loaded_bot):
    from core.help import category_commands, visible_command
    from core.utils import UserError

    async def go():
        h = Help()
        role = SimpleNamespace(id=900, name="staff", mention="<@&900>")
        author = SimpleNamespace(id=500, roles=[role], guild_permissions=discord.Permissions.none())
        guild = SimpleNamespace(id=42, owner_id=7)
        channel = SimpleNamespace(permissions_for=lambda member: discord.Permissions.none())
        ctx = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=author, guild=guild, channel=channel)
        h.context = ctx
        cases = [("helper", 1, {}), ("moderator", 2, {}), ("admin", 3, {}),
                 ("headadmin", 4, {}), ("overseer", 6, {}), ("supremeleader", 7, {}),
                 ("helper", 1, {"helper": {"perms": []}})]
        for level, rank, overrides in cases:
            cfg = {"users": {"500": level}, "roles": {}, "overrides": overrides}
            store = {"permlevels": cfg, "fakeperms": {}, "settings": {}}
            get_config = AsyncMock(side_effect=lambda guild_id, key: store.get(key, {}))
            with patch.object(loaded_bot.db, "get_config", get_config):
                access = await h.help_access(rank)
                view = HelpView(h, category_commands(loaded_bot), author_rank=rank, access=access)
                assert "Moderator" in view.pages
                for cmd in loaded_bot.walk_commands():
                    if not visible_command(cmd, loaded_bot, False, rank, access):
                        continue
                    ctx.command = cmd
                    for item in [cmd, *cmd.parents]:
                        for check in item.checks:
                            if not check.__qualname__.startswith(("has_perms.<locals>", "requires_uperm.<locals>")):
                                continue
                            try:
                                assert await check(ctx), (level, cmd.qualified_name)
                            except (UserError, commands.CommandError) as exc:
                                pytest.fail(f"{level}: {cmd.qualified_name} shown but permission check denied: {exc}")

    asyncio.run(go())


def test_help_menu_uses_each_role_assigned_level_and_keeps_developer_tools_separate(loaded_bot):
    async def go():
        h = Help()
        role = SimpleNamespace(id=900, name="Staff", mention="<@&900>")
        author = SimpleNamespace(id=500, roles=[role], guild_permissions=discord.Permissions.none())
        guild = SimpleNamespace(id=42, owner_id=7)
        channel = SimpleNamespace(permissions_for=lambda member: discord.Permissions.none())
        sent = AsyncMock(return_value=MagicMock())
        h.context = SimpleNamespace(clean_prefix=",", bot=loaded_bot, author=author, guild=guild, channel=channel,
                                    send=sent)
        h.is_owner_check = AsyncMock(return_value=False)
        levels = [("helper", 1), ("moderator", 2), ("admin", 3), ("headadmin", 4),
                  ("overseer", 6), ("supremeleader", 7)]
        for key, rank in levels:
            store = {"permlevels": {"roles": {"900": key}}, "fakeperms": {}, "settings": {}}
            with patch.object(loaded_bot.db, "get_config", AsyncMock(side_effect=lambda gid, module: store.get(module, {}))):
                await h.open_menu()
                view = sent.await_args.kwargs["view"]
                assert view.author_rank == rank
                assert "Developer" not in view.pages
                moderator = {c.name for c in view.items.get("Moderator", [])}
                admin = {c.name for c in view.items.get("Admin", [])}
                assert ("purge" in moderator) == (rank >= 2)
                assert {"warn", "timeout", "cases"} <= moderator
                assert ("kick" in moderator) == (rank >= 2)
                assert ("ban" in admin) == (rank >= 3)
                assert ("Permissions" in view.pages) == (rank >= 6)
                assert ("Antinuke" in view.pages) == (rank >= 7)

        # The real server owner gets server-owner-only help, not the developer category.
        guild.owner_id = author.id
        with patch.object(loaded_bot.db, "get_config", AsyncMock(return_value={})):
            await h.open_menu()
            view = sent.await_args.kwargs["view"]
            assert view.author_rank == 1000 and "Developer" not in view.pages
        h.is_owner_check = AsyncMock(return_value=True)
        await h.open_menu()
        assert "Developer" in sent.await_args.kwargs["view"].pages

    asyncio.run(go())


def test_command_cards_show_inherited_and_custom_level_requirements():
    from core.utils import has_perms, requires_uperm

    @commands.group()
    @requires_uperm("moderator")
    @has_perms(manage_messages=True)
    async def toolbox(ctx):
        """Moderation tools"""

    @toolbox.command()
    async def child(ctx):
        """A subcommand"""

    async def go():
        h = _help()
        h.context.author = SimpleNamespace(id=42, roles=[], guild_permissions=discord.Permissions.none())
        h.context.guild = SimpleNamespace(id=1, owner_id=99)
        h.context.channel = SimpleNamespace(permissions_for=lambda member: discord.Permissions.none())
        cfg = {"users": {"42": "helper"}, "overrides": {
            "helper": {"rank": 5, "perms": ["manage_messages"]}, "moderator": {"rank": 5, "name": "Senior Mod"}}}
        store = {"permlevels": cfg, "fakeperms": {}, "settings": {}}
        h.context.bot.db = SimpleNamespace(get_config=AsyncMock(side_effect=lambda guild, key: store.get(key, {})))
        h.context.send = AsyncMock()
        await h.send_command_help(child)
        description = h.context.send.await_args.kwargs["embed"].description
        assert "**Requires** Senior Mod level, Manage Messages" in description
        access = await h.help_access(5)
        view = HelpView(h, {"Community": [toolbox]}, start="Community", author_rank=5, access=access)
        await view.show_command(_it(), "toolbox")
        await view.show_subcommand(_it(), "toolbox child")
        assert "**Requires** Senior Mod level, Manage Messages" in view.embed().description

    asyncio.run(go())


def test_home_button_layout_fits_all_current_categories():
    view = HelpView(_help(), ALL, is_owner=True, author_rank=10000)
    assert len(view.pages) == len(CATEGORIES) + 1
    assert len(view.children) == len(view.pages) == 15
    assert _labels(view) == list(view.pages)
    for row in range(5):
        assert sum(component.row == row for component in view.children) == 3
    assert all(button.style == discord.ButtonStyle.secondary for button in view.children)


def test_home_falls_back_to_select_if_category_catalog_grows():
    import core.help as help_module

    extra = [("Extra One", [], []), ("Extra Two", [], [])]
    with patch.object(help_module, "CATEGORIES", help_module.CATEGORIES + extra):
        categories = {label: [] for label, _, _ in help_module.CATEGORIES}
        view = HelpView(_help(), categories, is_owner=True)
        assert len(view.children) == 1 and isinstance(view.children[0], CategorySelect)
        assert _labels(view) == list(view.pages)
        select = view.children[0]
        select._values = ["Extra Two"]
        it = _it()
        asyncio.run(select.callback(it))
        assert view.current == "Extra Two"
        it.response.edit_message.assert_awaited_once()


def test_home_card_uses_the_configured_prefix():
    card = _help("!").home_embed()
    assert card.description == "Select a category below to view its commands.\n\n**Prefix:** `!`"
