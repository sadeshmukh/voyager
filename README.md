# Voyager

A Slack bot for interactive party games within Slack (or Discord)?

why does setuptools want to be a pain

## Too short, didn't read?

Voyager creates dynamic party game instances in Slack! Players compete in various mini-games like quick math, trivia, speed challenges, and word games. Failed players face elimination rounds with different challenges. The system is designed to be extensible - adding new game types is easy!

## Game Types

- **Quick Math**: Fast arithmetic challenges
- **Trivia**: Knowledge-based questions  
- **Speed Challenge**: First to respond wins
- **Word Game**: Letter counting and word puzzles
- **Collaborative**: (not implemented yet)
- **Memory Game**: Coming soon!

## How It Works

### Example Session
```
GAME CHOSEN: Quick Math!
What's 47 + 83?
Time limit: 10 seconds

[Players submit answers...]

Results:
Correct: @a1 @b2  
At Risk: @c3 @d4

ELIMINATION ROUND
Players at risk: @c3 @d4
Speed Challenge: First to respond wins!
Failure means elimination!
```

## Setup

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

5. Install dependencies:

```bash
uv pip install -r pyproject.toml
```

## Usage

Run the bot:

```bash
uv run app.py
```

### Commands

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

## Adding New Game Types

The system is designed to be extensible! To add a new game type:

1. Add to `GameType` enum in `instance.py`
2. Add case in `_generate_challenge()` method
3. Optionally customize evaluation logic in `evaluate_current_challenge()`

Example:
```python
class GameType(Enum):
    # ... existing types ...
    RIDDLE = "riddle"

# In _generate_challenge():
elif game_type == GameType.RIDDLE:
    riddles = [
        ("I have keys but no locks. What am I?", ["piano", "keyboard"]),
        # ... more riddles
    ]
    riddle, answers = random.choice(riddles)
    return Challenge(
        challenge_type=game_type,
        question=riddle,
        correct_answer=answers,
        time_limit=30
    )
```

## Development

Check out the [Slack Bolt Python docs](https://slack.dev/bolt-python/concepts) for framework details.

### Architecture

- **`instance.py`**: Core game logic, extensible challenge system
    - ^^ independent of slack logic ^^
- **`app.py`**: Slack bot commands and event handling  
- **Game Flow**: INTRO → MAIN_ROUND → ELIMINATION → OUTRO
- **Player States**: ACTIVE → AT_RISK → ELIMINATED/WINNER
