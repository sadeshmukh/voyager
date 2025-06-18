# Voyager

A Slack bot with interactive game instances within Slack channels.

## Too short, didn't read?

Voyager is a Slack bot that allows you to create interactive game instances within Slack channels. It relies on reusing existing private instance channels for games. What games? I'm not entirely sure yet!

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

- `/waitlist` - Join the queue for a game
- `/state` - Check game/queue status
- `/start` - Start a game in an instance
- `/invitevoyage` - Invite others to your game instance
- `/admin-create` - Create new game instance (admin only)

## Development

Check out the [Slack Bolt Python docs](https://slack.dev/bolt-python/concepts) for framework details.
