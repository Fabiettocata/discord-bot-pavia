import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
import asyncio
import datetime
import pytz
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging  # LOG: import logging

# LOG: setup base logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === CONFIGURAZIONE ===
TOKEN = os.environ['DISCORD_TOKEN']
GUILD_ID = int(os.environ['DISCORD_GUILD_ID'])
CHANNEL_ID = int(os.environ['DISCORD_CHANNEL_ID'])
CHANNEL_ID_CLASSIFICA = int(os.environ['CHANNEL_ID_CLASSIFICA'])

italy_tz = pytz.timezone("Europe/Rome")

# === Google Sheets Setup ===
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open("Presenze Pavia Esports").sheet1  # nome esatto del file
    logging.info("Accesso a Google Sheets riuscito.")  # LOG
except Exception as e:
    logging.error(f"Errore accesso Google Sheets: {e}")  # LOG

# === INTENTI E BOT ===
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# === PULSANTI ===
class PresenzaView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="✅ Presente", style=discord.ButtonStyle.success, custom_id="presente"))
        self.add_item(Button(label="🕒 Ritardo", style=discord.ButtonStyle.primary, custom_id="ritardo"))
        self.add_item(Button(label="❌ Assente", style=discord.ButtonStyle.danger, custom_id="assente"))

# === FUNZIONE PER CALCOLARE I PUNTI ===
def calcola_punti(voto, ora_voto):
    # voto: 'presente', 'ritardo', 'assente'
    # ora_voto: datetime oggetto
    ora = ora_voto.hour + ora_voto.minute/60
    if ora <= 15:
        bonus = 1
    elif 15 < ora <= 20:
        bonus = 0
    else:
        # voto oltre le 20 o assenza voto = malus -2
        return -2

    if voto == "presente":
        base = 3
    elif voto == "ritardo":
        base = 1.5
    elif voto == "assente":
        base = 0
    else:
        base = 0

    return base + bonus

# === FUNZIONE PER OTTENERE DATI E COSTRUIRE LA CLASSIFICA ===
def costruisci_classifica():
    try:
        records = sheet.get_all_records()
        logging.info("Dati Google Sheets letti correttamente.")  # LOG
    except Exception as e:
        logging.error(f"Errore lettura dati da Google Sheets: {e}")  # LOG
        return "Errore nel recupero dati."

    # struttura {nome: punti_totali}
    punteggi = {}

    now = datetime.datetime.now(italy_tz)

    # Controlliamo anche chi non ha votato oggi e assegniamo malus -2
    votanti_oggi = set()

    for record in records:
        # record esempio: {'Timestamp': '2025-05-29 12:00', 'User': 'Lorenzo', 'Voto': 'presente'}
        timestamp_str = record.get('Timestamp') or record.get('timestamp') or record.get('Data') or record.get('data')
        user = record.get('User') or record.get('user') or record.get('Nome') or record.get('nome')
        voto = record.get('Voto') or record.get('voto') or record.get('Risposta') or record.get('risposta')
        if not (timestamp_str and user and voto):
            continue

        # Converti stringa timestamp in datetime
        try:
            voto_time = datetime.datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M")
            voto_time = italy_tz.localize(voto_time)
        except Exception:
            continue

        # Conta solo le risposte del giorno corrente (oggi)
        if voto_time.date() == now.date():
            votanti_oggi.add(user)
            punti = calcola_punti(voto.lower(), voto_time)
            punteggi[user] = punteggi.get(user, 0) + punti

    # Recupera lista completa membri (ipotizziamo membri dal guild Discord)
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        logging.error("Guild non trovata nel bot.")  # LOG
        return "Errore: guild non trovata."

    tutti_i_membri = [member.name for member in guild.members if not member.bot]

    # Assegna malus -2 a chi non ha votato oggi
    for membro in tutti_i_membri:
        if membro not in votanti_oggi:
            punteggi[membro] = punteggi.get(membro, 0) - 2

    # Ordina la classifica per punteggio decrescente
    classifica_ordinata = sorted(punteggi.items(), key=lambda x: x[1], reverse=True)

    # Costruisci messaggio da inviare
    messaggio = "**📊 Classifica presenze settimanale:**\n\n"
    pos = 1
    for nome, punti in classifica_ordinata:
        messaggio += f"{pos}. {nome}: {punti:.1f} punti\n"
        pos += 1

    return messaggio

# === TASK PER PUBBLICARE LA CLASSIFICA SETTIMANALE ===
@tasks.loop(minutes=1)
async def classifica_settimanale():
    now = datetime.datetime.now(italy_tz)
    if now.weekday() == 4 and now.hour == 12 and now.minute == 0:  # venerdì ore 12:00
        channel = bot.get_channel(CHANNEL_ID_CLASSIFICA)
        if channel:
            msg = costruisci_classifica()
            await channel.send(msg)
            logging.info(f"Classifica settimanale pubblicata il {now.strftime('%A %d %B %Y %H:%M')}")  # LOG

# === EVENTO BOT ONLINE ===
@bot.event
async def on_ready():
    logging.info(f"{bot.user} è online!")  # LOG
    if not sondaggio_giornaliero.is_running():
        sondaggio_giornaliero.start()
    if not classifica_settimanale.is_running():
        classifica_settimanale.start()

# === SONDAGGIO AUTOMATICO ===
@tasks.loop(minutes=1)
async def sondaggio_giornaliero():
    now = datetime.datetime.now(italy_tz)
    weekday = now.weekday()
    if weekday in [0, 1, 2, 3] and now.hour == 12 and now.minute == 0:
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(
                "**📋 Vota la tua presenza di oggi!**\n\nScegli tra le opzioni qui sotto:",
                view=PresenzaView()
            )
            logging.info(f"Sondaggio inviato il {now.strftime('%A %d %B %Y')}")  # LOG

# === RISPOSTA AI PULSANTI E REGISTRAZIONE SU SHEET ===
@bot.event
async def on_interaction(interaction: discord.Interaction):
    user = interaction.user.name
    scelta = interaction.data['custom_id']
    timestamp = datetime.datetime.now(italy_tz).strftime("%Y-%m-%d %H:%M")

    # Scrittura sul foglio
    try:
        sheet.append_row([timestamp, user, scelta])
        logging.info(f"{user} ha votato {scelta} alle {timestamp} (registrato su Google Sheets)")  # LOG
    except Exception as e:
        logging.error(f"Errore durante la scrittura su Google Sheets: {e}")  # LOG

    await interaction.response.send_message(
        f"Hai selezionato **{scelta.upper()}**. Grazie per aver votato! ✅",
        ephemeral=True
    )

# === COMANDO MANUALE PER TEST ===
@bot.command(name="test_sondaggio")
async def test_sondaggio(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(
            "**📋 Test sondaggio presenza**\n\nScegli tra le opzioni qui sotto:",
            view=PresenzaView()
        )
    else:
        await ctx.send("Canale non trovato.")

# === AVVIO BOT ===
bot.run(TOKEN)
