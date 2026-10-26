"""VoiceMaster: join-to-create temporary voice channels with a button interface."""
from __future__ import annotations

import discord
from discord.ext import commands

from core.utils import Colors, Context, UserError, has_perms, check_role, is_dangerous, antinuke_guard


def vm_embed(desc: str, color=Colors.main):
    return discord.Embed(description=desc, color=color)


class RenameModal(discord.ui.Modal, title="Rename your channel"):
    name = discord.ui.TextInput(label="New name", max_length=100)

    def __init__(self, channel: discord.VoiceChannel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, it: discord.Interaction):
        await self.channel.edit(name=str(self.name))
        await it.response.send_message(embed=vm_embed(f"✅ Renamed to **{self.name}**", Colors.success), ephemeral=True)


class LimitModal(discord.ui.Modal, title="Set user limit"):
    limit = discord.ui.TextInput(label="Limit (0 = unlimited)", max_length=2)

    def __init__(self, channel):
        super().__init__()
        self.channel = channel

    async def on_submit(self, it: discord.Interaction):
        try:
            n = max(0, min(99, int(str(self.limit))))
        except ValueError:
            return await it.response.send_message("Enter a number", ephemeral=True)
        await self.channel.edit(user_limit=n)
        await it.response.send_message(embed=vm_embed(f"✅ Limit set to **{n or '∞'}**", Colors.success), ephemeral=True)


class Interface(discord.ui.View):
    """Persistent control panel. Actions apply to the clicker's current voice channel."""

    def __init__(self, cog: "VoiceMaster"):
        super().__init__(timeout=None)
        self.cog = cog

    async def owned(self, it: discord.Interaction, need_owner=True) -> discord.VoiceChannel | None:
        vc = it.user.voice.channel if it.user.voice else None
        if not vc:
            await it.response.send_message(embed=vm_embed("⚠️ You're not in a voice channel", Colors.warn), ephemeral=True)
            return None
        owner = await self.cog.owner_of(vc.id)
        if owner is None:
            await it.response.send_message(embed=vm_embed("⚠️ That isn't a VoiceMaster channel", Colors.warn), ephemeral=True)
            return None
        if need_owner and owner != it.user.id:
            await it.response.send_message(embed=vm_embed("⚠️ You don't own this channel", Colors.warn), ephemeral=True)
            return None
        return vc

    async def done(self, it, text):
        await it.response.send_message(embed=vm_embed(f"✅ {text}", Colors.success), ephemeral=True)

    @discord.ui.button(emoji="🔒", custom_id="vm:lock", row=0)
    async def lock(self, it, _):
        if vc := await self.owned(it):
            await vc.set_permissions(it.guild.default_role, connect=False)
            await self.done(it, "Locked your channel")

    @discord.ui.button(emoji="🔓", custom_id="vm:unlock", row=0)
    async def unlock(self, it, _):
        if vc := await self.owned(it):
            await vc.set_permissions(it.guild.default_role, connect=None)
            await self.done(it, "Unlocked your channel")

    @discord.ui.button(emoji="👻", custom_id="vm:hide", row=0)
    async def hide(self, it, _):
        if vc := await self.owned(it):
            await vc.set_permissions(it.guild.default_role, view_channel=False)
            await self.done(it, "Hid your channel")

    @discord.ui.button(emoji="👁️", custom_id="vm:reveal", row=0)
    async def reveal(self, it, _):
        if vc := await self.owned(it):
            await vc.set_permissions(it.guild.default_role, view_channel=None)
            await self.done(it, "Revealed your channel")

    @discord.ui.button(emoji="✏️", custom_id="vm:rename", row=1)
    async def rename(self, it, _):
        if vc := await self.owned(it):
            await it.response.send_modal(RenameModal(vc))

    @discord.ui.button(emoji="👥", custom_id="vm:limit", row=1)
    async def limit(self, it, _):
        if vc := await self.owned(it):
            await it.response.send_modal(LimitModal(vc))

    @discord.ui.button(emoji="👑", custom_id="vm:claim", row=1)
    async def claim(self, it, _):
        vc = await self.owned(it, need_owner=False)
        if not vc:
            return
        owner_id = await self.cog.owner_of(vc.id)
        if owner_id == it.user.id:
            return await self.done(it, "You already own this channel")
        if any(m.id == owner_id for m in vc.members):
            return await it.response.send_message(embed=vm_embed("⚠️ The owner is still in the channel", Colors.warn), ephemeral=True)
        await self.cog.set_owner(vc, it.user)
        await self.done(it, "You now own this channel")

    @discord.ui.button(emoji="ℹ️", custom_id="vm:info", row=1)
    async def info(self, it, _):
        vc = await self.owned(it, need_owner=False)
        if not vc:
            return
        owner = await self.cog.owner_of(vc.id)
        locked = vc.overwrites_for(it.guild.default_role).connect is False
        e = discord.Embed(title=vc.name, color=Colors.main)
        e.add_field(name="Owner", value=f"<@{owner}>")
        e.add_field(name="Members", value=f"{len(vc.members)}/{vc.user_limit or '∞'}")
        e.add_field(name="Locked", value="Yes" if locked else "No")
        e.add_field(name="Bitrate", value=f"{vc.bitrate // 1000}kbps")
        await it.response.send_message(embed=e, ephemeral=True)

    @discord.ui.button(emoji="🗑️", custom_id="vm:delete", style=discord.ButtonStyle.danger, row=2)
    async def delete(self, it, _):
        if vc := await self.owned(it):
            await it.response.send_message(embed=vm_embed("🗑️ Deleting…"), ephemeral=True)
            await vc.delete(reason="VoiceMaster owner deleted")


class VoiceMaster(commands.Cog):
    """Temporary personal voice channels"""
    emoji = "🎙️"

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(Interface(self))

    async def owner_of(self, channel_id: int) -> int | None:
        row = await self.bot.db.fetchone("SELECT owner_id FROM vm_channels WHERE channel_id=?", channel_id)
        return row["owner_id"] if row else None

    async def set_owner(self, vc: discord.VoiceChannel, member: discord.Member):
        await self.bot.db.execute("UPDATE vm_channels SET owner_id=? WHERE channel_id=?", member.id, vc.id)
        await vc.set_permissions(member, connect=True, view_channel=True, manage_channels=True, move_members=True)

    # ------------------------------------------------------------ events
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if before.channel == after.channel:
            return
        cfg = await self.bot.db.get_config(member.guild.id, "voicemaster")
        if after.channel and after.channel.id == cfg.get("join"):
            await self.create_for(member, cfg)
        if before.channel and not before.channel.members and await self.owner_of(before.channel.id):
            try:
                await before.channel.delete(reason="VoiceMaster channel empty")
            except discord.HTTPException:
                pass
            await self.bot.db.execute("DELETE FROM vm_channels WHERE channel_id=?", before.channel.id)

    async def create_for(self, member: discord.Member, cfg: dict):
        guild = member.guild
        category = guild.get_channel(cfg.get("category", 0))
        prefs = cfg.get("users", {}).get(str(member.id), {})
        name = prefs.get("name") or cfg.get("name", "{user}'s channel").replace("{user}", member.display_name)
        overwrites = {member: discord.PermissionOverwrite(connect=True, view_channel=True, manage_channels=True,
                                                          move_members=True)}
        role = guild.get_role(cfg.get("role") or 0)
        try:
            vc = await guild.create_voice_channel(
                name[:100], category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites={**(category.overwrites if isinstance(category, discord.CategoryChannel) else {}), **overwrites},
                user_limit=prefs.get("limit", 0), bitrate=int(min(cfg.get("bitrate", 64000), guild.bitrate_limit)),
                reason=f"VoiceMaster for {member}")
        except discord.HTTPException:
            return
        await self.bot.db.execute("INSERT OR REPLACE INTO vm_channels VALUES(?,?,?)", vc.id, guild.id, member.id)
        try:
            await member.move_to(vc)
        except discord.HTTPException:
            # they left the join channel before we could move them — don't leave an empty, untracked channel behind
            try:
                await vc.delete(reason="VoiceMaster: owner left before being moved")
            except discord.HTTPException:
                pass
            await self.bot.db.execute("DELETE FROM vm_channels WHERE channel_id=?", vc.id)
            return
        if role:
            b_cfg = await self.bot.db.get_config(guild.id, "roleblacklist")
            if role.id not in b_cfg.get("roles", []):
                try:
                    await member.add_roles(role, reason="VoiceMaster role")
                except discord.HTTPException:
                    pass

    # ------------------------------------------------------------ setup
    @commands.group(aliases=["vm"], invoke_without_command=True)
    async def voicemaster(self, ctx: Context):
        """Set up and manage VoiceMaster"""
        await ctx.send_help(ctx.command)

    @voicemaster.command(name="setup")
    @has_perms(manage_guild=True)
    @commands.bot_has_permissions(manage_channels=True, move_members=True)
    async def vm_setup(self, ctx: Context):
        """Create the VoiceMaster category, join channel and interface"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "voicemaster")
        if ctx.guild.get_channel(cfg.get("join", 0)):
            raise UserError(f"VoiceMaster is already set up — use `{ctx.clean_prefix}vm reset` first")
        cat = await ctx.guild.create_category("Voice Channels", reason="VoiceMaster setup")
        iface = await ctx.guild.create_text_channel("interface", category=cat, overwrites={
            ctx.guild.default_role: discord.PermissionOverwrite(send_messages=False)})
        join = await ctx.guild.create_voice_channel("➕ Join to Create", category=cat)
        cfg.update(category=cat.id, join=join.id, interface=iface.id)
        await self.bot.db.set_config(ctx.guild.id, "voicemaster", cfg)
        await self.send_interface(iface)
        await ctx.approve(f"set up VoiceMaster · join {join.mention} to create a channel")

    async def send_interface(self, channel: discord.TextChannel):
        e = discord.Embed(title="VoiceMaster Interface", color=Colors.main,
                          description="Use the buttons below to control your voice channel.")
        e.add_field(name="Controls", value=(
            "🔒 lock • 🔓 unlock • 👻 hide • 👁️ reveal\n"
            "✏️ rename • 👥 limit • 👑 claim • ℹ️ info • 🗑️ delete"))
        if channel.guild.icon:
            e.set_thumbnail(url=channel.guild.icon.url)
        await channel.send(embed=e, view=Interface(self))

    @voicemaster.command(name="interface")
    @has_perms(manage_guild=True)
    async def vm_interface(self, ctx: Context, channel: discord.TextChannel = None):
        """Re-send the interface panel"""
        await self.send_interface(channel or ctx.channel)

    @voicemaster.command(name="reset")
    @has_perms(manage_guild=True)
    async def vm_reset(self, ctx: Context):
        """Delete the VoiceMaster setup"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "voicemaster")
        for key in ("join", "interface", "category"):
            ch = ctx.guild.get_channel(cfg.get(key, 0))
            if ch:
                try:
                    await ch.delete(reason="VoiceMaster reset")
                except discord.HTTPException:
                    pass
        await self.bot.db.set_config(ctx.guild.id, "voicemaster", {})
        await ctx.approve("reset VoiceMaster")

    @voicemaster.command(name="defaultname")
    @has_perms(manage_guild=True)
    async def vm_defaultname(self, ctx: Context, *, name: str):
        """Default channel name (`{user}` = owner's name)"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "voicemaster")
        cfg["name"] = name
        await self.bot.db.set_config(ctx.guild.id, "voicemaster", cfg)
        await ctx.approve(f"set the default channel name to `{name}`")

    @voicemaster.command(name="defaultrole")
    @has_perms(manage_guild=True)
    async def vm_defaultrole(self, ctx: Context, role: discord.Role = None):
        """Role given to members who create a channel"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "voicemaster")
        if role:
            check_role(ctx, role)
            b_cfg = await self.bot.db.get_config(ctx.guild.id, "roleblacklist")
            if role.id in b_cfg.get("roles", []):
                raise UserError(f"{role.mention} is blacklisted (bled) and cannot be assigned by anyone via any command")
            if is_dangerous(role.permissions):
                await antinuke_guard(ctx, "permissions")
        cfg["role"] = role.id if role else None
        await self.bot.db.set_config(ctx.guild.id, "voicemaster", cfg)
        await ctx.approve(f"set the default role to {role.mention}" if role else "removed the default role")

    @voicemaster.command(name="bitrate")
    @has_perms(manage_guild=True)
    async def vm_bitrate(self, ctx: Context, kbps: int):
        """Default bitrate for new channels"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "voicemaster")
        kbps = max(8, min(kbps, 384))
        if ctx.guild.bitrate_limit < kbps * 1000:
            kbps = int(ctx.guild.bitrate_limit // 1000)
        cfg["bitrate"] = kbps * 1000
        await self.bot.db.set_config(ctx.guild.id, "voicemaster", cfg)
        await ctx.approve(f"set the default bitrate to **{kbps}kbps**")

    # ------------------------------------------------------------ owner commands
    async def my_channel(self, ctx: Context) -> discord.VoiceChannel:
        vc = ctx.author.voice.channel if ctx.author.voice else None
        if not vc or await self.owner_of(vc.id) != ctx.author.id:
            raise UserError("You need to be in a VoiceMaster channel you own")
        return vc

    async def _save_pref(self, ctx, key, value):
        cfg = await self.bot.db.get_config(ctx.guild.id, "voicemaster")
        cfg.setdefault("users", {}).setdefault(str(ctx.author.id), {})[key] = value
        await self.bot.db.set_config(ctx.guild.id, "voicemaster", cfg)

    @commands.group(aliases=["voice"], invoke_without_command=True)
    async def vc(self, ctx: Context):
        """Control your VoiceMaster channel"""
        await ctx.send_help(ctx.command)

    @vc.command(name="lock")
    async def vc_lock(self, ctx: Context):
        """Lock your channel"""
        vc = await self.my_channel(ctx)
        await vc.set_permissions(ctx.guild.default_role, connect=False)
        await ctx.approve("locked your channel")

    @vc.command(name="unlock")
    async def vc_unlock(self, ctx: Context):
        """Unlock your channel"""
        vc = await self.my_channel(ctx)
        await vc.set_permissions(ctx.guild.default_role, connect=None)
        await ctx.approve("unlocked your channel")

    @vc.command(name="hide", aliases=["ghost"])
    async def vc_hide(self, ctx: Context):
        """Hide your channel"""
        vc = await self.my_channel(ctx)
        await vc.set_permissions(ctx.guild.default_role, view_channel=False)
        await ctx.approve("hid your channel")

    @vc.command(name="reveal", aliases=["unhide"])
    async def vc_reveal(self, ctx: Context):
        """Reveal your channel"""
        vc = await self.my_channel(ctx)
        await vc.set_permissions(ctx.guild.default_role, view_channel=None)
        await ctx.approve("revealed your channel")

    @vc.command(name="rename", aliases=["name"])
    async def vc_rename(self, ctx: Context, *, name: str):
        """Rename your channel (remembered for next time)"""
        vc = await self.my_channel(ctx)
        await vc.edit(name=name[:100])
        await self._save_pref(ctx, "name", name[:100])
        await ctx.approve(f"renamed your channel to **{discord.utils.escape_markdown(name)}**")

    @vc.command(name="limit")
    async def vc_limit(self, ctx: Context, limit: int):
        """Set a user limit (remembered)"""
        vc = await self.my_channel(ctx)
        limit = max(0, min(99, limit))
        await vc.edit(user_limit=limit)
        await self._save_pref(ctx, "limit", limit)
        await ctx.approve(f"set the limit to **{limit}**" if limit else "removed the limit")

    @vc.command(name="permit", aliases=["allow"])
    async def vc_permit(self, ctx: Context, target: discord.Member | discord.Role):
        """Let a member/role join even when locked"""
        vc = await self.my_channel(ctx)
        await vc.set_permissions(target, connect=True, view_channel=True)
        await ctx.approve(f"permitted {target.mention}")

    @vc.command(name="reject", aliases=["deny", "kick"])
    async def vc_reject(self, ctx: Context, member: discord.Member):
        """Block a member and kick them out"""
        vc = await self.my_channel(ctx)
        if member == ctx.author:
            raise UserError("You can't reject yourself")
        await vc.set_permissions(member, connect=False)
        if member in vc.members:
            await member.move_to(None)
        await ctx.approve(f"rejected {member.mention}")

    @vc.command(name="transfer")
    async def vc_transfer(self, ctx: Context, member: discord.Member):
        """Give ownership to someone in your channel"""
        vc = await self.my_channel(ctx)
        if member not in vc.members:
            raise UserError(f"{member.mention} isn't in your channel")
        await vc.set_permissions(ctx.author, overwrite=None)
        await self.set_owner(vc, member)
        await ctx.approve(f"transferred your channel to {member.mention}")

    @vc.command(name="claim")
    async def vc_claim(self, ctx: Context):
        """Claim a channel whose owner left"""
        vc = ctx.author.voice.channel if ctx.author.voice else None
        owner = await self.owner_of(vc.id) if vc else None
        if not owner:
            raise UserError("You're not in a VoiceMaster channel")
        if any(m.id == owner for m in vc.members):
            raise UserError("The owner is still in the channel")
        await self.set_owner(vc, ctx.author)
        await ctx.approve("you now own this channel")

    @vc.command(name="status")
    async def vc_status(self, ctx: Context, *, status: str = None):
        """Set your channel's voice status"""
        vc = await self.my_channel(ctx)
        await vc.edit(status=status)
        await ctx.approve("updated the channel status")


async def setup(bot):
    await bot.add_cog(VoiceMaster(bot))
