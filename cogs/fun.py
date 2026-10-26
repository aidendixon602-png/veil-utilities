"""Fun: games, interactions, randomizers, uwulock."""
from __future__ import annotations

import hashlib
import random
import re

import discord
from discord.ext import commands

from core.utils import Colors, Context, UserError, has_perms, requires_uperm

ACTIONS = {
    "hug": "hugs", "kiss": "kisses", "pat": "pats", "slap": "slaps", "cuddle": "cuddles", "poke": "pokes",
    "bite": "bites", "highfive": "high-fives", "wave": "waves at", "punch": "punches", "kick": "kicks",
    "tickle": "tickles", "feed": "feeds", "handhold": "holds hands with", "yeet": "yeets", "peck": "pecks",
}
NEKOS: dict[str, str] = {}  # map command name -> nekos.best endpoint when they differ


def uwuify(text: str) -> str:
    text = re.sub(r"[rl]", "w", text)
    text = re.sub(r"[RL]", "W", text)
    text = re.sub(r"n([aeiou])", r"ny\1", text)
    text = text.replace("ove", "uv")
    return text + random.choice(["", " uwu", " owo", " >w<", " ^w^", " :3"])


class TicTacToe(discord.ui.View):
    def __init__(self, x: discord.Member, o: discord.Member):
        super().__init__(timeout=180)
        self.players = {x.id: "❌", o.id: "⭕"}
        self.turn = x
        self.x, self.o = x, o
        self.board = [None] * 9
        for i in range(9):
            self.add_item(TTTButton(i))

    async def interaction_check(self, it: discord.Interaction) -> bool:
        if it.user.id in self.players:
            return True
        await it.response.send_message(f"🔒 This game is between {self.x.mention} and {self.o.mention}.", ephemeral=True)
        return False

    def winner(self):
        lines = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
        for a, b, c in lines:
            if self.board[a] and self.board[a] == self.board[b] == self.board[c]:
                return self.board[a]
        return "tie" if all(self.board) else None


class TTTButton(discord.ui.Button["TicTacToe"]):
    def __init__(self, i):
        super().__init__(label="​", style=discord.ButtonStyle.secondary, row=i // 3)
        self.i = i

    async def callback(self, it: discord.Interaction):
        v = self.view
        if it.user.id != v.turn.id:
            return await it.response.send_message("Not your turn.", ephemeral=True)
        mark = v.players[it.user.id]
        v.board[self.i] = mark
        self.label, self.emoji, self.disabled = None, mark, True
        self.style = discord.ButtonStyle.danger if mark == "❌" else discord.ButtonStyle.primary
        w = v.winner()
        if w:
            for c in v.children:
                c.disabled = True
            text = "It's a tie!" if w == "tie" else f"{it.user.mention} wins! 🎉"
            v.stop()
        else:
            v.turn = v.o if v.turn == v.x else v.x
            text = f"{v.turn.mention}'s turn ({v.players[v.turn.id]})"
        await it.response.edit_message(content=text, view=v)


class Fun(commands.Cog):
    """Games and silly commands"""
    emoji = "🎲"

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------ random
    @commands.command(name="8ball", aliases=["8b"])
    async def eightball(self, ctx: Context, *, question: str):
        """Ask the magic 8-ball"""
        answers = ["It is certain.", "Without a doubt.", "Yes, definitely.", "Most likely.", "Outlook good.",
                   "Signs point to yes.", "Reply hazy, try again.", "Ask again later.", "Cannot predict now.",
                   "Don't count on it.", "My reply is no.", "My sources say no.", "Very doubtful.", "Absolutely not."]
        await ctx.neutral(f"🎱 **{question}**\n{random.choice(answers)}")

    @commands.command(aliases=["cf", "flip"])
    async def coinflip(self, ctx: Context):
        """Flip a coin"""
        await ctx.neutral(f"🪙 **{random.choice(['Heads', 'Tails'])}**")

    @commands.command(aliases=["dice"])
    async def roll(self, ctx: Context, dice: str = "1d6"):
        """Roll dice: `roll 2d20`, `roll 100`"""
        m = re.fullmatch(r"(\d*)d(\d+)", dice.lower())
        if m:
            n, sides = int(m.group(1) or 1), int(m.group(2))
        elif dice.isdigit():
            n, sides = 1, int(dice)
        else:
            raise UserError("Use `NdS` like `2d6` or a number")
        if not (1 <= n <= 50 and 2 <= sides <= 10000):
            raise UserError("Up to 50 dice with 2–10000 sides")
        rolls = [random.randint(1, sides) for _ in range(n)]
        await ctx.neutral(f"🎲 {', '.join(map(str, rolls))}" + (f" = **{sum(rolls)}**" if n > 1 else ""))

    @commands.command(aliases=["pick"])
    async def choose(self, ctx: Context, *, options: str):
        """Pick between options: `choose pizza, tacos, sushi`"""
        opts = [o.strip() for o in re.split(r",|\bor\b|\|", options) if o.strip()]
        if len(opts) < 2:
            raise UserError("Give at least two options separated by commas")
        await ctx.neutral(f"🤔 I choose **{random.choice(opts)}**")

    @commands.command()
    async def ship(self, ctx: Context, a: discord.Member, b: discord.Member = None):
        """Ship compatibility"""
        b, a = (b, a) if b else (a, ctx.author)
        pct = int(hashlib.md5(f"{min(a.id, b.id)}{max(a.id, b.id)}".encode()).hexdigest(), 16) % 101
        bar = "❤️" * (pct // 10) + "🖤" * (10 - pct // 10)
        name = a.display_name[: len(a.display_name) // 2] + b.display_name[len(b.display_name) // 2:]
        await ctx.neutral(f"💘 **{a.display_name}** + **{b.display_name}** = **{name}**\n{bar} **{pct}%**")

    @commands.command(aliases=["howcool", "rate"])
    async def coolrate(self, ctx: Context, member: discord.Member = None):
        """How cool is someone"""
        member = member or ctx.author
        pct = int(hashlib.md5(f"cool{member.id}".encode()).hexdigest(), 16) % 101
        await ctx.neutral(f"😎 {member.mention} is **{pct}%** cool")

    @commands.command()
    async def rps(self, ctx: Context, choice: str):
        """Rock paper scissors"""
        opts = {"rock": "🪨", "paper": "📄", "scissors": "✂️", "r": "🪨", "p": "📄", "s": "✂️"}
        full = {"r": "rock", "p": "paper", "s": "scissors"}
        choice = full.get(choice.lower(), choice.lower())
        if choice not in opts:
            raise UserError("Pick rock, paper or scissors")
        bot = random.choice(["rock", "paper", "scissors"])
        beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
        result = "Tie!" if bot == choice else ("You win! 🎉" if beats[choice] == bot else "I win! 😈")
        await ctx.neutral(f"{opts[choice]} vs {opts[bot]} — **{result}**")

    @commands.command(aliases=["ttt"])
    @commands.guild_only()
    async def tictactoe(self, ctx: Context, opponent: discord.Member):
        """Play tic-tac-toe with someone"""
        if opponent.bot or opponent == ctx.author:
            raise UserError("Pick another human")
        await ctx.send(f"{ctx.author.mention} (❌) vs {opponent.mention} (⭕) — {ctx.author.mention}'s turn",
                       view=TicTacToe(ctx.author, opponent))

    @commands.command()
    async def reverse(self, ctx: Context, *, text: str):
        """Reverse text"""
        await ctx.send(text[::-1][:2000], allowed_mentions=discord.AllowedMentions.none())

    @commands.command()
    async def uwu(self, ctx: Context, *, text: str):
        """UwU-ify text"""
        await ctx.send(uwuify(text)[:2000], allowed_mentions=discord.AllowedMentions.none())

    @commands.command()
    async def cat(self, ctx: Context):
        """Random cat"""
        async with self.bot.session.get("https://api.thecatapi.com/v1/images/search") as r:
            url = (await r.json())[0]["url"]
        await ctx.send(embed=discord.Embed(color=Colors.main).set_image(url=url))

    @commands.command()
    async def dog(self, ctx: Context):
        """Random dog"""
        async with self.bot.session.get("https://dog.ceo/api/breeds/image/random") as r:
            url = (await r.json())["message"]
        await ctx.send(embed=discord.Embed(color=Colors.main).set_image(url=url))

    @commands.command(aliases=["dadjoke", "pun"])
    @requires_uperm()
    async def joke(self, ctx: Context):
        """Get a random dad joke or pun"""
        headers = {"Accept": "application/json", "User-Agent": "VeilUtilities/1.0"}
        async with self.bot.session.get("https://icanhazdadjoke.com/", headers=headers) as r:
            if r.status != 200:
                raise UserError("Couldn't think of a joke right now, try again in a second!")
            data = await r.json(content_type=None)
        joke_text = data.get("joke", "I couldn't think of a joke right now!")
        e = discord.Embed(
            title="😂 Random Joke",
            description=f"*{joke_text}*",
            color=Colors.main
        )
        e.set_footer(text="icanhazdadjoke.com")
        await ctx.send(embed=e)

    @commands.command(name="redditmeme", aliases=["dankmeme", "rmeme"])
    @requires_uperm()
    async def redditmeme(self, ctx: Context):
        """Fetch a random trending meme from Reddit"""
        async with self.bot.session.get("https://meme-api.com/gimme", headers={"User-Agent": "VeilUtilities/1.0"}) as r:
            if r.status != 200:
                raise UserError("Meme stream is temporarily unavailable, try again in a moment!")
            data = await r.json(content_type=None)
        title = data.get("title", "Random Meme")
        post_link = data.get("postLink", "https://reddit.com")
        img_url = data.get("url")
        subreddit = data.get("subreddit", "memes")
        ups = data.get("ups", 0)

        e = discord.Embed(title=title, url=post_link, color=Colors.main)
        if img_url:
            e.set_image(url=img_url)
        e.set_footer(text=f"r/{subreddit} • 👍 {ups:,}")
        await ctx.send(embed=e)

    @commands.command(name="advice", aliases=["tip", "wisdom"])
    @requires_uperm()
    async def advice(self, ctx: Context):
        """Receive random daily advice and wisdom"""
        async with self.bot.session.get("https://api.adviceslip.com/advice", headers={"User-Agent": "VeilUtilities/1.0"}) as r:
            if r.status != 200:
                raise UserError("Advice service is busy, try again in a moment!")
            data = await r.json(content_type=None)
        slip = data.get("slip", {})
        advice_text = slip.get("advice", "Always treat others the way you want to be treated.")
        e = discord.Embed(
            title="💡 Words of Wisdom",
            description=f"\"{advice_text}\"",
            color=Colors.main
        )
        e.set_footer(text="Advice Slip API")
        await ctx.send(embed=e)

    # ------------------------------------------------------------ interactions
    async def _interact(self, ctx: Context, action: str, member: discord.Member | None):
        try:
            async with self.bot.session.get(f"https://nekos.best/api/v2/{NEKOS.get(action, action)}") as r:
                url = (await r.json())["results"][0]["url"] if r.status == 200 else None
        except Exception:
            url = None
        target = member.mention if member and member != ctx.author else "themselves"
        e = discord.Embed(color=Colors.main, description=f"**{ctx.author.mention}** {ACTIONS[action]} **{target}**")
        if url:
            e.set_image(url=url)
        await ctx.send(embed=e)

    def _make(name):  # noqa: N805 - factory for interaction commands
        @commands.command(name=name, help=f"{name.title()} someone")
        async def cmd(self, ctx: Context, member: discord.Member = None):
            await self._interact(ctx, name, member)
        return cmd

    hug = _make("hug"); kiss = _make("kiss"); pat = _make("pat"); slap = _make("slap"); cuddle = _make("cuddle")
    poke = _make("poke"); bite = _make("bite"); highfive = _make("highfive"); wave = _make("wave")
    punch = _make("punch"); tickle = _make("tickle"); feed = _make("feed"); handhold = _make("handhold")
    yeet = _make("yeet"); peck = _make("peck")
    del _make

    # ------------------------------------------------------------ uwulock
    @commands.command()
    @has_perms(manage_messages=True)
    @commands.bot_has_permissions(manage_webhooks=True, manage_messages=True)
    async def uwulock(self, ctx: Context, member: discord.Member):
        """Toggle forcing someone's messages into uwu speak"""
        cfg = await self.bot.db.get_config(ctx.guild.id, "uwulock")
        locked = cfg.setdefault("users", [])
        if member.id in locked:
            locked.remove(member.id); msg = f"freed {member.mention} from uwulock"
        else:
            locked.append(member.id); msg = f"uwulocked {member.mention}"
        await self.bot.db.set_config(ctx.guild.id, "uwulock", cfg)
        await ctx.approve(msg)

    @commands.Cog.listener()
    async def on_message(self, m: discord.Message):
        if not m.guild or m.author.bot or not m.content or not isinstance(m.channel, discord.TextChannel):
            return
        cfg = await self.bot.db.get_config(m.guild.id, "uwulock")
        if m.author.id not in cfg.get("users", []):
            return
        try:
            hooks = await m.channel.webhooks()
            hook = discord.utils.get(hooks, name="veil utilities") or await m.channel.create_webhook(name="veil utilities")
            await m.delete()
            await hook.send(uwuify(m.content)[:2000], username=m.author.display_name,
                            avatar_url=m.author.display_avatar.url, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Fun(bot))
