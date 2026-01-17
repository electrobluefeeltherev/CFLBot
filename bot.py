import discord
import sqlite3
from datetime import datetime, timedelta
import pytz
import asyncio
import zoneinfo
from pathlib import Path
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

MATCH_ADMIN = 1459375669417869473

DB_PATH = Path("stats.db")
def init_database():
    """Safe initialization: connects to existing db and creates missing tables/indexes"""
    if not DB_PATH.exists():
        print('No stats.db found, Creating a new one...')
    else:
        print('Found existing stats.db, Checking/Updating tables...')

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript(
        """
        -- teams table
        CREATE TABLE IF NOT EXISTS teams (
            name                        TEXT PRIMARY KEY,
            shorthandle                 TEXT NOT NULL,
            created_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            budget                      INTEGER DEFAULT 0,
            captain_id                  TEXT NOT NULL,
            teamrole_id                 TEXT,
            captainrole_id              TEXT,
            total_runs                  INTEGER DEFAULT 0,
            matches_played              INTEGER DEFAULT 0
        );

        -- players
        CREATE TABLE IF NOT EXISTS players (
            user_id                     TEXT PRIMARY KEY,
            team_name                   TEXT,
            joined_at                   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (team_name)     REFERENCES teams(name) ON DELETE SET NULL
        );

        -- player stats

        CREATE TABLE IF NOT EXISTS stats (
            user_id                     TEXT PRIMARY KEY,
            total_runs                  INTEGER DEFAULT 0,
            batting_innings             INTEGER DEFAULT 0,
            times_out                   INTEGER DEFAULT 0,
            balls_faced                 INTEGER DEFAULT 0,
            clutch_runs                 INTEGER DEFAULT 0,
            wickets_taken               INTEGER DEFAULT 0,
            bowling_innings             INTEGER DEFAULT 0,
            balls_bowled                INTEGER DEFAULT 0,
            runs_conceded               INTEGER DEFAULT 0,
            clutch_wickets              INTEGER DEFAULT 0,
            FOREIGN KEY (user_id)       REFERENCES players(user_id) ON DELETE SET NULL
        );

        -- Xx -------------------------- xX Per Match Tables -------------------------- xX --

        -- matches
        CREATE TABLE IF NOT EXISTS matches (
            match_id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            match_date                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            team_a                      TEXT NOT NULL,
            team_b                      TEXT NOT NULL,
            winner                      TEXT CHECK(winner IN (team_a, team_b, 'tie', NULL)),
            total_runs_team_a           INTEGER DEFAULT 0,
            total_runs_team_b           INTEGER DEFAULT 0,
            FOREIGN KEY (team_a)        REFERENCES teams(name),
            FOREIGN KEY (team_b)        REFERENCES teams(name)
        );

        -- per match player-performance
        CREATE TABLE IF NOT EXISTS match_player_stats (
            match_id                    INTEGER NOT NULL,
            user_id                     TEXT NOT NULL,
            team_name                   TEXT NOT NULL,
            runs_scored                 INTEGER DEFAULT 0,
            balls_faced                 INTEGER DEFAULT 0,
            wickets_taken               INTEGER DEFAULT 0,
            runs_conceded               INTEGER DEFAULT 0,
            balls_bowled                INTEGER DEFAULT 0,
            is_captain                  BOOLEAN DEFAULT 0,
            is_man_of_match             BOOLEAN DEFAULT 0,
            PRIMARY KEY (match_id, user_id),
            FOREIGN KEY (match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES players(user_id) ON DELETE CASCADE,
            FOREIGN KEY (team_name) REFERENCES teams(name)
        );

        CREATE TABLE IF NOT EXISTS captain_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,          -- who did the action
            team_name   TEXT,                   -- optional
            captain_id  TEXT NOT NULL,          -- who became captain
            action      TEXT NOT NULL,          -- 'add' or 'remove'
            timestamp   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    c.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_players_team ON players (team_name);

        CREATE INDEX IF NOT EXISTS idx_match_stats_player ON match_player_stats (user_id);

        CREATE INDEX IF NOT EXISTS idx_match_stats_match ON match_player_stats (match_id);

        CREATE INDEX IF NOT EXISTS idx_match_stats_team_runs ON match_player_stats (team_name, runs_scored DESC);
        """
    )
    
    conn.commit()
    conn.close()

init_database()

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in c.fetchall()]
print("Tables found in database:", tables)
conn.close()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

IST = pytz.timezone("Asia/Kolkata")
auction_reminder = None

auction_active = False
auction_channel = None
current_player_id = None
current_bid = 0
highest_bidder_id = None
highest_bidder_team = None
bid_timer_task = None

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}, (ID: {bot.user.id})')
    print('----------')

async def get_next_unsold_player():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT user_id FROM players WHERE team_name IS NULL OR team_name = '' ORDER BY joined_at ASC LIMIT 1
                """
            )
            row = c.fetchone()
            return row[0] if row else None
    except Exception as e:
        print(f"Error getting unsold player: {e}")
        return None

async def finalize_sale():
    global current_player_id, current_bid, highest_bidder_team

    await auction_channel.send(f"**SOLD** <@{current_player_id}> to {highest_bidder_team}\nfor **{current_bid}**")

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("UPDATE players SET team_name = ? WHERE user_id = ?", (highest_bidder_team, current_player_id))
        c.execute("UPDATE teams SET budget = budget - ? WHERE name = ?", (current_bid, highest_bidder_team))
        conn.commit()

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT teamrole_id FROM teams WHERE name = ?", (highest_bidder_team,))
        role_id = c.fetchone()[0]
    
    guild = auction_channel.guild
    role = guild.get_role(int(role_id))
    member = guild.get_member(int(current_player_id))

    if role and member:
        await member.add_roles(role)

async def bid_timer():
    global current_bid, bid_timer_task

    try:
        await asyncio.sleep(15)

        if current_bid == 0:
            await auction_channel.send(f"**No bids** for <@{current_player_id}>; Player skipped")

            try:
                with sqlite3.connect(DB_PATH) as conn:
                    c = conn.cursor()
                    c.execute("UPDATE players SET team_name = '__No_Bids__'  WHERE user_id = ?", (current_player_id,))
                    conn.commit()

            except Exception as e:
                print(f"Error marking player skipped: {e}")
        else:
            await finalize_sale()

        await start_player_auction()

    except asyncio.CancelledError:
        pass

async def start_player_auction():
    global current_player_id, current_bid
    global highest_bidder_id, highest_bidder_team
    global bid_timer_task, auction_active

    if auction_channel is None:
        print("Auction channel not set. Aborting auction")
        auction_active = False
        return

    current_player_id = await get_next_unsold_player()

    if current_player_id is None:
        auction_active = False
        current_player_id = None
        current_bid = 0
        highest_bidder_id = None
        highest_bidder_team = None

        await auction_channel.send("Auction finished, no unsold players")
        return
    
    current_bid = 0
    highest_bidder_id = None
    highest_bidder_team = None

    PlayerEmbed = discord.Embed(
        title="Player up for Auction",
        description=f"<@{current_player_id}> is now open for bidding\n\nUse !bid <amount>\n\nExample:\n!bid 10M\n!bid 10000000",
        color= discord.Color.blue()
    )
    PlayerEmbed.add_field(name="Current Bid", value="No bids yet", inline=False)
    PlayerEmbed.add_field(name="Time remaining", value="15s", inline=False)

    await auction_channel.send(embed=PlayerEmbed)
    if bid_timer_task:
        bid_timer_task.cancel()
    bid_timer_task = asyncio.create_task(bid_timer())

@bot.tree.command(name="startauction", description="Let the auction begin!")
@app_commands.describe(channel = "The Auction Channel")
async def startauction(interaction: discord.Interaction, channel: discord.TextChannel):
    global auction_active, auction_channel

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("This action requires administrator privileges", ephemeral=True)
        return
    
    if auction_active:
        await interaction.response.send_message("Auction already running!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT captain_id FROM teams")
            captain_ids = {row[0] for row in c.fetchall()}

            if captain_ids:
                placeholders = ",".join("?" * len(captain_ids))

                c.execute(
                    f"""
                    UPDATE players
                    SET team_name = NULL
                    WHERE user_id NOT in ({placeholders})
                    AND team_name IS NOT NULL
                    AND team_name != ''
                    AND team_name != '__No_Bids__'
                    """, tuple(captain_ids)
                )
                affected = c.rowcount
                conn.commit()
            else:
                affected = 0
            await interaction.followup.send(f"Reset **{affected}** player(s) back into auction pool")
    except Exception as e:
        await interaction.followup.send(f"Error resetting players into auction pool: {e}")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT count(*) FROM players")
            player_count = c.fetchone()[0]
            if player_count == 0:
                await interaction.followup.send("There are no players registered(enrolled) for auction in the database", ephemeral=True)
                return
    except Exception as e:
        await interaction.followup.send(f"Error checking for players: {e}", ephemeral=True)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT count(*) FROM teams")
            team_count = c.fetchone()[0]
            if team_count == 0:
                await interaction.followup.send("There are no teams registered in the database", ephemeral=True)
                return
            c.execute("UPDATE teams SET budget = 145000000")
            updated = c.rowcount
            conn.commit()

        await interaction.followup.send(f"**All team budgets have been reset.**\n{updated} teams now have **145000000** each")
        await channel.send(f"**All team budgets have been reset.**\n{updated} teams now have **145000000** each")

    except Exception as e:
        await interaction.followup.send(f"Error resetting budgets:{e}", ephemeral=True)


    auction_active = True
    auction_channel = channel

    await channel.send("**Auction is live**\nUse !bid <amount>")
    await start_player_auction()


@bot.event
async def on_message(message: discord.Message):
    global current_bid, highest_bidder_id, highest_bidder_team, bid_timer_task

    if message.author.bot:
        return

    if not auction_active:
        return
    
    if message.channel != auction_channel:
        return
    
    if not message.content.lower().startswith("!bid"):
        return
    
    captain_roles = [r for r in message.author.roles if r.name.startswith("(C)")]
    if not captain_roles:
        await message.delete()
        return
    
    team_name = captain_roles[0].name.replace("(C)", "", 1)

    bid_text = message.content[4:].strip().upper()
    try:
        if bid_text.endswith("M"):
            bid_amount = int(float(bid_text[:-1]) * 1_000_000)
        else:
            bid_amount = int(bid_text)

    except ValueError:
        await message.delete()
        return
    
    if bid_amount <= current_bid:
        await message.delete()
        return
    
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT budget FROM teams WHERE name = ?", (team_name,))
        budget = c.fetchone()[0]

    if bid_amount > budget:
        await message.delete()
        return
    
    current_bid = bid_amount
    highest_bidder_id = message.author.id
    highest_bidder_team = team_name

    await auction_channel.send(f"**New bid** {bid_amount:,} by {message.author.mention} ({team_name})\nTimer reset to 15s")

    if bid_timer_task:
        bid_timer_task.cancel()

    bid_timer_task = asyncio.create_task(bid_timer())
    await message.delete()


@bot.tree.command(name="unenroll", description="Unenroll yourself from the game")
async def unenroll(interaction: discord.Interaction):
    user_id = interaction.user.id
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT user_id FROM players WHERE user_id = ?", (user_id,))
            if not c.fetchone():
                await interaction.response.send_message(F"You are not enrolled or your User_ID {user_id} is not found in the database.\nPlease Contact any admin if you think this is a mistake.", ephemeral=True)
                return

            c.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
            conn.commit()

        await interaction.response.send_message(f"**Unenroll Successful**\nUser <@{user_id}> have unenrolled themself from the Cricket Fantasy League")

    except Exception as e:
        await interaction.response.send_message(f"Database error; {e}")
        return

@bot.tree.command(name="auctionreminder", description="Set a reminder for the auction and post the details in a channel")
@app_commands.describe(time="Date and time of the reminder")
async def auctionreminder(interaction: discord.Interaction, time: str, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("This action requires Administrator Privileges", ephemeral=True)
        return
    try:
        reminder_time = datetime.strptime(time.upper(), "%d-%m-%Y %I:%M %p")
        reminder_time = IST.localize(reminder_time)

        now = datetime.now(IST)
        if reminder_time <= now:
            await interaction.response.send_message("Reminder must be set for future time", ephemeral=True)
            return
        
        delay = (reminder_time - now).total_seconds()

    except ValueError:
        await interaction.response.send_message("Invalid Time Format use **DD-MM-YYYY HH:MM AM/PM (eg. 17-02-1980 10:30 PM)", ephemeral=True)
        return
    
    formatted_time = reminder_time.strftime("%d %b %Y, %I:%M %p IST")
    
    await interaction.response.send_message(f"Auction Reminder set for **{formatted_time}** IST\nIn {channel.mention}")

    await asyncio.sleep(delay)

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM players")
        rows = c.fetchall()

    mentions = [f"<@{user_id}>" for (user_id,) in rows]

    AuctionEmbed = discord.Embed(
        title="**Auction Reminder**",
        description="Auction Reminder for the upcoming CFL Game",
        color=discord.Colour.gold()
    )
    AuctionEmbed.add_field(
        name="Players for Sale",
        value="\n".join(mentions),
        inline=False
    )

    await channel.send(embed=AuctionEmbed, content="@everyone" if mentions else None)

@bot.tree.command(name="enroll", description="Enroll yourself into the Cricket Fantasy League")
async def enroll(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM players WHERE user_id = ?", (user_id,))
            if c.fetchone():
                await interaction.response.send_message("You're already enrolled!", ephemeral=True)
                return
            c.execute(
                """
                INSERT INTO players (user_id, team_name)
                VALUES (?, NULL)
                """,
                (user_id,)
            )
            conn.commit()

        await interaction.response.send_message("**Enrollment Successful**\nYou are now a part of The Cricket Fatansy League\nYou may now join or create a team", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Enrollment failed: {e}", ephemeral=True)

async def check_enrolled(user_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM players WHERE user_id = ?", (user_id,))
        return bool(c.fetchone())

@bot.tree.command(name="createteam", description="Create a new Team; Your own Dream-Team")
@app_commands.describe(
    team_name="Name of the new team",
    shorthandle="A catchy shorform for your Team-Name",
    color="Team color in hex format (eg. #FF5555) - Optional"
    )

async def createteam(interaction: discord.Interaction, team_name: str, shorthandle: str, color: str = "#5865f2"):
    user_id = str(interaction.user.id)
    import re
    color = color.strip().upper()
    if not re.match(r'^#[0-9A-F]{6}$', color):
        await interaction.response.send_message("Invalid color format! Use #RRGGBB(eg. #FF55555)", ephemeral=True)
        return
    
    try:
        team_color = discord.Color(int(color[1:], 16))

    except ValueError:
        await interaction.response.send_message("Invalid hex color value!", ephemeral=True)
        return
    
    if not await check_enrolled(str(interaction.user.id)):
        await interaction.response.send_message("Please /enroll first!", ephemeral=True)
        return
    
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()

        c.execute("SELECT team_name FROM players WHERE user_id = ?", (user_id,))
        result = c.fetchone()

        if result and result[0] is not None:
            current_team = result[0]
            await interaction.response.send_message(
                f"You are already in team **{current_team}**!\n"
                "You cannot create another team while in one.\n"
                "Leave your current team first.",
                ephemeral=True
            )
            return
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO teams (name, shorthandle, captain_id) VALUES (?, ?, ?)
                """,
                (team_name, shorthandle, str(interaction.user.id))
            )
            c.execute(
                """
                UPDATE players
                SET team_name = ?
                WHERE user_id = ?
                """,
                (team_name, str(interaction.user.id))
            )
            conn.commit()

    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed: teams.name" in str(e):
            await interaction.response.send_message(f"Team {team_name} already exists!", ephemeral=True)
        elif "UNIQUE constraint failed: teams.shorthandle" in str(e):
            await interaction.response.send_message(f"shorthandle {shorthandle} already exists", ephemeral=True)
        else:
            await interaction.response.send_message(f"Database integrity error: {e}")
        return
    
    guild = interaction.guild
    
    try:
        team_role = await guild.create_role(name=f"{team_name}", color=team_color, hoist=True, mentionable=True)
        captain_role = await guild.create_role(name=f"(C){team_name}", color=team_color)

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(
                """
                UPDATE teams SET teamrole_id = ?, captainrole_id = ? WHERE name = ?
                """,
                (str(team_role.id), str(captain_role.id), team_name)
            )
            conn.commit()

        await interaction.user.add_roles(captain_role)

    except discord.Forbidden:
        await interaction.response.send_message("I dont have permission to create roles",ephemeral=True)
        return
    except Exception as e:
        await interaction.response.send_message(f"Error creating roles: {e}",ephemeral=True)

    await interaction.response.send_message(f"**Team Created**\n{team_name} Created Successfully by {interaction.user}")

@bot.tree.command(name="removeteam", description="Permanently delete a team, its roles, and DB entry (Admin Only!)")
@app_commands.describe(team_name="Exact name of the team to delete")
async def removeteam(interaction: discord.Interaction, team_name: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("This command requiers **Administrator Privileges**", ephemeral=True)
        return
    
    team_name = team_name.strip()
    if not team_name:
        await interaction.response.send_message("Please provide a valid team name.", ephemeral=True)
        return
    
    guild = interaction.guild

    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT name FROM teams WHERE name = ?", (team_name,))
            if not c.fetchone():
                await interaction.response.send_message(f"Team **{team_name}** not found in database", ephemeral=True)
                return
            c.execute("DELETE FROM teams WHERE name = ?",(team_name,))
            c.execute("UPDATE players SET team_name = NULL WHERE team_name = ?", (team_name,))
            conn.commit()

    except Exception as e:
        await interaction.response.send_message(f"Database error: {e}",ephemeral=True)
        return
    
    deleted_count = 0
    for role in guild.roles:
        if team_name.lower() in role.name.lower():
            try:
                await role.delete(reason=f"Team {team_name} removed by {interaction.user}")
                deleted_count += 1
            except discord.Forbidden:
                await interaction.response.send_message(f"Couldn't delete role {role.name}, bot lacks permission", ephemeral=True)
            except Exception as e:
                print(f'Failed to delete role {role.name}: {e}')

    await interaction.response.send_message(f"**Team Removed**\nTeam {team_name}, have been permanently deleted by {interaction.user}")


@bot.tree.command(name="setcaptain", description="Set Captain for a Team")
@app_commands.choices(action=[
    app_commands.Choice(name="Add", value="add"),
    app_commands.Choice(name="Remove", value="remove")
])
async def setcaptain(interaction: discord.Interaction, member: discord.Member, role: discord.Role, action: str):
    if MATCH_ADMIN not in [r.id for r in interaction.user.roles]:
        await interaction.response.send_message("You do not have permission to use this command", ephemeral=True)
        return
    
    try:
        if action == "add":
            await member.add_roles(role)
            action_text = "added"

        else:
            await member.remove_roles(role)
            action_text = "removed"

        await interaction.response.send_message(f"{role.mention} has been {action_text} to {member.mention} by <@&"+str(MATCH_ADMIN)+">")

    except discord.Forbidden:
        await interaction.response.send_message("I dont have permission to manage that role.")

    except Exception as e:
        await interaction.response.send_message(f"Error: {e}", ephemeral=True)

@bot.tree.command(name="hello", description="Greets you back")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hello, {interaction.user.name}! I am Online :D")

@bot.tree.command(name="ping", description="Shows you the bot's latency")
async def ping(interaction: discord.Interaction, speed: str = "normal"):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"My latency is {latency}ms. Speed: {speed}")

@bot.event
async def setup_hook():
    TEST_GUILD = discord.Object(id=1459330017778729036)
    bot.tree.copy_global_to(guild=TEST_GUILD)

    try:
        synced = await bot.tree.sync(guild=TEST_GUILD)
        print(f"Succesfully synced {len(synced)} command(s) to guild!")
    except Exception as e:
        print(f"Failed to sync commands:", e)
    print("Commands synced to guild")

bot.run(os.getenv('BOT_TOKEN'))