import discord
from discord.ext import commands, tasks
import json
from wallapop import WallapopClient
from storage import Storage
import asyncio

# ── Cargar configuración ──────────────────────────────────────────────────────
with open("config.json", "r") as f:
    config = json.load(f)

TOKEN       = config["discord_token"]
CHECK_EVERY = config.get("check_interval_seconds", 120)

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

bot     = commands.Bot(intents=intents, loop=loop)
storage = Storage("data.json")
client  = WallapopClient()

# ─────────────────────────────────────────────────────────────────────────────
# Helper: parsea "término 300 50" → ("término", 300.0, 50.0)
# ─────────────────────────────────────────────────────────────────────────────
def parse_args(raw: str):
    """
    Extrae keyword, max_price y min_price de una cadena libre.
    Los números al final se interpretan como precios.
    Ejemplos:
      "mini pc"          → ("mini pc", None, None)
      "mini pc 300"      → ("mini pc", 300.0, None)
      "mini pc 300 50"   → ("mini pc", 300.0, 50.0)
    """
    parts  = raw.strip().split()
    prices = []
    words  = []
    for p in reversed(parts):
        try:
            prices.insert(0, float(p))
        except ValueError:
            words = parts[:len(parts) - len(prices)]
            break
    else:
        words = []

    keyword   = " ".join(words).strip() or raw.strip()
    max_price = prices[0] if len(prices) >= 1 else None
    min_price = prices[1] if len(prices) >= 2 else None
    return keyword, max_price, min_price


# ─────────────────────────────────────────────────────────────────────────────
# Tarea periódica
# ─────────────────────────────────────────────────────────────────────────────
@tasks.loop(seconds=CHECK_EVERY)
async def check_wallapop():
    alerts = storage.get_alerts()
    if not alerts:
        return
    for alert in alerts:
        channel = bot.get_channel(alert["channel_id"])
        if channel is None:
            continue
        try:
            items = await client.search(
                keyword   = alert["keyword"],
                min_price = alert.get("min_price"),
                max_price = alert.get("max_price"),
            )
        except Exception as e:
            print(f"[ERROR] Wallapop search failed: {e}")
            continue
        new_items = storage.filter_new(alert["id"], items)
        for item in new_items:
            embed = build_embed(item, alert)
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                pass

@check_wallapop.before_loop
async def before_check():
    await bot.wait_until_ready()


def build_embed(item: dict, alert: dict) -> discord.Embed:
    price_str = f"{item['price']} €" if item.get("price") is not None else "Sin precio"
    embed = discord.Embed(
        title       = item.get("title", "Sin título")[:256],
        url         = item.get("url", ""),
        description = item.get("description", "")[:300] or "*Sin descripción*",
        color       = discord.Color.from_rgb(89, 203, 232),
    )
    embed.set_author(name=f"🔔 Nueva alerta: {alert['keyword'].upper()}")
    embed.add_field(name="💶 Precio",    value=price_str,                 inline=True)
    embed.add_field(name="📍 Ubicación", value=item.get("location", "—"), inline=True)
    embed.add_field(name="👤 Vendedor",  value=item.get("seller", "—"),   inline=True)
    if item.get("image"):
        embed.set_thumbnail(url=item["image"])
    embed.set_footer(text="Wallapop • Segunda mano")
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# Comandos  (todos usan *, raw: str para aceptar términos con espacios)
# ─────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    check_wallapop.start()
    print(f"✅  Bot conectado como {bot.user}  |  Revisando cada {CHECK_EVERY}s")


@bot.slash_command(name="alerta", description="TO CHANGE")
async def add_alert(
        ctx, 
        keyword: str = discord.Option(str, "TO CHANGE", required=True), 
        max_price: int = discord.Option(int, "TO CHANGE", required=False), 
        min_price: int = discord.Option(int, "TO CHANGE", required=False)
    ):
    """
    Crea una alerta. Admite términos con espacios.
    Uso:
      /alerta mini pc
      /alerta mini pc 300
      /alerta mini pc 300 50
    """

    alert_id = storage.add_alert(
        channel_id = ctx.channel.id,
        keyword    = keyword,
        min_price  = min_price,
        max_price  = max_price,
    )

    if max_price and min_price:
        price_txt = f"entre **{min_price} €** y **{max_price} €**"
    elif max_price:
        price_txt = f"hasta **{max_price} €**"
    elif min_price:
        price_txt = f"desde **{min_price} €**"
    else:
        price_txt = "sin filtro de precio"

    embed = discord.Embed(
        title       = "✅ Alerta creada",
        description = f"Buscaré **{keyword}** en Wallapop {price_txt}.\nTe avisaré aquí cuando haya publicaciones nuevas.",
        color       = discord.Color.green(),
    )
    embed.set_footer(text=f"ID de alerta: {alert_id}")
    await ctx.respond(embed=embed)


@bot.slash_command(name="alertas", description="TO CHANGE")
async def list_alerts(
        ctx
    ):

    alerts = [a for a in storage.get_alerts() if a["channel_id"] == ctx.channel.id]
    if not alerts:
        await ctx.respond("📭 No hay alertas activas en este canal.")
        return
    embed = discord.Embed(title="🔔 Alertas activas en este canal", color=discord.Color.blue())
    for a in alerts:
        price_parts = []
        if a.get("min_price") is not None:
            price_parts.append(f"desde {a['min_price']} €")
        if a.get("max_price") is not None:
            price_parts.append(f"hasta {a['max_price']} €")
        price_txt = " · ".join(price_parts) if price_parts else "sin filtro"
        embed.add_field(
            name  = f"🔍 `{a['keyword']}`  (ID: {a['id']})",
            value = price_txt,
            inline=False,
        )
    await ctx.respond(embed=embed)


@bot.slash_command(name="eliminar", description="TO CHANGE")
async def remove_alert(
        ctx, 
        alert_id: str = discord.Option(str, "TO CHANGE", required=True)
    ):

    removed = storage.remove_alert(alert_id)
    if removed:
        await ctx.respond(f"🗑️ Alerta `{alert_id}` eliminada correctamente.")
    else:
        await ctx.respond(f"❌ No encontré ninguna alerta con ID `{alert_id}`.")


@bot.slash_command(name="buscar", description="TO CHANGE")
async def search_now(
        ctx,
        keyword: str = discord.Option(str, "TO CHANGE", required=True), 
        max_price: int = discord.Option(int, "TO CHANGE", required=False), 
        min_price: int = discord.Option(int, "TO CHANGE", required=False)
    ):
    """
    Búsqueda manual. Admite términos con espacios.
    Uso:
      !buscar mini pc
      !buscar mini pc 300
      !buscar mini pc 300 50
    """

    async with ctx.typing():
        try:
            items = await client.search(keyword=keyword, min_price=min_price, max_price=max_price)
        except Exception as e:
            await ctx.respond(f"❌ Error al buscar: `{e}`")
            return

    if not items:
        await ctx.respond(f"😕 No encontré resultados para **{keyword}**.")
        return

    alert_ctx = {"keyword": keyword, "id": "manual"}
    await ctx.respond(f"🔎 Primeros **{len(items[:5])}** resultados para **{keyword}**:")
    for item in items[:5]:
        await ctx.respond(embed=build_embed(item, alert_ctx))


@bot.slash_command(name="ayuda", description="TO CHANGE")
async def help_cmd(ctx):
    embed = discord.Embed(
        title       = "📖 Comandos del Bot de Wallapop",
        description = "Te aviso cuando haya anuncios nuevos en Wallapop que coincidan con tus alertas.\n"
                      "Los términos **pueden tener espacios** — los últimos números se interpretan como precios.",
        color       = discord.Color.orange(),
    )
    embed.add_field(
        name  = "!alerta `<término>` `[precio_max]` `[precio_min]`",
        value = "Ejemplos:\n`!alerta minipc`\n`!alerta mini pc 150`\n`!alerta iphone 14 500 200`",
        inline=False,
    )
    embed.add_field(name="!alertas",                    value="Lista las alertas activas en este canal.", inline=False)
    embed.add_field(name="!eliminar `<id>`",            value="Elimina una alerta por su ID.",           inline=False)
    embed.add_field(
        name  = "!buscar `<término>` `[precio_max]` `[precio_min]`",
        value = "Búsqueda manual. Ejemplos:\n`!buscar mini pc`\n`!buscar teclado 80`",
        inline=False,
    )
    embed.set_footer(text=f"Revisión automática cada {CHECK_EVERY} segundos")
    await ctx.respond(embed=embed)


bot.run(TOKEN)