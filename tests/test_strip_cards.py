"""The strip cards: a two-column audit layout, an honest sentence, and nothing a reader can't act on."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from core import logembeds as fmt
from core.utils import Colors


def role(rid: int, name: str = "Role"):
    return SimpleNamespace(id=rid, name=name, mention=f"<@&{rid}>")


class FakeRole:
    """A role as `,strip` sees it: the bot keeps them below its own top role with `<`, so they need an order."""

    def __init__(self, rid: int, name: str = "Role", position: int = 1, permissions=None):
        self.id, self.name, self.mention = rid, name, f"<@&{rid}>"
        self.position, self.managed = position, False
        self.permissions = permissions or discord.Permissions.none()

    def is_default(self) -> bool:
        return self.id == 1

    def __lt__(self, other) -> bool:
        return self.position < other.position

    def __ge__(self, other) -> bool:
        return self.position >= other.position


def guild_role(rid: int, name: str = "Role", position: int = 1, **perms):
    return FakeRole(rid, name, position, discord.Permissions(**perms))


class Obj(SimpleNamespace):
    """Hashable and equal to itself only, like Discord's objects (a `roles` list is compared with `==`)."""

    __hash__ = object.__hash__

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


def member(uid: int = 7, name: str = "ham"):
    return Obj(id=uid, name=name, display_name=name, mention=f"<@{uid}>", bot=False, nick=None,
               display_avatar=SimpleNamespace(url="https://cdn.example/a.png"), roles=[], timed_out_until=None)


def fields(embed) -> list[tuple[str, str, bool]]:
    return [(f.name, f.value, f.inline) for f in embed.fields]


def test_field_card_lays_out_a_row_and_leaves_empty_fields_out():
    card = fmt.field_card(Colors.orange, "Member Stripped", subject=member(), fields=[
        ("User", fmt.with_id(member()), True), ("By", fmt.with_id(member(9, "mod")), True),
        ("Removed", "", False), ("Added", "", False),
        ("Roles removed", "<@&1>, <@&2>", False),
    ], ids=[("User ID", 7)])
    assert card.title == "Member Stripped" and card.author.name == "ham"
    assert fields(card) == [
        ("User", "<@7> (`7`)", True),
        ("By", "<@9> (`9`)", True),
        ("Roles removed", "<@&1>, <@&2>", False),
    ]
    assert card.footer.text == "User ID: 7" and card.timestamp is not None
    assert card.description is None


def test_field_card_starts_a_new_row_instead_of_squeezing_fields():
    long = "x" * 60
    card = fmt.field_card(Colors.orange, "t", fields=[("A", "short", True), ("B", long, True), ("C", "short", True)])
    assert [f[2] for f in fields(card)] == [True, True, True]
    # a narrow row is only kept when both fields still get room; the wide one takes its own row
    assert [len(v) for _, v, _ in fields(card)] == [5, 60, 5]


def test_with_id_keeps_the_id_when_the_account_is_gone():
    assert fmt.with_id(None) == ""
    assert fmt.with_id(member(5)) == "<@5> (`5`)"


def test_member_stripped_shows_the_two_columns_and_an_undo_hint():
    removed = [role(i, f"role-{i:02d}") for i in range(1, 9)]
    card = fmt.member_stripped(member(), removed, member(9, "mod"), hint="a developer can put them back")
    assert card.color.value == Colors.orange
    assert fields(card) == [
        ("User", "<@7> (`7`)", True),
        ("By", "<@9> (`9`)", True),
        ("Roles removed", ", ".join(f"<@&{i}>" for i in range(1, 9)), False),
    ]
    assert card.description == "-# How to undo · a developer can put them back"
    assert card.footer.text == "User ID: 7"


@pytest.mark.parametrize("removed", [[], [role(1)]])
def test_member_stripped_falls_back_to_plain_advice(removed):
    card = fmt.member_stripped(member(), removed, member(9))
    assert "Give the role(s) above back to undo this." in card.description


def test_member_stripped_without_an_actor_or_roles_is_still_valid():
    card = fmt.member_stripped(member(), [], None)
    assert [f[0] for f in fields(card)] == ["User"]
    assert card.footer.text == "User ID: 7"
    assert len(card) <= 6000


def test_a_long_role_list_is_never_cut_in_the_middle_of_a_mention():
    card = fmt.member_stripped(member(), [role(100 + i) for i in range(150)], member(9))
    value = card.fields[-1].value
    assert len(value) <= 1024 and value.endswith(" *+41 more*")
    assert "<@&" in value and value.rsplit(", ", 1)[-1].startswith("*+")


def test_roles_updated_is_the_same_shape_for_an_ordinary_edit():
    card = fmt.roles_updated(member(), [role(2, "Mod")], [], actor=member(9, "mod"), reason="cleanup")
    assert card.title == "Roles Updated" and card.color.value == Colors.success
    assert card.description is None
    assert fields(card) == [
        ("User", "<@7> (`7`)", True),
        ("Changed By", "<@9> (`9`)", True),
        ("Added (1)", "<@&2>", False),
        ("Reason", "cleanup", False),
    ]


def test_roles_updated_calls_a_mass_removal_a_strip_and_says_what_is_left():
    roles = [role(i) for i in range(1, 9)]
    card = fmt.roles_updated(member(), [], roles, actor=member(9), total=9)
    assert card.title == "Member Stripped" and card.color.value == Colors.orange
    assert card.description == "Stripped **8 roles** from <@7>, leaving 1 role."
    every = fmt.roles_updated(member(), [], roles, actor=member(9), total=8)
    assert every.description == "Stripped **8 roles** from <@7>, every role they had."


def test_two_roles_going_out_of_many_is_not_called_a_strip():
    card = fmt.roles_updated(member(), [], [role(1), role(2)], actor=member(9), total=12)
    assert card.title == "Roles Updated" and card.description is None
    assert fields(card) == [
        ("User", "<@7> (`7`)", True), ("Changed By", "<@9> (`9`)", True),
        ("Removed (2)", "<@&1>, <@&2>", False),
    ]
    # a handful of roles is fine to call a strip; one or two is just an edit
    small = fmt.roles_updated(member(), [], [role(1), role(2)], actor=member(9), total=2)
    assert small.title == "Roles Updated" and small.description is None
    three = fmt.roles_updated(member(), [], [role(1), role(2), role(3)], actor=member(9), total=3)
    assert three.title == "Member Stripped"
    assert three.description == "Stripped **3 roles** from <@7>, every role they had."


def test_the_old_sentence_card_is_untouched():
    """`roles_changed` still serves callers that never pass a total."""
    card = fmt.roles_changed(member(), [], [role(3, "VIP")])
    assert card.description.splitlines()[0] == "**<@7> had roles taken**"


# ------------------------------------------------------------------ the ,strip command itself

def strip_ctx():
    ctx = MagicMock()
    ctx.guild = SimpleNamespace(id=1, owner_id=2, roles=[guild_role(1, "@everyone")],
                                me=SimpleNamespace(top_role=FakeRole(99, "bot", 50)))
    ctx.clean_prefix = ","
    ctx.send = AsyncMock()
    ctx.author = member(2, "simisear")
    return ctx


def test_strip_posts_the_card_and_saves_the_roles():
    from cogs.devtools import DevTools
    from core.bot import Veil

    async def run():
        b = Veil()
        store = {}
        b.db.get_config = AsyncMock(side_effect=lambda gid, mod, default=None: store.setdefault(mod, {}))
        b.db.set_config = AsyncMock(side_effect=lambda gid, mod, data: store.__setitem__(mod, data))
        ctx = strip_ctx()
        target = member(5, "ham")
        target.roles = [guild_role(1, "@everyone", 0), guild_role(10, "Staff", 1), guild_role(11, "Mod", 2)]
        target.remove_roles = AsyncMock()
        cog = DevTools(b)
        await DevTools.strip.callback(cog, ctx, target)
        assert store["strips"] == {"5": [10, 11]}
        target.remove_roles.assert_awaited_once()
        card = ctx.send.await_args.kwargs["embed"]
        assert card.title == "Member Stripped" and card.author.name == "ham"
        assert ",unstrip" in card.description

    asyncio.run(run())


# ------------------------------------------------------------------ the log card for a role-edit burst

def test_the_role_log_counts_what_the_member_had_before_the_burst(monkeypatch):
    """Discord sends one update per role, so a strip arrives as a burst: the card must count from before it."""
    from cogs.logging import Logging

    async def run():
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        store = {"roles": 1}

        async def get_config(gid, module, default=None):
            return store.setdefault(module, {})

        bot = SimpleNamespace(db=SimpleNamespace(get_config=AsyncMock(side_effect=get_config),
                                                 set_config=AsyncMock()),
                              add_dynamic_items=MagicMock(), remove_dynamic_items=MagicMock(),
                              is_owner=AsyncMock(return_value=False))
        channel = MagicMock(spec=discord.TextChannel)
        channel.id, channel.name, channel.mention = 1, "role-logs", "<#1>"
        channel.send, channel.permissions_for = AsyncMock(), MagicMock(
            return_value=SimpleNamespace(view_channel=True, send_messages=True, embed_links=True))
        channel.category_id, channel.topic = None, None
        guild = SimpleNamespace(id=10, me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_channels=True,
                                                                                           view_audit_log=False)),
                                default_role=SimpleNamespace(id=1), text_channels=[channel], categories=[],
                                icon=None, owner_id=100, member_count=50,
                                get_channel=lambda cid: channel if cid == 1 else None, get_member=lambda uid: None)
        channel.guild = guild
        cog = Logging(bot)
        everyone = guild_role(1, "@everyone", 0)
        staff = [guild_role(10 + i, f"Staff {i}", 1 + i) for i in range(8)]
        target = member()
        target.guild = guild
        start = SimpleNamespace(**{**vars(target), "roles": [everyone, *staff]})
        step = SimpleNamespace(**{**vars(target), "roles": [everyone, *staff[4:]]})
        last = SimpleNamespace(**{**vars(target), "roles": [everyone]})
        await cog.on_member_update(start, step)
        await cog.on_member_update(step, last)
        await asyncio.gather(*list(cog._role_update_tasks.values()), return_exceptions=True)
        assert channel.send.await_count == 1
        embed = channel.send.await_args.kwargs["embed"]
        assert embed.title == "Member Stripped"
        removed = next(f for f in embed.fields if f.name.startswith("Removed"))
        assert removed.name == "Removed (8)" and removed.value.count("<@&") == 8
        assert embed.description == "Stripped **8 roles** from <@7>, leaving 1 role."

    asyncio.run(run())


def test_stripstaff_logs_the_member_stripped_card(monkeypatch):
    from cogs import moderation as moderation_module
    from cogs.moderation import Moderation
    from core.bot import Veil

    async def run():
        monkeypatch.setattr(moderation_module, "check_hierarchy_async", AsyncMock())
        b = Veil()
        b.db.get_config = AsyncMock(return_value={})
        b.db.fetchone = AsyncMock(return_value={"n": 4})
        b.db.execute = AsyncMock()
        posted = []
        logs = SimpleNamespace(route=AsyncMock(return_value=SimpleNamespace(post=AsyncMock())),
                               post=AsyncMock(side_effect=lambda channel, embed: posted.append(embed)))
        b.get_cog = MagicMock(return_value=logs)
        admin, target = member(2, "owner"), member(5, "ham")
        target.top_role = FakeRole(20, "Staff", 5)
        target.roles = [guild_role(10, "Staff", 6, manage_roles=True),
                        guild_role(11, "Mod", 7, kick_members=True)]
        target.remove_roles = AsyncMock()
        ctx = MagicMock()
        ctx.guild = SimpleNamespace(id=1, owner_id=2, me=SimpleNamespace(top_role=FakeRole(99, "bot", 50)),
                                    get_channel=MagicMock(return_value=None))
        ctx.author, ctx.approve = admin, AsyncMock()
        await Moderation.stripstaff.callback(Moderation(b), ctx, target)
        assert logs.route.await_args.args == (ctx.guild, "bans", "members")
        assert len(posted) == 1 and posted[0].title == "Member Stripped"
        assert posted[0].author.name == "ham" and posted[0].author.icon_url == target.display_avatar.url
        assert [f.name for f in posted[0].fields] == ["User", "By", "Roles removed"]
        assert posted[0].fields[-1].value == "<@&10>, <@&11>"
        target.remove_roles.assert_awaited_once()
        ctx.approve.assert_awaited_once()  # the ephemeral one-liner is still the command's own reply

    asyncio.run(run())


def test_stripstaff_still_works_when_logging_is_unavailable(monkeypatch):
    from cogs import moderation as moderation_module
    from cogs.moderation import Moderation
    from core.bot import Veil

    async def run():
        monkeypatch.setattr(moderation_module, "check_hierarchy_async", AsyncMock())
        b = Veil()
        b.db.get_config = AsyncMock(return_value={})
        b.db.fetchone = AsyncMock(return_value={"n": 1})
        b.db.execute = AsyncMock()
        b.get_cog = MagicMock(return_value=None)  # no Logging cog loaded at all
        target = member(5, "ham")
        target.top_role = FakeRole(20, "Staff", 5)
        target.roles = [guild_role(10, "Staff", 6, manage_roles=True)]
        target.remove_roles = AsyncMock()
        ctx = MagicMock()
        ctx.guild = SimpleNamespace(id=1, owner_id=2, me=SimpleNamespace(top_role=FakeRole(99, "bot", 50)),
                                    get_channel=MagicMock(return_value=None))
        ctx.author, ctx.approve = member(2, "owner"), AsyncMock()
        await Moderation.stripstaff.callback(Moderation(b), ctx, target)
        target.remove_roles.assert_awaited_once()
        ctx.approve.assert_awaited_once()

    asyncio.run(run())
