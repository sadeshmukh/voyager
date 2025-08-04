two_player_config = {
    "main_rounds": 3,
}

multi_player_config = {
    "main_rounds": 5,
}

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
