import os


two_player_config = {
    "main_rounds": 3,
}

multi_player_config = {
    "main_rounds": 5,
}

MAX_CHANNELS = 10
RESPONSE_TIME_THRESHOLDS = {
    "fast": 3,
    "medium": 8,
}

SERVER_DEFAULTS = {
    "max_channels": 10,
    "initialized": False,
}

SCORING = {
    "correct_answer_points": 10,
}

AI_ENDPOINT = "https://ai.hackclub.com/chat/completions"

ERROR_RESPONSE = {
    "server_only": "This command can only be used in a server!",
    "admin_required": "You need administrator permissions to use this command!",
    "max_channels_reached": f"Maximum number of game channels ({MAX_CHANNELS}) reached! Cannot create more channels.",
    "no_available_channels": "No available game channels! Use `/admin create` to create more channels.",
    "no_available_channels_guild": "No available game channels in {guild_name}. Use /admin create to create more.",
    "game_not_found": "Game not found.",
    "user_not_found": "User not found! ",
    "no_game_running": "No game is currently running in this channel.",
    "on_waitlist": "You're on the waitlist - an instance will be allocated soon!",
    "game_already_started": "Game already started.",
    "cannot_invite_started": "Cannot invite players - game has already started!",
    "cannot_invite_bots": "Cannot invite bots to the game!",
    "already_in_game": "{user_mention} is already in this game!",
    "no_active_game": "No active game in this channel!",
    "game_not_in_progress": "Game is not in progress!",
    "games_cannot_start_lobby": "Games cannot be started in the lobby channel!",
    "server_not_initialized": "Server not initialized! Server will auto-initialize when ready.",
    "need_at_least_2_players": "Need at least 2 players to start the game! Use the Invite button to invite more players.",
    "use_waitlist_in_lobby": "No game instance found in this channel! Use `/waitlist` in the lobby to join a game.",
    "already_in_waitlist": "You're already in the waitlist! (Position #{position})",
    "command_lobby_only": "This command can only be used in {lobby_mention}!",
    "purging_lobby": "Purging lobby channel...",
    "failed_create_channel": "Failed to create game channel! Bot may lack permissions.",
    "failed_purge_lobby": "Failed to purge lobby channel! Bot may lack permissions.",
}

TRIVIA_CATEGORIES = [
    {"id": 9, "name": "General Knowledge"},
    {"id": 10, "name": "Entertainment: Books"},
    {"id": 11, "name": "Entertainment: Film"},
    {"id": 12, "name": "Entertainment: Music"},
    {"id": 13, "name": "Entertainment: Musicals & Theatres"},
    {"id": 14, "name": "Entertainment: Television"},
    {"id": 15, "name": "Entertainment: Video Games"},
    {"id": 16, "name": "Entertainment: Board Games"},
    {"id": 17, "name": "Science & Nature"},
    {"id": 18, "name": "Science: Computers"},
    {"id": 19, "name": "Science: Mathematics"},
    {"id": 20, "name": "Mythology"},
    {"id": 21, "name": "Sports"},
    {"id": 22, "name": "Geography"},
    {"id": 23, "name": "History"},
    {"id": 24, "name": "Politics"},
    {"id": 25, "name": "Art"},
    {"id": 26, "name": "Celebrities"},
    {"id": 27, "name": "Animals"},
    {"id": 28, "name": "Vehicles"},
    {"id": 29, "name": "Entertainment: Comics"},
    {"id": 30, "name": "Science: Gadgets"},
    {"id": 31, "name": "Entertainment: Japanese Anime & Manga"},
    {"id": 32, "name": "Entertainment: Cartoon & Animations"},
]

DEFAULT_LIVES = 3
DEFAULT_TIME_LIMIT = 30

RIDDLES_CSV_PATH = "riddles.csv"

host_dialogue = {  # TODO: actually fix dialogue (thank chatgpt for filler)
    "intro": [
        "Welcome!",
        "I'm your host, and I'm excited to see how you all perform!",
        "Let's see who can score the most points!",
    ],
    "main_round": [
        "Time for another round of challenges!",
        "Don't worry, everyone learns from mistakes!",
        "Let's see those answers!",
    ],
    "final_results": [
        "Time to see who performed best in this challenge!",
        "Let's see those final scores!",
        "May the highest score win!",
    ],
    "outro": [
        "That's a wrap on tonight's challenge!",
        "Thanks for playing, and great job everyone!",
        "See you next time!",
    ],
}

dialogue_timing = {
    "default_wait": 2.0,
    "short_wait": 1.0,
    "long_wait": 3.0,
    "intro": 2.5,
    "main_round": 1.5,
    "final_results": 3.0,
    "outro": 2.0,
}

DEBUG_MODE = os.environ.get("DEBUG", "false").lower() == "true"
