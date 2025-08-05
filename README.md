# Voyager

A dynamic party game bot for Discord! 

## Too short, didn't read?

Voyager creates dynamic party game instances in Discord! Players compete in various mini-games like quick math, trivia, speed challenges, and word games. Failed players face elimination rounds with different challenges. It's very low friction - just /waitlist, and you're in!

## Game Types

- **Quick Math**: Fast arithmetic challenges
- **Trivia**: Knowledge-based questions  
- **Speed Challenge**: First to respond wins
- **Text Modification**: Word puzzles and transformations
- **Memory Game**: Remember sequences and patterns
- **Emoji Challenge**: Find emojis containing specific letters
- **Riddles**: Brain teasers and logic puzzles
- **Collaborative**: Team-based challenges

## How It Works

### Example Session
```
GAME CHOSEN: Quick Math!
What's 47 + 83?
Time limit: 8 seconds

[Players submit answers...]

Results:
✅ Correct: @player1 @player2  
❌ Incorrect/No Answer: @player3 @player4

🏆 Live Leaderboard:
@player1: **15** pts
@player2: **12** pts
```

## Discord Setup

1. Create a Discord application at https://discord.com/developers/applications
2. Create a bot for your application and get the bot token
3. Enable necessary bot permissions:
   - Send Messages
   - Use Slash Commands
   - Manage Channels
   - Manage Roles
   - Read Message History
4. Enable the MEMBERS and MESSAGE_CONTENT intents in the Discord Developer Portal
5. Set up environment variables in `.env`:

```env
DISCORD_BOT_TOKEN=your-discord-bot-token
ADMIN_ID=your-discord-user-id
LOBBY_CHANNEL_ID=your-lobby-channel-id
```

6. Install dependencies:

```bash
uv pip install -r pyproject.toml
```

7. Invite the bot to your server with appropriate permissions

## Usage

Run the bot:

```bash
uv run discord.py
```

### Commands

**Lobby Commands:**
- `/waitlist` - Join the queue for a game
- `/state` - Check game/queue status

**Game Instance Commands:**
- `/start` - Start a game in an instance
- `/next-round` - Start next main round (random game type)
- `/state` - Check detailed game status

### Game Flow

1. Players join the waitlist in the lobby channel
2. They get assigned to a game channel when enough players are ready
3. Rounds... score... points...
4. get more points!


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
