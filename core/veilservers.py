"""Private developer-only view of joined guilds whose names contain Veil."""
from __future__ import annotations

import asyncio
import logging
import time
import unicodedata
from dataclasses import dataclass

import aiohttp
import discord

from core.utils import Colors, OwnedView

log = logging.getLogger("veil.veilservers")
PER_PAGE = 6


@dataclass(frozen=True)
class Match:
    name: str
    guild_id: int | None
    owner_id: int | None
    members: int | None
    exact: bool


def _positive_id(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**64 else None


def _field(guild, name):
    try:
        return getattr(guild, name, None)
    except Exception:
        return None


def scan_joined(guilds) -> tuple[int, list[Match], int]:
    """Use a cache snapshot only; no network requests or discovery of non-joined guilds."""
    snapshot = list(guilds)
    found = []
    unavailable = 0
    for guild in snapshot:
        try:
            name = getattr(guild, "name", None)
            if not isinstance(name, str):
                unavailable += 1
                continue
            folded = name.casefold()
            if "veil" not in folded:
                continue
            count = _field(guild, "member_count")
            count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
            found.append(Match(name, _positive_id(_field(guild, "id")),
                               _positive_id(_field(guild, "owner_id")), count, folded == "veil"))
        except Exception:
            # An unavailable entry should not take down the entire scan. Do not log cross-server details.
            unavailable += 1
    found.sort(key=lambda item: (not item.exact, item.name.casefold(), item.guild_id or 0))
    return len(snapshot), found, unavailable


def _safe_name(name: str) -> str:
    # Remove line breaks, control characters and direction overrides before escaping markup and mentions.
    plain = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in name)[:100]
    return discord.utils.escape_markdown(plain.replace("@", "@\u200b")) or "Unnamed server"


def _display_id(value: int | None) -> str:
    return f"`{value}`" if value is not None else "Unavailable"


def result_pages(guilds) -> list[discord.Embed]:
    scanned, matches, unavailable = scan_joined(guilds)
    exact = sum(match.exact for match in matches)
    summary = (f"**Scanned:** {scanned:,} · **Matches:** {len(matches):,} "
               f"· **Exact:** {exact:,} · **Partial:** {len(matches) - exact:,}")
    if unavailable:
        summary += f"\n-# {unavailable:,} server(s) could not be inspected; counts may be incomplete."
    pages = []
    for start in range(0, max(1, len(matches)), PER_PAGE):
        lines = []
        for i, match in enumerate(matches[start:start + PER_PAGE], start + 1):
            members = f"{match.members:,}" if match.members is not None else "Unavailable"
            kind = "Exact" if match.exact else "Partial"
            lines.append(f"**{i}. {_safe_name(match.name)}** · {kind}\n"
                         f"Server ID: {_display_id(match.guild_id)} · Owner ID: {_display_id(match.owner_id)} "
                         f"· Members: {members}")
        description = summary + "\n\n" + ("\n\n".join(lines) if lines else "No matching joined servers found.")
        pages.append(discord.Embed(title="Veil servers", description=description, color=Colors.main))
    for i, page in enumerate(pages, 1):
        page.set_footer(text=f"Page {i}/{len(pages)} · Joined servers only · Private scan")
    return pages


async def developer_allowed(bot, user) -> bool:
    try:
        return bool(await bot.is_owner(user))
    except Exception as exc:
        log.warning("Developer access check failed (%s); access denied", type(exc).__name__)
        return False


class VeilServersView(OwnedView):
    """Only the invoking developer may navigate or refresh this DM-only scan."""

    def __init__(self, bot, author_id: int, pages: list[discord.Embed]):
        super().__init__(author_id, timeout=180)
        self.bot, self.pages, self.index = bot, pages, 0
        self.message = None
        self._last_refresh = 0.0
        self._sync()

    def _sync(self):
        self.previous.disabled = self.index == 0
        self.next_page.disabled = self.index >= len(self.pages) - 1
        self.indicator.label = f"{self.index + 1} / {len(self.pages)}"

    async def interaction_check(self, it: discord.Interaction) -> bool:
        if not await super().interaction_check(it):
            return False
        if it.guild is not None or not await developer_allowed(self.bot, it.user):
            await it.response.send_message("This private menu is no longer available.", ephemeral=True)
            return False
        return True

    async def _show(self, it):
        self._sync()
        await it.response.edit_message(embed=self.pages[self.index], view=self,
                                       allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, it: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        await self._show(it)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def indicator(self, it: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_page(self, it: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        await self._show(it)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh(self, it: discord.Interaction, button: discord.ui.Button):
        current = time.monotonic()
        if self._last_refresh and current - self._last_refresh < 3:
            return await it.response.send_message("Please wait a moment before refreshing again.", ephemeral=True)
        self._last_refresh = current
        try:
            self.pages = result_pages(self.bot.guilds)
        except Exception as exc:
            log.warning("Veil scan failed (%s)", type(exc).__name__)
            return await it.response.send_message("The scan could not be refreshed. Try again later.", ephemeral=True)
        self.index = 0
        await self._show(it)

    async def on_error(self, it: discord.Interaction, error: Exception, item: discord.ui.Item):
        log.warning("Veil interaction failed (%s)", type(error).__name__)
        if not it.response.is_done():
            await it.response.send_message("This private menu could not be updated.", ephemeral=True)

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(view=None)
            except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
                log.warning("Could not remove expired Veil scan controls")
