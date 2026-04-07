from discord.ext import commands
from utils.embeds import queue_embed, error_embed, success_embed


class QueueCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx: commands.Context, page: int = 1):
        state = self.fs.get_server_state(str(ctx.guild.id))
        if not state:
            await ctx.send(embed=error_embed("No active session."))
            return
        q = state.get("queue", [])
        current = state.get("currentTrack")
        embed = queue_embed(q, current, page=page - 1)
        await ctx.send(embed=embed)

    @commands.command(name="remove")
    async def remove(self, ctx: commands.Context, position: int):
        queue = self.fs.get_queue(str(ctx.guild.id))
        if position < 1 or position > len(queue):
            await ctx.send(embed=error_embed(f"Invalid position. Queue has {len(queue)} tracks."))
            return
        removed = queue[position - 1]
        self.fs.remove_from_queue(str(ctx.guild.id), position - 1)
        await ctx.send(embed=success_embed(f"Removed: **{removed['title']}**"))

    @commands.command(name="move")
    async def move(self, ctx: commands.Context, from_pos: int, to_pos: int):
        queue = self.fs.get_queue(str(ctx.guild.id))
        if (from_pos < 1 or from_pos > len(queue) or
                to_pos < 1 or to_pos > len(queue)):
            await ctx.send(embed=error_embed(f"Invalid positions. Queue has {len(queue)} tracks."))
            return
        track = queue[from_pos - 1]
        self.fs.reorder_queue(str(ctx.guild.id), from_pos - 1, to_pos - 1)
        await ctx.send(embed=success_embed(
            f"Moved **{track['title']}** from position {from_pos} to {to_pos}"
        ))

    @commands.command(name="shuffle")
    async def shuffle(self, ctx: commands.Context):
        queue = self.fs.get_queue(str(ctx.guild.id))
        if not queue:
            await ctx.send(embed=error_embed("Queue is empty."))
            return
        self.fs.shuffle_queue(str(ctx.guild.id))
        await ctx.send(embed=success_embed(f"Shuffled {len(queue)} tracks."))


async def setup(bot: commands.Bot):
    await bot.add_cog(QueueCmd(bot))
