# Voyager

A dynamic party game bot for Discord! 
Try it out here: https://discord.gg/UWpehz7ttb

## Too short, need more?

Voyager creates dynamic party game instances in Discord! Players compete in various mini-games like quick math, trivia, speed challenges, and word games. Failed players face elimination rounds with different challenges. It's very low friction - just /waitlist, and you're in!

The following game types are available: quickmath, trivia, speed, textmod, memory, emoji, riddle

## How It Works

### Example Session
```
Player uses /waitlist in #voyager-lobby
-> Gets assigned to #v-inst-awesome-game
-> Invites friends by @mentioning them in lobby

Game starts after clicking "Start" in the instance channel:
"Alright, let's do this!"

Round 1 - Quick Math:
What's 47 + 83?
Time limit: 8 seconds

[Players type answers in chat...]

Results:
Correct: @player1 (18 pts) @player2 (10 pts)  
Incorrect/No Answer: @player3 @player4

Leaderboard:
@player1: **18** pts
@player2: **10** pts
@player3: **0** pts
@player4: **0** pts

[Game continues for 15-20 rounds with different game types]
```

## Discord Setup

1. Create a Discord application at https://discord.com/developers/applications
2. Create a bot for your application and get the bot token
3. Enable necessary bot permissions:
   - **Send Messages** - For game communication
   - **Read Messages** - To read player responses
   - **Manage Channels** - To create and manage game channels
   - **Manage Messages** - To delete messages and purge channels
   - **Embed Links** - For rich game embeds and leaderboards
   - **Manage Roles** - To create and assign game roles
4. Enable the **MESSAGE_CONTENT** intent in the Discord Developer Portal
5. Set up environment variables in `.env`:

```env
DISCORD_BOT_TOKEN=your-discord-bot-token
DISCORD_ADMIN_ID=your-discord-user-id

# AI Configuration (optional)
AI_PROVIDER=hackclub  # or "ollama"
OLLAMA_ENDPOINT=http://localhost:11434  # only needed if using ollama
OLLAMA_MODEL=llama2  # only needed if using ollama
```

6. Install dependencies:

```bash
uv pip install -r pyproject.toml
```

7. Invite the bot to your server with appropriate permissions

## AI Configuration

The bot uses AI to verify answers for certain game types. You can configure which AI provider to use. Unfortunately, Hack Club doesn't offer a non-thinking variant, leading to inconsistent answers and token overuse.

For that reason, I currently recommend using Ollama.

### Ollama (Local)
To use Ollama for answer verification:

1. Install and run Ollama: https://ollama.com/
2. Pull a model: `ollama pull gemma2:2b`
3. Set environment variables:
   ```env
   AI_PROVIDER=ollama
   OLLAMA_ENDPOINT=http://localhost:11434
   OLLAMA_MODEL=gemma2:2b
   ```

(I already had gemma2:2b installed, and it was about the lightest model I could find that was still good at verifying yes/no)

## Usage

Run the bot:

```bash
uv run discord.py
```

### Commands

**Lobby Commands:**
- `/waitlist` - Join the queue for a game
- `/state` - Check game/queue status

**Game Instance Commands:** (not necessary)
- `/start` - Start a game in an instance
- `/state` - Check detailed game status

**Server Commands:**
- `/server create` - Create game channels (requires Administrator permissions on server)
- `/server config` - Configure server settings (requires Administrator permissions on server)

**Admin Commands:**
- `/admin create` - Create game channels (requires ADMIN_ID)
- `/admin config` - Configure server settings (requires ADMIN_ID)

### Game Flow

1. Players join the waitlist in the lobby channel
2. They get assigned to a game channel on their own and can invite others to join
3. Games run for 15-20 rounds 
4. Players compete in various mini-games
5. Final scores and winner announcement

## Adding New Game Types

The system is designed to be extensible! To add a new game type:

1. Add to `GameType` enum in `instance.py`
2. Add case in `generate_challenge()` function in `cogs/game.py`
3. Optionally customize evaluation logic in `evaluate_current_challenge()`

Example:
```python
class GameType(Enum):
    # ... existing types ...
    RIDDLE = "riddle"

# In generate_challenge():
elif game_type == GameType.RIDDLE:
    riddle, answer = get_riddle()
    return Challenge(
        challenge_type=game_type,
        question=riddle,
        correct_answer=[answer],
        time_limit=30
    )
```

---

## ARCHIVE: Slack Support

### Original Slack Setup

1. Create a Slack app at https://api.slack.com/apps with appropriate permissions
2. Enable Socket Mode and install the app - make sure to add commands through the app settings
3. Create a channel for the game lobby
4. Set up environment variables in `.env`:

```env
SLACK_BOT_TOKEN=xoxb-your-token
SLACK_SIGNING_SECRET=your-signing-secret
SLACK_APP_TOKEN=xapp-your-token
ADMIN_ID=U0123456789
LOBBY_CHANNEL_ID=C0123456789
```

### Slack Commands

**Lobby Commands:**
- `/waitlist` - Join the queue for a game
- `/state` - Check game/queue status

**Game Instance Commands:**
- `/start` - Start a game in an instance
- `/next-round` - Start next main round (random game type)
- `/game-type [type]` - Start round with specific game type
- `/elimination` - Start elimination round for at-risk players
- `/answer [response]` - Submit your answer
- `/evaluate` - Evaluate current challenge and show results
- `/invitevoyage` - Invite others to your game instance
- `/state` - Check detailed game status

**Admin Commands:**
- `/admin-create [name]` - Create new game instance

### Game Types Available:
- `quick_math` - Fast arithmetic 
- `trivia` - Knowledge questions
- `speed_challenge` - First response wins
- `word_game` - Word puzzles
- `collaborative` - Team challenges

For development reference, check out the [Slack Bolt Python docs](https://slack.dev/bolt-python/concepts).
