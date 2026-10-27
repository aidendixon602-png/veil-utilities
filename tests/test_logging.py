"""Logging: which channel gets what, the compact embeds, the ,logs panel and commands, and the ban-log button."""
from __future__ import annotations

import asyncio
import logging as pylogging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands, tasks

import cogs.logging as L
from cogs.logging import OFF, BanLogButton, ChannelPick, KindSelect, LogPanel, Logging, canon, describe
from core import logembeds as fmt
from core.bot import EXTENSIONS, Veil
from core.utils import Colors, Ignored, UserError


@pytest.fixture(autouse=True)
def no_audit_wait(monkeypatch):
    monkeypatch.setattr(fmt, "AUDIT_SETTLE", 0)
    monkeypatch.setattr(L, "FEED_WAIT", 0)  # feed lines go out straight away in tests


def test_feed_lines_arriving_together_share_one_message_until_it_is_full(monkeypatch):
    async def go():
        channel = text_channel(1, "command-logs")
        posted = SimpleNamespace(edit=AsyncMock())
        channel.send.return_value = posted
        cog, _ = make_cog()
        await asyncio.gather(*(cog.post_line(channel, f"line {i}") for i in range(3)))
        assert channel.send.await_count == 1 and posted.edit.await_args.kwargs["content"] == "line 0\nline 1\nline 2"
        monkeypatch.setattr(L, "FEED_LIMIT", 20)  # no room left: a new message starts
        await cog.post_line(channel, "line 3 is too long to fit")
        assert channel.send.await_count == 2
    asyncio.run(go())


# ------------------------------------------------------------------ tiny stand-ins for Discord objects

class Obj(SimpleNamespace):
    """Hashable (usable as a dict key, like Discord's own objects) and prints as its name."""
    __hash__ = object.__hash__

    def __str__(self):
        return getattr(self, "name", "obj")


def text_channel(cid: int, name: str, *, can_send: bool = True):
    ch = MagicMock(spec=discord.TextChannel)  # a spec'd mock really is a TextChannel as far as isinstance goes
    ch.id, ch.name, ch.mention = cid, name, f"<#{cid}>"
    ch.send, ch.delete, ch.edit = AsyncMock(), AsyncMock(), AsyncMock()
    ch.category_id, ch.topic = None, None
    ch.permissions_for.return_value = SimpleNamespace(view_channel=can_send, send_messages=can_send, embed_links=can_send)
    return ch


def make_guild(channels=(), *, manage_channels=True, view_audit_log=False, gid=10):
    chans = {c.id: c for c in channels}
    me = Obj(id=2, guild_permissions=SimpleNamespace(manage_channels=manage_channels, view_audit_log=view_audit_log))
    guild = SimpleNamespace(id=gid, me=me, default_role=Obj(id=1), text_channels=list(channels),
                            categories=[], icon=None, owner_id=100, member_count=50,
                            get_channel=lambda cid: chans.get(cid), get_member=lambda uid: None)
    for c in channels:
        c.guild = guild
    return guild


def make_cog(cfg=None):
    store = {"logs": dict(cfg or {})}

    async def get_config(gid, module, default=None):
        return store.setdefault(module, {})

    async def set_config(gid, module, data):
        store[module] = data

    bot = SimpleNamespace(db=SimpleNamespace(get_config=AsyncMock(side_effect=get_config),
                                             set_config=AsyncMock(side_effect=set_config)),
                          add_dynamic_items=MagicMock(), remove_dynamic_items=MagicMock(),
                          is_owner=AsyncMock(return_value=False))
    return Logging(bot), store


def member(uid=7, name="ham", display=None, *, roles=(), bot=False, created_days=400):
    return Obj(
        id=uid, name=name, display_name=display or name, mention=f"<@{uid}>", bot=bot,
        display_avatar=SimpleNamespace(url="https://cdn.example/a.png"), roles=list(roles),
        created_at=discord.utils.utcnow() - timedelta(days=created_days), joined_at=discord.utils.utcnow() - timedelta(days=3),
        guild=SimpleNamespace(id=10, member_count=50, icon=None), nick=None, timed_out_until=None)


def role(rid, name="Mod", color=0, default=False):
    return SimpleNamespace(id=rid, name=name, mention=f"<@&{rid}>", color=discord.Colour(color), is_default=lambda: default,
                           guild=SimpleNamespace(icon=None), hoist=False, mentionable=False,
                           permissions=discord.Permissions.none())


def message(*, content="hello", author=None, channel=None, attachments=(), mid=500):
    author = author or member()
    channel = channel or SimpleNamespace(id=30, mention="<#30>", name="general")
    return SimpleNamespace(id=mid, content=content, author=author, channel=channel, attachments=list(attachments),
                           guild=SimpleNamespace(id=10), jump_url="https://discord.com/channels/10/30/500",
                           created_at=discord.utils.utcnow())


# ------------------------------------------------------------------ names and which channel is used

def test_category_names_and_aliases_resolve():
    assert canon("Moderation") == canon("mod") == canon("bans") == "bans"
    assert canon("Joins & leaves") == canon("joins") == "joinleave"
    assert canon("VC") == "voice" and canon("Messages") == "messages"
    assert canon("nonsense") is None
    assert set(L.TYPES) == set(L.KINDS)


def test_explicit_channel_wins_then_recognisable_names_then_server_log():
    msg, auto_roles, srv = text_channel(1, "my-messages"), text_channel(2, "role-logs"), text_channel(3, "server-logs")
    guild = make_guild([msg, auto_roles, srv])
    cfg = {"messages": 1}
    assert L._channel_for(guild, cfg, "messages") is msg            # set in the config
    assert L._channel_for(guild, cfg, "roles") is auto_roles        # found by name
    assert L._channel_for(guild, cfg, "voice") is srv               # no voice channel: general server log
    assert L._channel_for(guild, cfg, "commands") is None           # commands never spill into the server log
    assert L._channel_for(guild, cfg, "bans") is None


def test_off_means_off_even_when_a_channel_with_a_matching_name_exists():
    guild = make_guild([text_channel(1, "message-logs")])
    assert L._channel_for(guild, {"messages": OFF}, "messages") is None
    assert describe(guild, {"messages": OFF}, "messages") == "`off`"


def test_joins_and_leaves_follow_the_member_log_unless_given_their_own():
    members, joins = text_channel(1, "x"), text_channel(2, "y")
    guild = make_guild([members, joins])
    assert L._channel_for(guild, {"members": 1}, "joinleave") is members
    assert L._channel_for(guild, {"members": 1, "joinleave": 2}, "joinleave") is joins
    assert L._channel_for(guild, {"members": OFF}, "joinleave") is None
    assert L._channel_for(make_guild([text_channel(3, "join-logs")]), {}, "joinleave").name == "join-logs"


def test_legacy_channel_names_still_work():
    guild = make_guild([text_channel(1, "bans"), text_channel(2, "veil-utilities-logging"), text_channel(3, "command-logs")])
    assert L._channel_for(guild, {}, "bans").name == "bans"
    assert L._channel_for(guild, {}, "commands").name == "command-logs"
    assert L._channel_for(guild, {}, "server").name == "veil-utilities-logging"
    assert L._channel_for(guild, {}, "antinuke").name == "veil-utilities-logging"


def test_describe_reads_naturally_for_the_panel():
    guild = make_guild([text_channel(1, "message-logs"), text_channel(2, "elsewhere")])
    assert describe(guild, {"roles": 2}, "roles") == "<#2>"
    assert describe(guild, {}, "messages") == "<#1> *(auto)*"
    assert describe(guild, {}, "voice") == "`not set`"


def test_route_skips_ignored_channels_and_members_and_tries_fallbacks_in_order():
    async def go():
        mods, members = text_channel(1, "a"), text_channel(2, "b")
        guild = make_guild([mods, members])
        cog, _ = make_cog({"bans": 1, "members": 2, "messages": 2, "ignore": {"channels": [99], "users": [55]}})
        assert await cog.route(guild, "messages") is members
        assert await cog.route(guild, "messages", channel_id=99) is None
        assert await cog.route(guild, "messages", user_id=55) is None
        assert await cog.route(guild, "bans", "members") is mods
        cog2, _ = make_cog({"bans": OFF, "members": 2})
        assert await cog2.route(guild, "bans", "members") is members
        assert await cog.route(None if False else guild, "voice") is None
    asyncio.run(go())


# ------------------------------------------------------------------ creating the channels

def creating_guild(existing=(), **kw):
    created = []

    async def create_text_channel(name, **kwargs):
        ch = text_channel(100 + len(created), name)
        created.append((ch, kwargs))
        guild.text_channels.append(ch)
        guild.get_channel = lambda cid, _old=guild.get_channel: next((c for c, _ in created if c.id == cid), None) or _old(cid)
        return ch

    async def create_category(name, **kwargs):
        cat = SimpleNamespace(id=99, name=name)
        guild.categories.append(cat)
        return cat

    guild = make_guild(existing, **kw)
    guild.create_text_channel, guild.create_category = create_text_channel, create_category
    return guild, created


def test_setup_creates_one_private_channel_per_category_and_points_the_config_at_them():
    async def go():
        guild, created = creating_guild()
        cog, store = make_cog()
        made = await cog.ensure_default_channels(guild)
        names = {c.name for c in made}
        assert names == {"message-logs", "member-logs", "role-logs", "channel-logs", "voice-logs", "mod-logs",
                         "server-logs", "command-logs", "antinuke-logs"}
        overwrites = created[0][1]["overwrites"]
        assert overwrites[guild.default_role].view_channel is False
        assert all(kwargs["category"].name == "VEIL LOGS" for _, kwargs in created)
        by_name = {c.name: c.id for c in made}
        assert store["logs"]["messages"] == by_name["message-logs"] and store["logs"]["bans"] == by_name["mod-logs"]
    asyncio.run(go())


def test_setup_reuses_existing_channels_and_respects_off_and_existing_settings():
    async def go():
        existing = text_channel(1, "message-logs")
        chosen = text_channel(2, "my-roles")
        guild, created = creating_guild([existing, chosen])
        cog, store = make_cog({"roles": 2, "voice": OFF})
        made = await cog.ensure_default_channels(guild)
        names = {c.name for c in made}
        assert "message-logs" not in names and "role-logs" not in names and "voice-logs" not in names
        assert store["logs"]["messages"] == 1          # bound to the channel that was already there
        assert store["logs"]["roles"] == 2 and store["logs"]["voice"] == OFF
    asyncio.run(go())


def test_setup_needs_manage_channels_and_does_nothing_when_all_set():
    async def go():
        cog, _ = make_cog()
        guild, _ = creating_guild(manage_channels=False)
        assert await cog.ensure_default_channels(guild) == []
        ids = {k: i for i, k in enumerate(L.SETUP_KINDS, start=1)}
        done = [text_channel(i, f"c{i}") for i in ids.values()]
        cog2, _ = make_cog(ids)
        assert await cog2.ensure_default_channels(make_guild(done)) == []
    asyncio.run(go())


# ------------------------------------------------------------------ the cards (A + B) and the feed lines (C)

def body(e: discord.Embed) -> list[str]:
    return (e.description or "").splitlines()


def detail(e: discord.Embed, label: str) -> str | None:
    """The value of a `**Label:** value` line on a card."""
    prefix = f"**{label}:** "
    return next((line[len(prefix):] for line in body(e) if line.startswith(prefix)), None)


def test_a_card_is_the_person_a_bold_sentence_and_labelled_lines():
    deleted = fmt.message_deleted(message(content="line one\nline two", attachments=[
        SimpleNamespace(filename="cat.png", url="https://cdn/cat.png")]))
    assert deleted.color.value == Colors.error
    assert deleted.author.name == "ham" and deleted.author.icon_url == "https://cdn.example/a.png"
    assert body(deleted)[0] == "**Message sent by <@7> deleted in <#30>**"
    assert "> line one\n> line two" in deleted.description
    assert detail(deleted, "Attachments") == "[cat.png](https://cdn/cat.png)" and detail(deleted, "Sent").startswith("<t:")
    assert deleted.title is None and not deleted.fields and deleted.thumbnail.url is None
    assert deleted.footer.text == "User ID: 7 · Message ID: 500"
    assert deleted.timestamp is not None and "Today at" not in deleted.footer.text  # one time, shown by Discord


def test_empty_details_are_left_out():
    card = fmt.entry_card(Colors.error, "x happened", details=[("Reason", ""), ("Who", None), ("Where", "here")])
    assert body(card) == ["**x happened**", "**Where:** here"]
    assert fmt.person(member(5, "bad")) == "<@5> (`bad`)" and fmt.person(None) == "*unknown*"


def test_deleted_by_a_moderator_says_so_but_self_deletes_do_not():
    assert detail(fmt.message_deleted(message(), actor=member(9, "mod")), "Deleted by") == "<@9>"
    assert detail(fmt.message_deleted(message(), actor=message().author), "Deleted by") is None
    assert detail(fmt.message_deleted(message()), "Deleted by") is None


def test_an_edit_is_one_feed_line_with_before_and_after():
    line = fmt.message_edited(message(content="old text"), message(content="new text"))
    assert line.startswith("<t:") and ":t> ✏️ <@7> edited a message in <#30>: `old text` → `new text`" in line
    assert line.endswith("· [jump](https://discord.com/channels/10/30/500)")
    long = fmt.message_edited(message(content="x" * 5000), message(content="y\n" * 3000))
    assert len(long) <= 1900 and "\n" not in long


def test_long_content_is_cut_to_fit_discords_limits():
    big = fmt.message_deleted(message(content="z" * 9000))
    assert len(big.description) <= 4096


def test_lists_are_cut_with_a_count_instead_of_overflowing():
    lines = [f"• line number {i} with some padding to make it long enough" for i in range(60)]
    out = fmt.bullets(lines)
    assert len(out) <= 1024 and out.splitlines()[-1].startswith("*+") and out.endswith("more*")
    assert fmt.bullets(["a", "b"]) == "a\nb"


def test_join_flags_brand_new_accounts_and_shows_the_member_number():
    fresh = fmt.member_joined(member(created_days=2))
    old = fmt.member_joined(member(created_days=900))
    assert "⚠️ **new account**" in detail(fresh, "Account") and "⚠️" not in detail(old, "Account")
    assert body(old)[0] == "**<@7> joined the server**" and detail(old, "Member") == "#50"
    assert old.footer.text == "User ID: 7" and old.color.value == Colors.success and old.author.name == "ham"
    assert detail(fmt.member_joined(member(bot=True)), "Bot") == "yes"


def test_leave_and_kick_are_different_cards():
    m = member(roles=[role(1, "everyone", default=True), role(2, "Mod"), role(3, "VIP")])
    left = fmt.member_left(m)
    assert left.color.value == Colors.error and body(left)[0] == "**<@7> left the server**"
    assert detail(left, "Roles") == "<@&3>, <@&2>" and "was here 3 days" in detail(left, "Joined")
    assert detail(left, "Members now") == "50"
    kicked = fmt.member_left(m, member(9, "mod"), "spam")
    assert kicked.color.value == Colors.warn and body(kicked)[0] == "**<@7> was kicked by <@9>**"
    assert detail(kicked, "Reason") == "spam" and detail(kicked, "Members now") is None


def test_ban_and_unban_cards_name_the_moderator_and_the_reason():
    user = member(5, "bad")
    banned = fmt.user_banned(user, member(9, "mod"), "raid")
    assert banned.author.name == "bad" and banned.color.value == Colors.error
    assert body(banned)[0] == "**<@5> was banned by <@9>**" and detail(banned, "Reason") == "raid"
    assert detail(banned, "Account").startswith("created <t:")
    unknown = fmt.user_banned(user)
    assert body(unknown)[0] == "**<@5> was banned**" and detail(unknown, "Reason") == "no reason given"
    clicked = fmt.user_unbanned(user, member(9, "mod"), clicked=True)
    assert clicked.color.value == Colors.success and body(clicked)[0] == "**<@5> was unbanned by <@9>**"
    assert detail(clicked, "How") == "the button on the ban log" and detail(clicked, "Reason") is None


def test_nickname_card_shows_both_names_and_who_changed_it():
    e = fmt.nickname_changed(member(), "old", None, member(9, "mod"))
    assert body(e) == ["**<@7>'s nickname was changed by <@9>**", "**Before:** `old`", "**After:** *none*"]
    assert body(fmt.nickname_changed(member(), None, "new", member()))[0] == "**<@7>'s nickname was changed**"


def test_role_change_cards_pick_a_colour_and_list_each_side():
    m, added, removed = member(), [role(2, "Mod")], [role(3, "VIP")]
    given = fmt.roles_changed(m, added, [])
    assert given.color.value == Colors.success and body(given)[0] == "**<@7> was given roles**"
    assert body(fmt.roles_changed(m, [], removed))[0] == "**<@7> had roles taken**"
    mixed = fmt.roles_changed(m, added, removed, member(9, "mod"), "cleanup")
    assert mixed.color.value == Colors.blurple
    assert body(mixed) == ["**<@7> had their roles changed by <@9>**", "**Added:** <@&2>", "**Removed:** <@&3>",
                           "**Reason:** cleanup"]


def test_handing_out_a_dangerous_role_is_flagged():
    admin = role(2, "Boss")
    admin.permissions = discord.Permissions(administrator=True)
    mgr = role(3, "Mgr")
    mgr.permissions = discord.Permissions(manage_roles=True, kick_members=True)
    card = fmt.roles_changed(member(), [admin, mgr, role(4, "Plain")], [])
    assert detail(card, "⚠️ Dangerous") == "<@&2> has administrator; <@&3> has manage roles, kick members"
    assert fmt.dangerous(admin) == ["administrator"] and fmt.dangerous(role(9)) == []


def test_timeout_cards():
    until = discord.utils.utcnow() + timedelta(hours=1, minutes=5)
    e = fmt.timed_out(member(), until, member(9, "mod"), "calm down")
    assert body(e)[0] == "**<@7> was timed out by <@9>**"
    assert detail(e, "Duration") == "1 hour, 5 minutes" and detail(e, "Reason") == "calm down"
    assert detail(e, "Ends").startswith("<t:")
    cleared = fmt.timeout_removed(member(), member(9, "mod"))
    assert body(cleared) == ["**<@7>'s timeout was removed by <@9>**"]


def test_role_diffs_only_list_what_changed():
    a, b = role(1, "Old"), role(1, "New", color=0xFF0000)
    b.permissions = discord.Permissions(ban_members=True)
    changes = fmt.role_changes(a, b)
    assert "Name: `Old` → `New`" in changes and "Colour: `default` → `#FF0000`" in changes
    assert "Permissions added: `ban members`" in changes
    assert fmt.role_changes(a, a) == []
    assert "Permissions removed" in fmt.role_changes(b, a)[-1]


def test_role_cards_show_who_did_it_the_details_and_the_danger():
    r = role(2, "VIP", color=0x00FF00)
    r.permissions, r.position = discord.Permissions(administrator=True), 4
    mod = member(9, "mod")
    created = fmt.role_created(r, mod)
    assert created.color.value == 0x00FF00 and created.author.name == "mod"  # the header is who did it
    assert body(created)[0] == "**The role <@&2> was created by <@9>**"
    assert detail(created, "Details") == "colour #00FF00 · position 4"
    assert detail(created, "⚠️ Dangerous") == "administrator" and created.footer.text == "Role ID: 2"
    deleted = fmt.role_deleted(role(2, "VIP"), mod, "Antinuke: Mass Role Delete")
    assert body(deleted)[0] == "**The role `@VIP` was deleted by <@9>**"
    assert detail(deleted, "Reason") == "Antinuke: Mass Role Delete"
    assert fmt.role_deleted(role(2, "VIP")).author.name == "VIP"  # nobody known: the role's name heads it
    updated = fmt.role_updated(r, ["Name: `a` → `b`"], mod)
    assert body(updated)[:2] == ["**The role <@&2> was updated by <@9>**", "**Name:** `a` → `b`"]


def test_channel_cards_name_the_kind_category_and_who_did_it():
    cat = SimpleNamespace(name="Staff")
    text = MagicMock(spec=discord.TextChannel, id=4, mention="<#4>", category=cat, guild=SimpleNamespace(icon=None),
                      topic="staff only", nsfw=False, slowmode_delay=5, position=3)
    text.name = "chat"
    voice = MagicMock(spec=discord.VoiceChannel, id=5, mention="<#5>", category=None, guild=SimpleNamespace(icon=None),
                      position=1, nsfw=False, slowmode_delay=0, topic=None)
    voice.name = "Lounge"
    created = fmt.channel_created(text, member(9, "mod"))
    assert body(created) == ["**The channel <#4> was created by <@9>**", "**Type:** Text channel",
                             "**Category:** Staff", "**Topic:** staff only", "**Slowmode:** 5s"]
    assert created.footer.text == "Channel ID: 4"
    deleted = fmt.channel_deleted(voice)
    assert deleted.author.name == "#Lounge" and body(deleted)[0] == "**The channel `#Lounge` was deleted**"
    assert detail(deleted, "Category") == "none"


def test_channel_diffs():
    def chan(**kw):
        base = dict(name="a", category=None, topic=None, slowmode_delay=0, nsfw=False, user_limit=0, overwrites={})
        base.update(kw)
        return SimpleNamespace(**base)

    target = Obj(mention="<@&8>")
    changes = fmt.channel_changes(chan(), chan(name="b", slowmode_delay=5, topic="hi", overwrites={target: object()}))
    assert "Name: `#a` → `#b`" in changes and "Slowmode: `0s` → `5s`" in changes
    assert any(c.startswith("Topic:") for c in changes) and "Permissions changed for <@&8>" in changes
    assert fmt.channel_changes(chan(), chan()) == []


def test_server_diffs_and_cards():
    def guild(**kw):
        base = dict(name="S", vanity_url_code=None, icon=None, banner=None, id=1,
                    verification_level=discord.VerificationLevel.low)
        base.update(kw)
        return SimpleNamespace(**base)

    changes = fmt.server_changes(guild(), guild(name="T", verification_level=discord.VerificationLevel.high))
    assert "Name: `S` → `T`" in changes and any("Verification level" in c for c in changes)
    assert fmt.server_changes(guild(), guild()) == []
    card = fmt.server_updated(guild(), changes, member(9, "mod"))
    assert card.footer.text == "Server ID: 1" and body(card)[0] == "**The server was updated by <@9>**"
    assert detail(card, "Name") == "`S` → `T`"


def test_invite_and_emoji_cards():
    inv = SimpleNamespace(code="abc", channel=SimpleNamespace(mention="<#1>"), inviter=member(9, "mod"), max_uses=5,
                          max_age=7200, guild=SimpleNamespace(icon=None), uses=3, temporary=False)
    e = fmt.invite_created(inv)
    assert body(e) == ["**An invite for <#1> was created by <@9>**", "**Link:** discord.gg/abc", "**Uses:** 5 uses",
                       "**Expires:** in 2h"]
    gone = fmt.invite_deleted(inv)
    assert body(gone)[0] == "**An invite for <#1> was deleted**" and detail(gone, "Used") == "3 times"
    forever = fmt.invite_created(SimpleNamespace(**{**inv.__dict__, "max_uses": 0, "max_age": 0}))
    assert detail(forever, "Uses") == "unlimited" and detail(forever, "Expires") == "never"
    emoji = SimpleNamespace(name="a", __str__=lambda s: "<:a:1>")
    emoji_card = fmt.emojis_changed(SimpleNamespace(id=1, icon=None, name="S"), [emoji], [SimpleNamespace(name="gone")])
    assert "`:a:`" in detail(emoji_card, "Added") and detail(emoji_card, "Removed") == "`:gone:`"


def test_voice_changes_are_feed_lines():
    ch1 = SimpleNamespace(id=1, mention="<#1>", name="Lounge", members=[1, 2])
    ch2 = SimpleNamespace(id=2, mention="<#2>", name="Gaming")
    state = lambda ch, mute=False, deaf=False: SimpleNamespace(channel=ch, mute=mute, deaf=deaf)  # noqa: E731
    m = member()
    assert fmt.voice_changed(m, state(None), state(ch1)).endswith(":t> 🔊 <@7> joined <#1>")
    assert fmt.voice_changed(m, state(ch1), state(None)).endswith("🔇 <@7> left <#1>")
    assert fmt.voice_changed(m, state(ch1), state(ch2)).endswith("↪️ <@7> moved <#1> → <#2>")
    assert fmt.voice_changed(m, state(ch1), state(ch1, mute=True)).endswith("🔕 <@7> was server muted in <#1>")
    assert fmt.voice_changed(m, state(ch1), state(ch1)) is None


def cmd_ctx(content=",ban @x spam", name="ban", invoked="ban"):
    return SimpleNamespace(message=SimpleNamespace(content=content, id=900, jump_url="https://discord.com/channels/10/5/900"),
                           channel=SimpleNamespace(mention="<#5>"), author=member(),
                           command=SimpleNamespace(name=name, qualified_name=name, aliases=["b"], cog_name="Moderation"), invoked_with=invoked)


def test_a_command_is_one_feed_line():
    line = fmt.command_used(cmd_ctx(), "Admin", "Owner", rank=3, ms=84.4)
    assert line.startswith("<t:") and line.endswith(
        ":t> ⌨️ <@7> ran `,ban @x spam` in <#5> · [jump](https://discord.com/channels/10/5/900) · 84 ms")
    assert "6.2s" in fmt.command_used(cmd_ctx(), "None", "-", ms=6175)
    assert "\n" not in fmt.command_used(cmd_ctx("a\nb"), "None", "-")


def test_a_failed_command_says_why_on_its_line():
    line = fmt.command_used(cmd_ctx(), "None", "-", error="You need the Admin permission level")
    assert ":t> ❌ <@7> ran `,ban @x spam` in <#5>" in line
    assert line.endswith("· failed: You need the Admin permission level")


def test_bulk_delete_transcript_is_oldest_first():
    first, second = message(content="one", mid=1), message(content="two", mid=2)
    lines = fmt.transcript([second, first]).splitlines()
    assert lines[0].endswith("ham: one") and lines[1].endswith("ham: two")
    card = fmt.bulk_deleted([first, second])
    assert card.author.name == "#general" and body(card)[0] == "**2 messages deleted in <#30>**"
    assert detail(card, "From") == "<@7> ×2" and detail(card, "Transcript") == "attached"
    assert card.footer.text == "Channel ID: 30"


def test_a_burst_of_deletions_is_one_card_with_the_names():
    roles = [role(i, f"testnuke-role-{i}") for i in range(1, 31)]
    card = fmt.grouped("role_delete", roles, member(9, "mod"), "Antinuke test (,testnuke)")
    assert card.color.value == Colors.error and card.author.name == "mod"
    assert body(card)[0] == "**30 roles were deleted by <@9>**"
    assert detail(card, "Roles").startswith("@testnuke-role-1, @testnuke-role-2,") and detail(card, "Roles").endswith("+5 more")
    assert detail(card, "Reason") == "Antinuke test (,testnuke)"
    made = fmt.grouped("role_create", roles[:2])
    assert body(made)[0] == "**2 roles were created**" and detail(made, "Roles") == "<@&1>, <@&2>"


def test_case_cards_follow_the_same_shape():
    case = fmt.case_card(12, "ban", member(5, "bad"), member(9, "mod"), "raid")
    assert case.author.name == "bad" and body(case) == ["**Case #12 · Ban: <@5> by <@9>**", "**Reason:** raid"]
    assert case.footer.text == "User ID: 5 · Case: 12"
    by_id = fmt.case_card(13, "unban", discord.Object(5), member(9, "mod"), None)
    assert body(by_id) == ["**Case #13 · Unban: <@5> by <@9>**", "**Reason:** no reason given"]


# ------------------------------------------------------------------ who did it (audit log)

def audit_guild(entries, *, allowed=True):
    async def audit_logs(limit, action):
        for entry in entries:
            yield entry
    guild = make_guild(view_audit_log=allowed)
    guild.audit_logs = audit_logs
    return guild


def entry(target_id, user, *, age=2, reason=None, **extra):
    return SimpleNamespace(target=SimpleNamespace(id=target_id), user=user, reason=reason,
                           created_at=discord.utils.utcnow() - timedelta(seconds=age), extra=SimpleNamespace(**extra))


def test_audit_actor_finds_a_recent_matching_entry():
    mod = member(9, "mod")
    guild = audit_guild([entry(1, member(8)), entry(5, mod, reason="spam")])
    assert asyncio.run(fmt.audit_actor(guild, discord.AuditLogAction.ban, 5)) == (mod, "spam")


def test_audit_actor_ignores_old_entries_missing_access_and_errors():
    mod = member(9, "mod")
    assert asyncio.run(fmt.audit_actor(audit_guild([entry(5, mod, age=600)]), discord.AuditLogAction.ban, 5)) == (None, None)
    assert asyncio.run(fmt.audit_actor(audit_guild([entry(5, mod)], allowed=False), discord.AuditLogAction.ban, 5)) == (None, None)

    guild = make_guild(view_audit_log=True)

    def boom(limit, action):
        raise discord.Forbidden(MagicMock(status=403, reason="no"), "nope")
    guild.audit_logs = boom
    assert asyncio.run(fmt.audit_actor(guild, discord.AuditLogAction.ban, 5)) == (None, None)


def test_audit_actor_can_require_extra_details_to_match():
    mod = member(9, "mod")
    guild = audit_guild([entry(5, mod, channel=SimpleNamespace(id=1)), entry(5, member(8, "other"), channel=SimpleNamespace(id=2))])
    found, _ = asyncio.run(fmt.audit_actor(guild, discord.AuditLogAction.message_delete, 5,
                                           check=lambda e: e.extra.channel.id == 2))
    assert found.id == 8


# ------------------------------------------------------------------ listeners end to end

def test_deleted_message_is_posted_to_the_message_channel_only_when_there_is_one():
    async def go():
        channel = text_channel(1, "message-logs")
        guild = make_guild([channel])
        cog, _ = make_cog({"messages": 1})
        m = message()
        m.guild = guild
        await cog.on_message_delete(m)
        embed = channel.send.await_args.kwargs["embed"]
        assert embed.author.name == "ham" and body(embed)[0] == "**Message sent by <@7> deleted in <#30>**"

        empty_cog, _ = make_cog({})
        guild_without = make_guild([])
        m2 = message()
        m2.guild = guild_without
        await empty_cog.on_message_delete(m2)  # nothing set: quietly nothing
    asyncio.run(go())


def test_bots_and_ignored_sources_are_not_logged():
    async def go():
        channel = text_channel(1, "message-logs")
        guild = make_guild([channel])
        cog, _ = make_cog({"messages": 1, "ignore": {"channels": [30], "users": []}})
        bot_msg = message(author=member(bot=True))
        bot_msg.guild = guild
        await cog.on_message_delete(bot_msg)
        ignored = message()
        ignored.guild = guild
        await cog.on_message_delete(ignored)
        await cog.on_message_edit(message(content="a"), SimpleNamespace(**{**vars(message(content="b")), "guild": guild}))
        channel.send.assert_not_awaited()
    asyncio.run(go())


def test_no_audit_log_lookup_when_nothing_would_be_logged(monkeypatch):
    async def go():
        calls = []

        async def spy(*a, **k):
            calls.append(a)
            return None, None
        monkeypatch.setattr(fmt, "audit_actor", spy)
        cog, _ = make_cog({})
        m = message()
        m.guild = make_guild([])
        await cog.on_message_delete(m)
        await cog.on_member_ban(m.guild, member())
        assert calls == []
    asyncio.run(go())


def test_bans_get_the_unban_button_and_the_moderator(monkeypatch):
    async def go():
        channel = text_channel(1, "mod-logs")
        guild = audit_guild([entry(5, member(9, "mod"), reason="raid")])
        guild.text_channels, guild.get_channel = [channel], {1: channel}.get
        cog, _ = make_cog({"bans": 1})
        await cog.on_member_ban(guild, member(5, "bad"))
        kwargs = channel.send.await_args.kwargs
        banned = kwargs["embed"]
        assert body(banned)[0] == "**<@5> was banned by <@9>**" and detail(banned, "Reason") == "raid"
        button = kwargs["view"].children[0]
        assert button.item.label == "Unban" and button.item.custom_id == "banlog:10:5:1"
    asyncio.run(go())


def test_a_kick_goes_to_the_moderation_channel_and_a_plain_leave_to_the_leave_log():
    async def go():
        mod, left = text_channel(1, "mod-logs"), text_channel(2, "member-logs")
        guild = audit_guild([entry(5, member(9, "mod"), reason="spam")])
        guild.text_channels, guild.get_channel = [mod, left], {1: mod, 2: left}.get
        cog, _ = make_cog({"bans": 1, "members": 2})
        m = member(5, "gone")
        m.guild = guild
        await cog.on_member_remove(m)
        assert body(mod.send.await_args.kwargs["embed"])[0] == "**<@5> was kicked by <@9>**"
        left.send.assert_not_awaited()

        guild2 = audit_guild([])
        guild2.text_channels, guild2.get_channel = [mod, left], {1: mod, 2: left}.get
        m2 = member(6, "left")
        m2.guild = guild2
        await cog.on_member_remove(m2)
        assert body(left.send.await_args.kwargs["embed"])[0] == "**<@6> left the server**"
    asyncio.run(go())


def test_role_changes_are_merged_into_one_log(monkeypatch):
    async def go():
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        channel = text_channel(1, "role-logs")
        guild = make_guild([channel])
        cog, _ = make_cog({"roles": 1})
        a, b, c = role(2, "A"), role(3, "B"), role(4, "C")
        m = member()
        before = SimpleNamespace(**{**vars(m), "roles": [], "guild": guild})
        step1 = SimpleNamespace(**{**vars(m), "roles": [a], "guild": guild})
        step2 = SimpleNamespace(**{**vars(m), "roles": [a, b, c], "guild": guild})
        await cog.on_member_update(before, step1)
        await cog.on_member_update(step1, step2)
        await asyncio.gather(*[t for t in cog._role_update_tasks.values()], return_exceptions=True)
        assert channel.send.await_count == 1
        embed = channel.send.await_args.kwargs["embed"]
        assert body(embed)[0] == "**<@7> was given roles**" and detail(embed, "Added") == "<@&2>, <@&3>, <@&4>"
    asyncio.run(go())


def test_a_burst_of_role_deletions_is_one_card_that_grows(monkeypatch):
    async def go():
        monkeypatch.setattr(L, "BURST_WAIT", 0.01)
        channel = text_channel(1, "role-logs")
        posted = SimpleNamespace(edit=AsyncMock())
        channel.send.return_value = posted
        guild = make_guild([channel])
        cog, _ = make_cog({"roles": 1})
        roles = []
        for i in range(1, 7):
            r = role(i, f"testnuke-role-{i}")
            r.guild, r.position = guild, 1
            roles.append(r)
        await asyncio.gather(*(cog.on_guild_role_delete(r) for r in roles[:5]))
        assert channel.send.await_count == 1
        assert body(channel.send.await_args.kwargs["embed"])[0] == "**5 roles were deleted**"
        await cog.on_guild_role_delete(roles[5])  # a straggler a moment later joins the same card
        assert channel.send.await_count == 1
        assert body(posted.edit.await_args.kwargs["embed"])[0] == "**6 roles were deleted**"
    asyncio.run(go())


def test_a_single_deletion_still_gets_its_own_card(monkeypatch):
    async def go():
        monkeypatch.setattr(L, "BURST_WAIT", 0.01)
        channel = text_channel(1, "channel-logs")
        guild = make_guild([channel])
        cog, _ = make_cog({"channels": 1})
        gone = MagicMock(spec=discord.TextChannel, id=4, mention="<#4>", category=None, guild=guild, position=2,
                         nsfw=False, slowmode_delay=0)
        gone.name = "chat"
        await cog.on_guild_channel_delete(gone)
        assert body(channel.send.await_args.kwargs["embed"])[0] == "**The channel `#chat` was deleted**"
    asyncio.run(go())


def test_timeouts_use_the_moderation_channel_and_fall_back_to_members():
    async def go():
        mod, members = text_channel(1, "mod-logs"), text_channel(2, "member-logs")
        guild = make_guild([mod, members])
        until = discord.utils.utcnow() + timedelta(hours=1)
        m = member()
        before = SimpleNamespace(**{**vars(m), "guild": guild})
        after = SimpleNamespace(**{**vars(m), "guild": guild, "timed_out_until": until})
        cog, _ = make_cog({"bans": 1, "members": 2})
        await cog.on_member_update(before, after)
        assert mod.send.await_count == 1 and members.send.await_count == 0
        cog2, _ = make_cog({"bans": OFF, "members": 2})
        await cog2.on_member_update(before, after)
        assert members.send.await_count == 1
    asyncio.run(go())


def test_a_bans_channel_only_gets_bans_timeouts_go_to_the_member_log():
    async def go():
        bans, members = text_channel(1, "bans"), text_channel(2, "member-logs")
        guild = make_guild([bans, members])
        until = discord.utils.utcnow() + timedelta(hours=1)
        m = member()
        before = SimpleNamespace(**{**vars(m), "guild": guild})
        after = SimpleNamespace(**{**vars(m), "guild": guild, "timed_out_until": until})
        cog, _ = make_cog({})  # nothing set: both picked up by name, like the screenshot's server
        await cog.on_member_update(before, after)
        bans.send.assert_not_awaited()
        assert body(members.send.await_args.kwargs["embed"])[0] == "**<@7> was timed out**"
        for name in ("ban-logs", "🔨│bans", "BANS"):
            assert L.bans_only(SimpleNamespace(name=name))
        assert not L.bans_only(SimpleNamespace(name="mod-logs"))
    asyncio.run(go())


def test_timeouts_can_have_their_own_channel():
    async def go():
        mod, mutes = text_channel(1, "mod-logs"), text_channel(2, "mute-logs")
        guild = make_guild([mod, mutes])
        m = member()
        before = SimpleNamespace(**{**vars(m), "guild": guild})
        after = SimpleNamespace(**{**vars(m), "guild": guild,
                                   "timed_out_until": discord.utils.utcnow() + timedelta(hours=1)})
        cog, _ = make_cog({})
        await cog.on_member_update(before, after)
        assert mutes.send.await_count == 1 and mod.send.await_count == 0
        assert canon("mutes") == canon("timeout") == "timeouts"
    asyncio.run(go())


def test_a_kick_skips_a_bans_only_channel_and_goes_with_the_leaves():
    async def go():
        bans, joins = text_channel(1, "bans"), text_channel(2, "join-logs")
        guild = audit_guild([entry(5, member(9, "mod"), reason="spam")])
        guild.text_channels, guild.get_channel = [bans, joins], {1: bans, 2: joins}.get
        cog, _ = make_cog({})
        gone = member(5, "gone")
        gone.guild = guild
        await cog.on_member_remove(gone)
        bans.send.assert_not_awaited()
        assert body(joins.send.await_args.kwargs["embed"])[0] == "**<@5> was kicked by <@9>**"
    asyncio.run(go())


def test_actions_the_bot_did_for_a_command_credit_whoever_ran_it():
    mod = member(145140603963414941, "web3_w2z")
    guild = SimpleNamespace(me=Obj(id=2), get_member=lambda uid: mod if uid == mod.id else None)
    bot = Obj(id=2)
    assert fmt.on_behalf(guild, bot, "web3_w2z (145140603963414941): spamming") == (mod, "spamming")
    assert fmt.on_behalf(guild, bot, "web3_w2z (145140603963414941): None") == (mod, None)  # old "no reason"
    assert fmt.on_behalf(guild, bot, "web3_w2z (145140603963414941)") == (mod, None)
    assert fmt.on_behalf(guild, bot, "softban by web3_w2z (145140603963414941): raid") == (mod, "softban: raid")
    assert fmt.on_behalf(guild, bot, "Antinuke: Mass Ban") == (bot, "Antinuke: Mass Ban")  # the bot's own doing
    assert fmt.on_behalf(guild, bot, "ghost (999999999999999999): x") == (bot, "ghost (999999999999999999): x")
    other = member(9, "someone")
    assert fmt.on_behalf(guild, other, "web3_w2z (145140603963414941): x") == (other, "web3_w2z (145140603963414941): x")


def test_audit_reasons_say_who_asked():
    from core.utils import audit_reason
    who = Obj(id=145140603963414941, name="web3_w2z")  # prints as its name, like a discord.User
    assert audit_reason(who, "spam") == "web3_w2z (145140603963414941): spam"
    assert audit_reason(who) == "web3_w2z (145140603963414941)"
    assert audit_reason(who, "raid", "softban") == "softban by web3_w2z (145140603963414941): raid"
    assert len(audit_reason(who, "x" * 2000)) <= 512


def test_command_logging_respects_ignores_and_names_the_rank(monkeypatch):
    async def go():
        channel = text_channel(1, "command-logs")
        guild = make_guild([channel])
        guild.owner_id = 77
        cog, _ = make_cog({"commands": 1, "ignore": {"users": [8]}})
        cog.bot.owner_ids = frozenset()
        author = member(77, "owner")
        ctx = SimpleNamespace(guild=guild, author=author, channel=SimpleNamespace(id=3, mention="<#3>"),
                              message=SimpleNamespace(content=",ping", id=1, jump_url="https://x/y"),
                              command=SimpleNamespace(name="ping", qualified_name="ping", aliases=[], cog_name="Utility"),
                              invoked_with="ping")
        await cog.on_command_completion(ctx)
        assert "⌨️ <@77> ran `,ping` in <#3>" in channel.send.await_args.args[0]
        assert channel.send.await_args.kwargs["allowed_mentions"].users is False  # a log line never pings
        channel.send.reset_mock()
        ctx.author = member(8, "ignored")
        await cog.on_command_completion(ctx)
        channel.send.assert_not_awaited()
    asyncio.run(go())


def _command_setup():
    channel = text_channel(1, "command-logs")
    guild = make_guild([channel])
    guild.owner_id = 77
    cog, _ = make_cog({"commands": 1})
    cog.bot.owner_ids = frozenset()
    ctx = SimpleNamespace(guild=guild, author=member(77, "owner"), channel=SimpleNamespace(id=3, mention="<#3>"),
                          message=SimpleNamespace(content=",ban @x", id=1, jump_url="https://x/y"),
                          command=SimpleNamespace(name="ban", qualified_name="ban", aliases=[], cog_name="Moderation"),
                          invoked_with="ban")
    return channel, cog, ctx


def test_the_command_line_says_how_long_it_took():
    async def go():
        channel, cog, ctx = _command_setup()
        await cog.on_command(ctx)
        await cog.on_command_completion(ctx)
        assert channel.send.await_args.args[0].endswith(" ms")
    asyncio.run(go())


def test_failed_commands_get_a_line_with_the_reason():
    async def go():
        channel, cog, ctx = _command_setup()
        await cog.on_command(ctx)
        await cog.on_command_error(ctx, UserError("that member is above you"))
        line = channel.send.await_args.args[0]
        assert "❌ <@77> ran `,ban @x`" in line and line.endswith("· failed: that member is above you")
        posted = channel.send.return_value
        posted.edit = AsyncMock()
        crash = commands.CommandInvokeError(ValueError("boom"))
        await cog.on_command_error(ctx, crash)
        grouped = posted.edit.await_args.kwargs["content"].splitlines()  # the next line joins the same message
        assert len(grouped) == 2 and grouped[1].endswith("· failed: ValueError: boom")
        assert channel.send.await_count == 1
    asyncio.run(go())


def test_noise_is_not_logged_as_a_failed_command():
    async def go():
        channel, cog, ctx = _command_setup()
        for noise in (Ignored(), commands.CommandNotFound(), commands.CommandOnCooldown(commands.Cooldown(1, 5), 3, commands.BucketType.user)):
            await cog.on_command_error(ctx, noise)
        ctx.command = None  # a message that only looked like a command
        await cog.on_command_error(ctx, commands.BadArgument("x"))
        channel.send.assert_not_awaited()
    asyncio.run(go())


def test_send_helper_for_other_cogs_posts_to_the_named_category():
    async def go():
        channel = text_channel(1, "server-logs")
        cog, _ = make_cog({"server": 1})
        guild = make_guild([channel])
        assert await cog.send(guild, "server", discord.Embed(description="x")) is True
        assert await cog.send(guild, "moderation", discord.Embed(description="x")) is False
        channel.send.side_effect = discord.Forbidden(MagicMock(status=403, reason="no"), "x")
        assert await cog.send(guild, "server", discord.Embed(description="x")) is False
    asyncio.run(go())


# ------------------------------------------------------------------ ,logs commands

def command_ctx(guild, cfg_cog):
    ctx = MagicMock()
    ctx.guild = guild
    ctx.clean_prefix = ","
    ctx.approve, ctx.send = AsyncMock(), AsyncMock()
    return ctx


def test_set_command_accepts_a_channel_off_auto_and_all(monkeypatch):
    async def go():
        target = text_channel(1, "logs-here")
        guild = make_guild([target])
        cog, store = make_cog()
        ctx = command_ctx(guild, cog)
        monkeypatch.setattr(commands.TextChannelConverter, "convert", AsyncMock(return_value=target))
        await cog.logs_set.callback(cog, ctx, "mod", "#logs-here")
        assert store["logs"]["bans"] == 1
        await cog.logs_set.callback(cog, ctx, "Messages", "off")
        assert store["logs"]["messages"] == OFF
        await cog.logs_set.callback(cog, ctx, "messages", "auto")
        assert store["logs"]["messages"] is None
        await cog.logs_set.callback(cog, ctx, "all", "#logs-here")
        assert all(store["logs"][k] == 1 for k in L.TYPES)
        assert "all logs" in ctx.approve.await_args.args[0]
    asyncio.run(go())


def test_set_command_rejects_unknown_categories_and_channels_it_cannot_post_in(monkeypatch):
    async def go():
        quiet = text_channel(1, "locked", can_send=False)
        guild = make_guild([quiet])
        cog, _ = make_cog()
        ctx = command_ctx(guild, cog)
        with pytest.raises(UserError, match="Unknown category"):
            await cog.logs_set.callback(cog, ctx, "bogus", "off")
        monkeypatch.setattr(commands.TextChannelConverter, "convert", AsyncMock(return_value=quiet))
        with pytest.raises(UserError, match="Embed Links"):
            await cog.logs_set.callback(cog, ctx, "messages", "#locked")
    asyncio.run(go())


def test_ignore_list_add_remove_and_duplicates():
    async def go():
        cog, store = make_cog()
        guild = make_guild([])
        ctx = command_ctx(guild, cog)
        channel = text_channel(5, "ignored")
        await cog._toggle_ignore(ctx, channel, True)
        await cog._toggle_ignore(ctx, member(8), True)
        assert store["logs"]["ignore"] == {"channels": [5], "users": [8]}
        with pytest.raises(UserError, match="already ignored"):
            await cog._toggle_ignore(ctx, channel, True)
        await cog._toggle_ignore(ctx, channel, False)
        assert store["logs"]["ignore"]["channels"] == []
        with pytest.raises(UserError, match="not ignored"):
            await cog._toggle_ignore(ctx, channel, False)
    asyncio.run(go())


def test_test_command_reports_working_broken_and_unset_categories():
    async def go():
        good, broken = text_channel(1, "message-logs"), text_channel(2, "role-logs")
        broken.send.side_effect = discord.Forbidden(MagicMock(status=403, reason="no"), "x")
        guild = make_guild([good, broken])
        cog, _ = make_cog({"messages": 1, "roles": 2})
        ctx = command_ctx(guild, cog)
        ctx.author = member()
        await cog.logs_test.callback(cog, ctx)
        report = ctx.send.await_args.kwargs["embed"]
        assert "Messages → <#1>" in report.description and "Couldn't post in: Roles" in report.description
        assert "Not set:" in report.description and report.color.value == Colors.error
    asyncio.run(go())


def test_cleanup_deletes_only_duplicate_log_channels():
    async def go():
        a, dup, other = text_channel(1, "message-logs"), text_channel(2, "message-logs"), text_channel(3, "general")
        for c in (a, dup, other):
            c.delete = AsyncMock()
        empty = SimpleNamespace(name="veil logs", channels=[], delete=AsyncMock())
        guild = make_guild([a, dup, other])
        guild.categories = [empty]
        cog, _ = make_cog()
        ctx = command_ctx(guild, cog)
        await cog.logs_cleanup.callback(cog, ctx)
        dup.delete.assert_awaited_once()
        a.delete.assert_not_awaited()
        other.delete.assert_not_awaited()
        empty.delete.assert_awaited_once()
        assert "1" in ctx.approve.await_args.args[0]
    asyncio.run(go())


# ------------------------------------------------------------------ the panel

def panel(cfg=None, channels=(), author=42):
    guild = make_guild(channels or [text_channel(1, "message-logs")])
    cog, store = make_cog(cfg)
    view = LogPanel(cog, guild, author, store["logs"])
    return view, cog, store, guild


def interaction():
    return SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock(),
                                                    is_done=lambda: False),
                           edit_original_response=AsyncMock(), user=SimpleNamespace(id=42))


def test_panel_lists_every_category_and_starts_without_a_channel_picker():
    view, *_ = panel()
    assert [type(c) for c in view.children].count(KindSelect) == 1
    assert not any(isinstance(c, ChannelPick) for c in view.children)
    options = next(c for c in view.children if isinstance(c, KindSelect)).options
    assert [o.value for o in options] == list(L.TYPES)
    text = view.embed().description
    assert text.count("\n") == len(L.KINDS) - 1 and "💬 **Messages** — <#1> *(auto)*" in text
    buttons = {c.label: c for c in view.children if isinstance(c, L.PanelButton)}
    assert buttons["Turn off"].disabled and buttons["Auto"].disabled and not buttons["Create channels"].disabled


def test_choosing_a_category_reveals_the_channel_picker_and_enables_the_buttons():
    async def go():
        view, *_ = panel()
        it = interaction()
        await view.choose(it, "roles")
        assert any(isinstance(c, ChannelPick) for c in view.children)
        assert next(c for c in view.children if isinstance(c, L.PanelButton) and c.label == "Turn off").disabled is False
        assert "▸ 🏷️ **Roles**" in view.embed().description
        assert [o.value for o in next(c for c in view.children if isinstance(c, KindSelect)).options if o.default] == ["roles"]
    asyncio.run(go())


def test_picking_a_channel_saves_it_but_refuses_one_the_bot_cannot_use():
    async def go():
        ok, locked = text_channel(1, "ok"), text_channel(2, "locked", can_send=False)
        view, cog, store, guild = panel(channels=[ok, locked])
        view.selected = "voice"
        it = interaction()
        await view.assign(it, 1)
        assert store["logs"]["voice"] == 1
        it2 = interaction()
        await view.assign(it2, 2)
        assert store["logs"]["voice"] == 1
        assert "Embed Links" in it2.response.send_message.await_args.args[0]
    asyncio.run(go())


def test_panel_off_and_auto_buttons_update_the_config():
    async def go():
        view, cog, store, _ = panel({"voice": 1})
        view.selected = "voice"
        await view.do_off(interaction())
        assert store["logs"]["voice"] == OFF and "`off`" in view.embed().description
        await view.do_auto(interaction())
        assert store["logs"]["voice"] is None
    asyncio.run(go())


def test_panel_create_button_runs_setup_and_needs_the_permission():
    async def go():
        guild, created = creating_guild()
        cog, store = make_cog()
        view = LogPanel(cog, guild, 42, store["logs"])
        it = interaction()
        await view.do_create(it)
        assert len(created) == len(L.SETUP_KINDS)
        assert it.edit_original_response.await_args.kwargs["embed"].description.count("<#") >= len(L.SETUP_KINDS)

        locked_guild, _ = creating_guild(manage_channels=False)
        view2 = LogPanel(cog, locked_guild, 42, {})
        it2 = interaction()
        await view2.do_create(it2)
        assert "Manage Channels" in it2.response.send_message.await_args.args[0]
    asyncio.run(go())


def test_only_the_person_who_opened_the_panel_can_use_it():
    async def go():
        view, *_ = panel(author=42)
        stranger = interaction()
        stranger.user = SimpleNamespace(id=99)
        stranger.client = SimpleNamespace(is_owner=AsyncMock(return_value=False))
        assert await view.interaction_check(stranger) is False
        owner = interaction()
        assert await view.interaction_check(owner) is True
    asyncio.run(go())


# ------------------------------------------------------------------ the unban / reban button

def button_interaction(user_id, *, rank_cfg=None, owner_id=100, admin=False):
    member_ = Obj(id=user_id, name="clicker", mention=f"<@{user_id}>",
                  guild_permissions=discord.Permissions.all() if admin else discord.Permissions.none())
    guild = SimpleNamespace(id=1, owner_id=owner_id, get_member=lambda uid: member_, unban=AsyncMock(), ban=AsyncMock())
    client = SimpleNamespace(get_guild=lambda gid: guild, is_owner=AsyncMock(return_value=False),
                             get_user=lambda uid: SimpleNamespace(name="target", id=uid),
                             db=SimpleNamespace(get_config=AsyncMock(return_value=rank_cfg or {})))
    return SimpleNamespace(guild=guild, user=member_, client=client,
                           response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()),
                           followup=SimpleNamespace(send=AsyncMock())), guild


def test_button_state_lives_in_the_custom_id_so_it_survives_restarts():
    button = BanLogButton(1, 123, True)
    assert button.item.label == "Unban" and button.item.style == discord.ButtonStyle.success
    assert button.item.custom_id == "banlog:1:123:1"
    reban = BanLogButton(1, 123, False)
    assert reban.item.label == "Reban" and reban.item.style == discord.ButtonStyle.danger
    match = BanLogButton.__discord_ui_compiled_template__.fullmatch("banlog:1:123:0")
    rebuilt = asyncio.run(BanLogButton.from_custom_id(None, None, match))
    assert (rebuilt.guild_id, rebuilt.user_id, rebuilt.is_banned) == (1, 123, False)


def test_button_is_for_admin_plus_not_discord_administrators_and_never_for_yourself():
    async def go():
        button = BanLogButton(1, 123, False)
        it, _ = button_interaction(999, admin=True)  # Discord admin, but no bot rank
        await button.callback(it)
        assert "**Admin** permission level or higher" in it.response.send_message.await_args.args[0]
        it.guild.ban.assert_not_awaited()

        me, _ = button_interaction(123, owner_id=123)  # the server owner is the target
        await button.callback(me)
        assert "cannot ban or unban yourself" in me.response.send_message.await_args.args[0]
    asyncio.run(go())


def test_admins_can_unban_and_the_button_flips_to_reban():
    async def go():
        it, guild = button_interaction(300, rank_cfg={"users": {"300": "admin"}})
        await BanLogButton(1, 123, True).callback(it)
        guild.unban.assert_awaited_once()
        flipped = it.response.edit_message.await_args.kwargs["view"].children[0]
        assert flipped.item.label == "Reban" and flipped.item.custom_id == "banlog:1:123:0"
        it.followup.send.assert_not_awaited()  # no extra message in the channel: the card says it

        it2, guild2 = button_interaction(300, rank_cfg={"users": {"300": "admin"}})
        await BanLogButton(1, 123, False).callback(it2)
        guild2.ban.assert_awaited_once()
        assert it2.response.edit_message.await_args.kwargs["view"].children[0].item.label == "Unban"
    asyncio.run(go())


def test_button_reports_discord_errors_instead_of_flipping():
    async def go():
        it, guild = button_interaction(300, rank_cfg={"users": {"300": "admin"}})
        guild.unban.side_effect = discord.NotFound(MagicMock(status=404, reason="x"), "unknown ban")
        await BanLogButton(1, 123, True).callback(it)
        assert "isn't banned any more" in it.response.send_message.await_args.args[0]
        it.response.edit_message.assert_not_awaited()
    asyncio.run(go())


def test_clicking_notes_it_on_the_card_and_the_unban_card_names_the_clicker():
    async def go():
        it, guild = button_interaction(300, rank_cfg={"users": {"300": "admin"}})
        logs, _ = make_cog({"bans": 1})
        it.client.get_cog = lambda name: logs if name == "Logging" else None
        it.message = SimpleNamespace(embeds=[discord.Embed(description="<@123> · banned by <@9>")])
        await BanLogButton(1, 123, True).callback(it)
        edited = it.response.edit_message.await_args.kwargs["embed"]
        assert edited.description.startswith("<@123> · banned by <@9>\n-# 🔓 unbanned by <@300> <t:")  # a note added
        it.followup.send.assert_not_awaited()

        channel = text_channel(1, "mod-logs")
        g = make_guild([channel], gid=1)  # the audit log would say the bot did it: the click says who really did
        await logs.on_member_unban(g, member(123, "target"))
        card = channel.send.await_args.kwargs["embed"]
        assert body(card)[0] == "**<@123> was unbanned by <@300>**"
        assert detail(card, "How") == "the button on the ban log"
        assert logs.take_click(1, 123, "unban") is None  # used once
    asyncio.run(go())


def test_a_failed_click_is_forgotten():
    async def go():
        it, guild = button_interaction(300, rank_cfg={"users": {"300": "admin"}})
        logs, _ = make_cog()
        it.client.get_cog = lambda name: logs
        guild.unban.side_effect = discord.HTTPException(MagicMock(status=500, reason="x"), "down")
        await BanLogButton(1, 123, True).callback(it)
        assert "Couldn't unban **target**" in it.response.send_message.await_args.args[0]
        assert logs.take_click(1, 123, "unban") is None
    asyncio.run(go())


def test_the_cog_registers_the_button_so_old_ban_logs_keep_working():
    async def go():
        cog, _ = make_cog()
        await cog.cog_load()
        cog.bot.add_dynamic_items.assert_called_once_with(BanLogButton)
        await cog.cog_unload()
        cog.bot.remove_dynamic_items.assert_called_once_with(BanLogButton)
    asyncio.run(go())


# ------------------------------------------------------------------ other modules

def test_antinuke_alerts_go_to_the_channel_chosen_in_the_panel():
    from cogs.antinuke import Antinuke

    async def go():
        chosen = text_channel(1, "whatever-i-picked")
        guild = make_guild([chosen])
        logs, _ = make_cog({"antinuke": 1})
        cog = Antinuke.__new__(Antinuke)
        cog.bot = SimpleNamespace(get_cog=lambda name: logs if name == "Logging" else None)
        assert await cog._log_channel(guild, {}) is chosen
        own = text_channel(2, "own")
        guild2 = make_guild([own, chosen])
        assert await cog._log_channel(guild2, {"log": 2}) is own   # antinuke's own setting still wins
        cog.bot = SimpleNamespace()
        assert await cog._log_channel(guild, {}) is None
    asyncio.run(go())


def test_case_log_uses_the_log_card():
    from core.modlog import create_case

    async def go():
        sent = text_channel(1, "cases")
        guild = make_guild([sent])
        store = {"settings": {"modlog": 1}}
        bot = SimpleNamespace(db=SimpleNamespace(
            fetchone=AsyncMock(return_value={"n": 12}), execute=AsyncMock(),
            get_config=AsyncMock(side_effect=lambda gid, mod: store[mod])))
        mod, target = member(9, "mod"), member(5, "bad")
        assert await create_case(bot, guild, "ban", target, mod, "raid") == 12
        embed = sent.send.await_args.kwargs["embed"]
        assert embed.author.name == "bad" and body(embed) == ["**Case #12 · Ban: <@5> by <@9>**", "**Reason:** raid"]
        assert embed.footer.text == "User ID: 5 · Case: 12"
    asyncio.run(go())


# ------------------------------------------------------------------ the real bot

@pytest.fixture(scope="module")
def loaded_bot():
    pylogging.disable(pylogging.CRITICAL)
    loop = asyncio.new_event_loop()

    async def build():
        bot = Veil()
        await bot.db.connect()
        for ext in EXTENSIONS:
            await bot.load_extension(ext)
        for cog in bot.cogs.values():
            for attr in list(vars(cog).values()):
                if isinstance(attr, tasks.Loop):
                    attr.cancel()
        return bot

    bot = loop.run_until_complete(build())
    yield bot
    loop.run_until_complete(bot.db.close())
    loop.close()
    pylogging.disable(pylogging.NOTSET)


def test_real_bot_exposes_logs_with_its_aliases_and_subcommands(loaded_bot):
    logs = loaded_bot.get_command("logs")
    assert loaded_bot.get_command("log") is logs
    assert {c.name for c in logs.commands} >= {"set", "off", "auto", "setup", "test", "ignore", "cleanup"}
    assert loaded_bot.get_command("setuplogs") and loaded_bot.get_command("cleanuplogs")
    assert "BanLogButton" in {c.__name__ for c in loaded_bot._connection._view_store._dynamic_items.values()}


def test_every_log_command_needs_manage_server(loaded_bot):
    for cmd in [loaded_bot.get_command("logs"), *loaded_bot.get_command("logs").walk_commands(),
                loaded_bot.get_command("setuplogs")]:
        assert getattr(cmd.callback, "__veil_perms__", None) or cmd.checks, cmd.qualified_name


def test_setup_splits_the_old_catch_all_channel_and_files_log_channels_under_veil_logs():
    """The server from the screenshot: everything pointed at #veil-utilities-logging, log channels at the top."""
    async def go():
        veil = text_channel(1, "veil-utilities-logging")
        joins, bans, cmds, anti = (text_channel(2, "join-logs"), text_channel(3, "bans"),
                                   text_channel(4, "command-logs"), text_channel(5, "antinuke-logs"))
        general, roles = text_channel(6, "general"), text_channel(7, "roles")
        anti.category_id = 50
        guild, created = creating_guild([veil, joins, bans, cmds, anti, general, roles])
        guild.categories = [SimpleNamespace(id=50, name="VEIL LOGS")]
        shared = {k: 1 for k in ("messages", "members", "roles", "channels", "voice", "server")}
        cog, store = make_cog({**shared, "bans": 3, "commands": 4, "antinuke": 5})

        report = await cog.organize(guild)

        assert {c.name for c in report.created} == {"message-logs", "member-logs", "role-logs", "channel-logs",
                                                    "voice-logs", "server-logs"}
        assert all(kwargs["category"].id == 50 for _, kwargs in created)       # made inside the existing category
        assert {c.name for c in report.moved} == {"join-logs", "bans", "command-logs", "veil-utilities-logging"}
        for ch in (joins, bans, cmds, veil):
            assert ch.edit.await_args.kwargs["category"].id == 50
        anti.edit.assert_not_awaited()                                         # already there
        general.edit.assert_not_awaited()                                      # never touch non-log channels
        roles.edit.assert_not_awaited()
        assert [c.name for c in report.unused] == ["veil-utilities-logging"]   # filed and listed, not deleted
        veil.delete.assert_not_awaited()
        assert store["logs"]["bans"] == 3 and store["logs"]["commands"] == 4  # dedicated ones kept
        assert store["logs"]["messages"] != 1
    asyncio.run(go())


def test_a_second_setup_changes_nothing():
    async def go():
        guild, created = creating_guild()
        cog, _ = make_cog()
        await cog.organize(guild)
        for ch, _ in created:
            ch.category_id = 99
        again = await cog.organize(guild)
        assert not again.created and not again.moved and not again.unused
    asyncio.run(go())


def test_a_channel_named_for_joins_beats_the_member_log_fallback():
    joins, members = text_channel(2, "join-logs"), text_channel(8, "member-logs")
    guild = make_guild([joins, members])
    assert L._channel_for(guild, {"members": 8}, "joinleave") is joins
    assert L._channel_for(guild, {"members": 8, "joinleave": OFF}, "joinleave") is None


def cat(cid, name, position):
    return SimpleNamespace(id=cid, name=name, position=position, edit=AsyncMock())


def test_veil_logs_is_moved_to_the_top_of_the_channel_list():
    async def go():
        guild, _ = creating_guild([text_channel(1, "message-logs")])
        text, voice, logs = cat(40, "Text Channels", 0), cat(41, "Voice Channels", 1), cat(50, "VEIL LOGS", 2)
        guild.categories = [text, voice, logs]
        cog, _ = make_cog()
        report = await cog.organize(guild)
        assert report.raised
        assert logs.edit.await_args.kwargs["position"] == 0
        text.edit.assert_not_awaited() and voice.edit.assert_not_awaited()
    asyncio.run(go())


def test_veil_logs_already_on_top_is_left_alone():
    async def go():
        guild, _ = creating_guild([text_channel(1, "message-logs")])
        logs, text = cat(50, "VEIL LOGS", 0), cat(40, "Text Channels", 1)
        guild.categories = [logs, text]
        cog, _ = make_cog()
        report = await cog.organize(guild)
        assert not report.raised and logs.edit.await_count == 0
    asyncio.run(go())


def everywhere_cog(guilds, global_cfg=None, authorized=lambda gid: True):
    cog, store = make_cog()
    store["global"] = dict(global_cfg or {})
    cog.bot.guilds = guilds
    cog.bot.is_authorized = authorized
    cog.bot.get_guild = lambda gid: next((g for g in guilds if g.id == gid), None)
    return cog, store


def test_setup_runs_in_every_server_it_may_work_in(monkeypatch):
    async def go():
        a, _ = creating_guild(gid=1)
        b, _ = creating_guild(gid=2)
        no_perms, _ = creating_guild(gid=3, manage_channels=False)
        unauthorized, _ = creating_guild(gid=4)
        cog, _ = everywhere_cog([a, b, no_perms, unauthorized], authorized=lambda gid: gid != 4)
        seen = []
        real = cog.organize

        async def spy(guild, kinds=None):
            seen.append(guild.id)
            return await real(guild, kinds)
        cog.organize = spy
        monkeypatch.setattr("core.devchannels.ensure", AsyncMock())
        totals = await cog.organize_everywhere(pause=0)
        assert seen == [1, 2]
        assert totals["servers"] == 2 and totals["skipped"] == 2 and totals["created"] == 18
    asyncio.run(go())


def test_automatic_setup_is_on_by_default_and_runs_once_per_start(monkeypatch):
    async def go():
        cog, _ = everywhere_cog([])
        assert await cog.auto_setup_enabled() is True
        cog.organize_everywhere = AsyncMock(return_value={"servers": 0, "created": 0, "moved": 0, "skipped": 0})
        await cog.on_ready()
        await cog.on_ready()  # a reconnect
        cog.organize_everywhere.assert_awaited_once()
    asyncio.run(go())


def test_automatic_setup_can_be_switched_off():
    async def go():
        cog, store = everywhere_cog([], {"auto_log_setup": False})
        cog.organize_everywhere = AsyncMock()
        await cog.on_ready()
        cog.organize_everywhere.assert_not_awaited()
    asyncio.run(go())


def test_a_new_server_is_set_up_when_the_bot_joins(monkeypatch):
    async def go():
        guild, _ = creating_guild(gid=7)
        cog, _ = everywhere_cog([guild])
        cog.organize = AsyncMock()
        monkeypatch.setattr(L.asyncio, "sleep", AsyncMock())
        await cog.on_guild_join(guild)
        cog.organize.assert_awaited_once_with(guild)
    asyncio.run(go())


def _setup_ctx(is_dev):
    ctx = MagicMock()
    ctx.approve = AsyncMock()
    ctx.typing = MagicMock(return_value=AsyncMock())
    ctx.author = SimpleNamespace(id=5)
    return ctx


def test_only_developers_switch_automatic_setup(monkeypatch):
    async def go():
        cog, store = everywhere_cog([])
        monkeypatch.setattr("core.perms.is_bot_developer", lambda user, bot: False)
        with pytest.raises(L.UserError):
            await cog._set_auto(_setup_ctx(False), "off")
        monkeypatch.setattr("core.perms.is_bot_developer", lambda user, bot: True)
        cog.organize_everywhere = AsyncMock(return_value={"servers": 3, "created": 4, "moved": 5, "skipped": 0})
        ctx = _setup_ctx(True)
        await cog._set_auto(ctx, "on")
        assert store["global"]["auto_log_setup"] is True
        assert "**3** server(s)" in ctx.approve.await_args.args[0]
        await cog._set_auto(ctx, "off")
        assert store["global"]["auto_log_setup"] is False
    asyncio.run(go())
