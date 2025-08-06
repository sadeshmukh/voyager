import os


two_player_config = {
    "main_rounds": 15,
}

multi_player_config = {
    "main_rounds": 20,
}

MAX_CHANNELS = 10
RESPONSE_TIME_THRESHOLDS = {
    "fast": 3,
    "medium": 8,
}

SERVER_DEFAULTS = {
    "max_channels": 10,
    "initialized": False,
    "hoist_roles": True,
    "rounds_per_game": 15,
    "role_color": "blue",
    "default_time_limit": 30,
    "min_players_to_start": 2,
    "max_players_per_game": 8,
    "waitlist_timeout": 5,
    "enable_speed_bonus": True,
    "enable_first_answer_bonus": True,
    "game_types_enabled": "quick_math,trivia,speed_challenge,text_modification,memory,emoji,riddles,collaborative",
}

SERVER_CONFIG_OPTIONS = {
    "hoist_roles": {
        "type": "bool",
        "description": "Whether to hoist game roles in the member list",
        "default": True,
        "choices": ["true", "false"] if "choices" in globals() else None,
    },
    "rounds_per_game": {
        "type": "int",
        "description": "Number of rounds per game (max 25)",
        "default": 15,
        "min": 5,
        "max": 25,
    },
    "role_color": {
        "type": "str",
        "description": "Color for game roles",
        "default": "blue",
        "choices": [
            "blue",
            "green",
            "red",
            "yellow",
            "purple",
            "orange",
            "pink",
            "teal",
        ],
    },
    "max_channels": {
        "type": "int",
        "description": "Maximum number of game channels allowed (3-10)",
        "default": 10,
        "min": 3,
        "max": 10,
    },
    "default_time_limit": {
        "type": "int",
        "description": "Default time limit for challenges in seconds (max 60)",
        "default": 30,
        "min": 10,
        "max": 60,
    },
    "min_players_to_start": {
        "type": "int",
        "description": "Minimum players required to start a game (max 10)",
        "default": 2,
        "min": 1,
        "max": 10,
    },
    "max_players_per_game": {
        "type": "int",
        "description": "Maximum players allowed per game (max 20)",
        "default": 8,
        "min": 2,
        "max": 20,
    },
    "waitlist_timeout": {
        "type": "int",
        "description": "Minutes to wait before starting game with fewer players (max 30)",
        "default": 5,
        "min": 1,
        "max": 30,
    },
    "enable_speed_bonus": {
        "type": "bool",
        "description": "Whether to award speed bonuses for fast answers",
        "default": True,
    },
    "enable_first_answer_bonus": {
        "type": "bool",
        "description": "Whether to award bonus points for first correct answer",
        "default": True,
    },
    "game_types_enabled": {
        "type": "str",
        "description": "Comma-separated list of enabled game types",
        "default": "quick_math,trivia,speed_challenge,text_modification,memory,emoji,riddles,collaborative",
        "choices": [
            "quick_math,trivia,speed_challenge,text_modification,memory,emoji,riddles,collaborative",
            "quick_math,trivia,speed_challenge",
            "trivia,riddles",
            "quick_math,speed_challenge,text_modification",
        ],
    },
}

SCORING = {
    "correct_answer_points": 10,
    "speed_bonus_points": 5,
    "first_answer_bonus": 3,
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

host_dialogue = {
    "intro": [
        "Alright, let's do this!",
        "Time to see what you've got...",
        "Who's gonna win this one?",
    ],
    "main_round": [
        "Next round!",
        "Here we go again...",
        "I'm ready for the next answers - are you?",
    ],
    "final_results": [
        "Final scores coming up...",
        "And the winner is...",
        "Here's how everyone did:",
    ],
    "outro": [
        "Good game everyone!",
        "GGs",
        "That was fun!",
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

GAME_NAME_ADJECTIVES = [
    "Epic",
    "Mysterious",
    "Golden",
    "Cosmic",
    "Legendary",
    "Hidden",
    "Ancient",
    "Magical",
    "Swift",
    "Brave",
    "Clever",
    "Wild",
    "Silent",
    "Bright",
    "Dark",
    "Fierce",
    "Gentle",
    "Wise",
    "Quick",
    "Strong",
    "Calm",
    "Bold",
    "Shiny",
    "Rare",
]

GAME_NAME_NOUNS = [
    "Quest",
    "Adventure",
    "Journey",
    "Challenge",
    "Mission",
    "Voyage",
    "Expedition",
    "Trial",
    "Test",
    "Battle",
    "Race",
    "Hunt",
    "Discovery",
    "Exploration",
    "Puzzle",
    "Mystery",
    "Treasure",
    "Legend",
    "Tale",
    "Story",
    "Saga",
    "Chronicle",
    "Odyssey",
]

ROLE_NAME_FRUITS = [
    "Apple",
    "Banana",
    "Orange",
    "Grape",
    "Strawberry",
    "Peach",
    "Mango",
    "Pineapple",
    "Kiwi",
    "Blueberry",
    "Cherry",
    "Pear",
    "Coconut",
    "Lemon",
    "Watermelon",
]

SPEED_CHALLENGE_PROMPTS = [
    "Type: SPEED",
    "Type: SECOND",
    "Type: DASH",
    "Type: ZOOM",
    "Type 'I LOSE' to win this round!",
    "Type: SAHIL THE GOAT",
]

TEXT_MODIFICATION_WORDS = [
    "hello",
    "voyager",
    "discord",
    "gaming",
    "python",
    "challenge",
    "quizzer",
]

TEXT_MODIFICATION_TYPES = [
    "reverse",
    "alternating_case",
]

MATH_OPERATIONS = [  # yes this is overcomplicated. no I do not care
    ("+", "add"),
    ("-", "subtract"),
    ("×", "multiply"),
    ("÷", "divide"),
]
