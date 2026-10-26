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


# ------------------------------------------------------------------ the embeds

def fields(e: discord.Embed) -> dict[str, str]:
    return {f.name: f.value for f in e.fields}


def test_every_log_is_a_titled_card_with_the_person_their_ids_and_one_timestamp():
    deleted = fmt.message_deleted(message(content="line one\nline two", attachments=[
        SimpleNamespace(filename="cat.png", url="https://cdn/cat.png")]))
    assert deleted.color.value == Colors.error and deleted.title == "Message Deleted — ham"
    assert deleted.thumbnail.url == "https://cdn.example/a.png"
    f = fields(deleted)
    assert f["Author"] == "<@7>\n`ham` · `7`" and f["Channel"].startswith("<#30>")
    assert "> line one\n> line two" in deleted.description
    assert "📎 [cat.png](https://cdn/cat.png)" in f["Attachments (1)"]
    assert deleted.footer.text == "User ID: 7 · Message ID: 500"
    assert deleted.timestamp is not None and "Today at" not in deleted.footer.text  # one time, shown by Discord


def test_person_names_the_user_marks_bots_and_admits_when_the_actor_is_unknown():
    assert fmt.person(member(5, "bad")) == "<@5>\n`bad` · `5`"
    assert fmt.person(member(5, "bot", bot=True)).startswith("<@5> 🤖")
    assert fmt.person(None).startswith("*Unknown*")


def test_deleted_by_a_moderator_says_so_but_self_deletes_do_not():
    mod = member(9, "mod")
    assert fields(fmt.message_deleted(message(), actor=mod))["Deleted By"].startswith("<@9>")
    assert "Deleted By" not in fields(fmt.message_deleted(message(), actor=message().author))
    assert "Deleted By" not in fields(fmt.message_deleted(message()))


def test_edits_show_before_and_after_with_a_jump_link():
    e = fmt.message_edited(message(content="old text"), message(content="new text"))
    assert e.color.value == Colors.warn and e.title == "Message Edited — ham"
    f = fields(e)
    assert "[Jump to message](https://discord.com/channels/10/30/500)" in f["Channel"]
    assert (f["Before"], f["After"]) == ("> old text", "> new text")


def test_long_content_is_cut_to_fit_discords_limits():
    e = fmt.message_edited(message(content="x" * 5000), message(content="y\n" * 3000))
    assert len(e) <= 6000 and all(len(f.value) <= 1024 for f in e.fields)
    big = fmt.message_deleted(message(content="z" * 9000))
    assert len(big.description) <= 4096


def test_lists_are_cut_with_a_count_instead_of_overflowing():
    lines = [f"• line number {i} with some padding to make it long enough" for i in range(60)]
    out = fmt.bullets(lines)
    assert len(out) <= 1024 and out.splitlines()[-1].startswith("*+") and out.endswith("more*")
    assert fmt.bullets(["a", "b"]) == "a\nb"


def test_join_flags_brand_new_accounts_and_shows_age_and_member_number():
    fresh = fmt.member_joined(member(created_days=2))
    old = fmt.member_joined(member(created_days=900))
    assert "⚠️ New account" in fields(fresh)["Account Created"] and "⚠️" not in fields(old)["Account Created"]
    assert fields(old)["Account Age"].startswith("900 days") and fields(old)["Member"] == "#50"
    assert old.footer.text == "User ID: 7" and old.color.value == Colors.success and old.title == "Member Joined — ham"
    assert "Type" in fields(fmt.member_joined(member(bot=True)))


def test_leave_and_kick_are_different_cards():
    m = member(roles=[role(1, "everyone", default=True), role(2, "Mod"), role(3, "VIP")])
    left = fmt.member_left(m)
    assert left.title == "Member Left — ham" and left.color.value == Colors.error
    assert fields(left)["Roles (2)"] == "<@&3>, <@&2>"          # highest role first
    assert fields(left)["Time in Server"].startswith("3 days") and "Kicked By" not in fields(left)
    assert left.footer.text == "User ID: 7 · Members: 50"
    kicked = fmt.member_left(m, member(9, "mod"), "spam")
    assert kicked.title == "Member Kicked — ham" and kicked.color.value == Colors.warn
    assert fields(kicked)["Kicked By"].startswith("<@9>") and fields(kicked)["Reason"] == "> spam"


def test_ban_and_unban_cards_name_the_moderator_and_mark_bots():
    user = member(5, "bad")
    banned = fmt.user_banned(user, member(9, "mod"), "raid")
    f = fields(banned)
    assert banned.title == "Member Banned — bad" and f["Moderator"].startswith("<@9>") and f["Reason"] == "> raid"
    assert "Account Created" in f and "Joined" in f
    assert "🤖" in fields(fmt.user_banned(user, member(9, "bot", bot=True)))["Moderator"]
    assert fields(fmt.user_banned(user))["Moderator"].startswith("*Unknown*")
    assert fields(fmt.user_banned(user))["Reason"] == "*No reason given*"
    assert fmt.user_unbanned(user).title == "Member Unbanned — bad"


def test_nickname_card_shows_both_names_and_who_changed_it():
    e = fmt.nickname_changed(member(), "old", None, member(9, "mod"))
    f = fields(e)
    assert f["Before"] == "`old`" and f["After"].startswith("*none") and f["Changed By"].startswith("<@9>")
    assert fields(fmt.nickname_changed(member(), None, "new", member()))["Changed By"] == "Themselves"
    assert fields(fmt.nickname_changed(member(), None, "new"))["Changed By"] == "Themselves"


def test_role_change_cards_pick_a_colour_and_list_each_side_with_ids():
    m, added, removed = member(), [role(2, "Mod")], [role(3, "VIP")]
    assert fmt.roles_changed(m, added, []).color.value == Colors.success
    assert fmt.roles_changed(m, [], removed).color.value == Colors.error
    mixed = fmt.roles_changed(m, added, removed, member(9, "mod"), "cleanup")
    f = fields(mixed)
    assert mixed.color.value == Colors.blurple and mixed.title == "Roles Updated — ham"
    assert f["➕ Added (1)"] == "• <@&2> · `Mod` · `2`" and f["➖ Removed (1)"] == "• <@&3> · `VIP` · `3`"
    assert f["Responsible Actor"].startswith("<@9>") and f["Reason"] == "> cleanup"
    assert {"Joined", "Account Created", "Roles"} <= set(f)
    assert mixed.footer.text == "User ID: 7 · Roles now: 0"
    assert fields(fmt.roles_changed(m, added, []))["Responsible Actor"].startswith("*Unknown*")


def test_handing_out_a_dangerous_role_is_flagged():
    admin = role(2, "Boss")
    admin.permissions = discord.Permissions(administrator=True)
    mgr = role(3, "Mgr")
    mgr.permissions = discord.Permissions(manage_roles=True, kick_members=True)
    f = fields(fmt.roles_changed(member(), [admin, mgr, role(4, "Plain")], []))
    lines = f["➕ Added (3)"].splitlines()
    assert "⚠️ `administrator`" in lines[0] and "`manage roles`, `kick members`" in lines[1] and "⚠️" not in lines[2]
    assert fmt.dangerous(admin) == ["administrator"] and fmt.dangerous(role(9)) == []


def test_timeout_cards():
    until = discord.utils.utcnow() + timedelta(hours=1, minutes=5)
    e = fmt.timed_out(member(), until, member(9, "mod"), "calm down")
    f = fields(e)
    assert e.title == "Member Timed Out — ham" and f["Moderator"].startswith("<@9>") and f["Reason"] == "> calm down"
    assert f["Duration"].startswith("1 hour") and "Until" in f
    cleared = fmt.timeout_removed(member(), member(9, "mod"))
    assert cleared.title == "Timeout Removed — ham" and fields(cleared)["Removed By"].startswith("<@9>")


def test_role_diffs_only_list_what_changed():
    a, b = role(1, "Old"), role(1, "New", color=0xFF0000)
    b.permissions = discord.Permissions(ban_members=True)
    changes = fmt.role_changes(a, b)
    assert "Name: `Old` → `New`" in changes and "Colour: `default` → `#FF0000`" in changes
    assert "Permissions added: `ban members`" in changes
    assert fmt.role_changes(a, a) == []
    assert "Permissions removed" in fmt.role_changes(b, a)[-1]


def test_role_cards_show_who_did_it_the_facts_and_the_danger():
    r = role(2, "VIP", color=0x00FF00)
    r.permissions = discord.Permissions(administrator=True)
    created = fmt.role_created(r, member(9, "mod"))
    f = fields(created)
    assert created.color.value == 0x00FF00 and created.title == "Role Created — VIP"
    assert f["Role"] == "<@&2>\n`VIP` · `2`" and f["Created By"].startswith("<@9>") and f["Colour"] == "`#00FF00`"
    assert "administrator" in f["⚠️ Dangerous permissions"] and created.footer.text == "Role ID: 2"
    deleted = fmt.role_deleted(role(2, "VIP"))
    assert deleted.title == "Role Deleted — VIP" and fields(deleted)["Role"] == "`@VIP` · `2`"
    updated = fmt.role_updated(r, ["Name: `a` → `b`"], member(9, "mod"))
    assert fields(updated)["Changes"] == "• Name: `a` → `b`" and fields(updated)["Updated By"].startswith("<@9>")


def test_channel_cards_name_the_kind_category_and_who_did_it():
    cat = SimpleNamespace(name="Staff")
    text = MagicMock(spec=discord.TextChannel, id=4, mention="<#4>", category=cat, guild=SimpleNamespace(icon=None),
                      topic="staff only", nsfw=False, slowmode_delay=5, position=3)
    text.name = "chat"
    voice = MagicMock(spec=discord.VoiceChannel, id=5, mention="<#5>", category=None, guild=SimpleNamespace(icon=None),
                      position=1)
    voice.name = "Lounge"
    created = fmt.channel_created(text, member(9, "mod"))
    f = fields(created)
    assert created.title == "Text channel Created — chat" and f["Channel"] == "<#4>\n`#chat` · `4`"
    assert f["Category"] == "**Staff**" and f["Topic"] == "> staff only" and f["Slowmode"] == "5s"
    assert f["Created By"].startswith("<@9>")
    deleted = fmt.channel_deleted(voice)
    assert deleted.title == "Voice channel Deleted — Lounge" and fields(deleted)["Category"] == "*none*"


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
    assert card.footer.text == "Server ID: 1" and fields(card)["Updated By"].startswith("<@9>")


def test_invite_and_emoji_cards():
    inv = SimpleNamespace(code="abc", channel=SimpleNamespace(mention="<#1>"), inviter=member(9, "mod"), max_uses=5,
                          max_age=7200, guild=SimpleNamespace(icon=None), uses=3, temporary=False)
    e = fmt.invite_created(inv)
    f = fields(e)
    assert "https://discord.gg/abc" in f["Invite"] and f["Created By"].startswith("<@9>")
    assert f["Max Uses"] == "5 uses" and f["Expires"] == "in 2h" and f["Channel"] == "<#1>"
    gone = fmt.invite_deleted(inv)
    assert gone.title == "Invite Deleted" and fields(gone)["Times Used"] == "3"
    assert fields(fmt.invite_created(SimpleNamespace(**{**inv.__dict__, "max_uses": 0, "max_age": 0})))["Expires"] == "never"
    emoji = SimpleNamespace(name="a", __str__=lambda s: "<:a:1>")
    emoji_card = fmt.emojis_changed(SimpleNamespace(id=1, icon=None), [emoji], [SimpleNamespace(name="gone")])
    assert "`:gone:`" in fields(emoji_card)["➖ Removed (1)"] and "`:a:`" in fields(emoji_card)["➕ Added (1)"]


def test_voice_cards():
    ch1 = SimpleNamespace(id=1, mention="<#1>", name="Lounge", members=[1, 2])
    ch2 = SimpleNamespace(id=2, mention="<#2>", name="Gaming")
    state = lambda ch, mute=False, deaf=False: SimpleNamespace(channel=ch, mute=mute, deaf=deaf)  # noqa: E731
    m = member()
    joined = fmt.voice_changed(m, state(None), state(ch1))
    assert joined.title == "Joined Voice — ham" and "<#1>" in fields(joined)["Channel"]
    assert "2 in the channel" in fields(joined)["Channel"]
    assert fmt.voice_changed(m, state(ch1), state(None)).title == "Left Voice — ham"
    moved = fmt.voice_changed(m, state(ch1), state(ch2))
    assert moved.title == "Moved Voice — ham" and "<#1>" in fields(moved)["From"] and "<#2>" in fields(moved)["To"]
    assert fmt.voice_changed(m, state(ch1), state(ch1, mute=True)).title == "Server Muted — ham"
    assert fmt.voice_changed(m, state(ch1), state(ch1)) is None


def cmd_ctx(content=",ban @x spam", name="ban", invoked="ban"):
    return SimpleNamespace(message=SimpleNamespace(content=content, id=900, jump_url="https://discord.com/channels/10/5/900"),
                           channel=SimpleNamespace(mention="<#5>"), author=member(),
                           command=SimpleNamespace(name=name, qualified_name=name, aliases=["b"], cog_name="Moderation"), invoked_with=invoked)


def test_command_card_shows_who_where_the_authority_and_how_it_went():
    e = fmt.command_used(cmd_ctx(), "Admin", "Owner", rank=3, ms=84.4)
    f = fields(e)
    assert e.title == "Command Used" and e.color.value == Colors.blurple
    assert e.author.name == "ham" and e.author.icon_url == "https://cdn.example/a.png"
    assert e.description == "`,ban @x spam`" and f["User"] == "<@7>"
    assert f["Channel"] == "<#5> · [jump](https://discord.com/channels/10/5/900)"
    assert f["Permission"] == "Admin\n-# via Owner"
    assert f["Result"] == "✅ Completed · 84 ms" and f["Module"] == "Moderation"
    assert "Alias used" not in f and "Error" not in f and "Took" not in f
    assert e.thumbnail.url is None  # no thumbnail: the three columns get the full width and don't wrap
    assert e.footer.text == "User ID: 7 · Message ID: 900"


def test_command_card_hides_internal_numbers():
    f = fields(fmt.command_used(cmd_ctx(), "Bot Developer", "Built-in", rank=10000, ms=721))
    assert f["Permission"] == "Bot Developer"
    assert "10000" not in str(f) and "Built-in" not in str(f)
    assert fields(fmt.command_used(cmd_ctx(), "None", "-"))["Permission"] == "Everyone"


def test_command_card_handles_long_commands_aliases_and_failures():
    long = cmd_ctx("a\nb")
    assert fmt.command_used(long, "None", "-").description == "```a\nb```"
    aliased = cmd_ctx(",b @x", invoked="b")
    assert fields(fmt.command_used(aliased, "None", "-"))["Alias used"] == "`b`"
    failed = fmt.command_used(cmd_ctx(), "None", "-", error="You need the Admin permission level")
    f = fields(failed)
    assert failed.title == "Command Failed" and failed.color.value == Colors.error
    assert f["Result"] == "❌ Failed" and f["Error"] == "> You need the Admin permission level"


def test_bulk_delete_transcript_is_oldest_first():
    first, second = message(content="one", mid=1), message(content="two", mid=2)
    lines = fmt.transcript([second, first]).splitlines()
    assert lines[0].endswith("ham: one") and lines[1].endswith("ham: two")
    card = fmt.bulk_deleted([first, second])
    f = fields(card)
    assert card.title == "Messages Bulk Deleted" and f["Messages"] == "2" and f["People"] == "1"
    assert f["Channel"].startswith("<#30>") and "<@7> · 2" in f["Authors"]


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
        assert embed.title == "Message Deleted — ham"

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
        assert banned.title == "Member Banned — bad" and fields(banned)["Moderator"].startswith("<@9>")
        assert fields(banned)["Reason"] == "> raid"
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
        assert mod.send.await_args.kwargs["embed"].title == "Member Kicked — gone"
        left.send.assert_not_awaited()

        guild2 = audit_guild([])
        guild2.text_channels, guild2.get_channel = [mod, left], {1: mod, 2: left}.get
        m2 = member(6, "left")
        m2.guild = guild2
        await cog.on_member_remove(m2)
        assert left.send.await_args.kwargs["embed"].title == "Member Left — left"
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
        added = fields(embed)["➕ Added (3)"]
        assert embed.title == "Roles Added — ham" and "<@&2>" in added and "<@&3>" in added and "<@&4>" in added
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
        card = channel.send.await_args.kwargs["embed"]
        assert card.description == "`,ping`" and fields(card)["Permission"] == "Server Owner"
        assert fields(card)["Result"].startswith("✅ Completed")
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


def test_the_command_card_says_how_long_it_took():
    async def go():
        channel, cog, ctx = _command_setup()
        await cog.on_command(ctx)
        await cog.on_command_completion(ctx)
        assert fields(channel.send.await_args.kwargs["embed"])["Result"].endswith(" ms")
    asyncio.run(go())


def test_failed_commands_get_a_card_with_the_reason():
    async def go():
        channel, cog, ctx = _command_setup()
        await cog.on_command(ctx)
        await cog.on_command_error(ctx, UserError("that member is above you"))
        card = channel.send.await_args.kwargs["embed"]
        assert card.title == "Command Failed" and card.author.name.endswith("owner") and card.color.value == Colors.error
        assert fields(card)["Error"] == "> that member is above you" and fields(card)["Result"].startswith("❌ Failed")
        crash = commands.CommandInvokeError(ValueError("boom"))
        await cog.on_command_error(ctx, crash)
        assert fields(channel.send.await_args.kwargs["embed"])["Error"] == "> ValueError: boom"
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
        assert "Only Bot Developers" in it.response.send_message.await_args.args[0]
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
        assert "unbanned **target**" in it.followup.send.await_args.args[0]

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
        assert "not currently banned" in it.response.send_message.await_args.args[0]
        it.response.edit_message.assert_not_awaited()
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


def test_case_log_uses_the_detailed_card():
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
        f = fields(embed)
        assert embed.title == "Case #12 — Ban" and f["User"] == "<@5>\n`bad` · `5`"
        assert f["Moderator"] == "<@9>\n`mod` · `9`" and f["Action"] == "Ban" and f["Reason"] == "> raid"
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
