import csv
import typing as t
import random
import requests
import logging

trivia_categories = [
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


def get_trivia_question(category: str = "") -> t.Tuple[str, str]:
    try:
        if not category:
            category_info = random.choice(trivia_categories)
            category_id = category_info["id"]
        else:
            # assume it's an index into trivia_categories
            category_info = trivia_categories[int(category)]
            category_id = category_info["id"]

        response = requests.get(
            f"https://opentdb.com/api.php?amount=1&category={category_id}"
        )
        data = response.json()
        question = data["results"][0]["question"]
        answer = data["results"][0]["correct_answer"]
        return question, [answer]  # for multiple correct answers
    except Exception:
        return "What is the capital of France?", ["Paris"]


def get_riddle() -> t.Tuple[str, str]:
    # couldn't find a decent api, so read from CSV
    # csv is in gitignore but source is here: https://github.com/crawsome/riddles/blob/main/riddles.csv
    try:
        with open("riddles.csv", "r", encoding="utf-8") as file:
            reader = csv.reader(file)
            riddle = random.choice(list(reader))
        answer = riddle[1]
        riddle = riddle[0]
        return riddle, answer
    except FileNotFoundError:
        return (
            "What has keys but no locks, space but no room, and you can enter but not go inside?",
            "A keyboard",
        )
    except Exception:
        return "What gets wetter as it dries?", "A towel"


# https://api.dictionaryapi.dev/api/v2/entries/en/<word>


def get_definition(word: str, lang: str = "en") -> str:
    response = requests.get(
        f"https://api.dictionaryapi.dev/api/v2/entries/{lang}/{word}"
    )
    data = response.json()
    return data[0]["meanings"][0]["definitions"][0]["definition"]


def is_word_valid(word: str, lang: str = "en") -> bool:
    try:
        get_definition(word, lang)
        return True
    except Exception:
        return False


def purge_channel_messages(
    app, channel_id: str, user_filter: str = None
) -> t.Dict[str, t.Any]:
    """
    Purge all messages from a channel, optionally filtered by user.

    Args:
        app: Slack app instance
        channel_id: ID of the channel to purge
        user_filter: Optional user ID to filter messages (only delete messages from this user)

    Returns:
        Dict with success status and message count
    """
    logger = logging.getLogger("voyager")
    deleted_count = 0
    errors = []

    try:
        cursor = None
        while True:
            result = app.client.conversations_history(
                channel=channel_id, limit=100, cursor=cursor
            )

            messages = result.get("messages", [])
            if not messages:
                break

            if user_filter:
                messages = [msg for msg in messages if msg.get("user") == user_filter]

            for message in messages:
                try:
                    app.client.chat_delete(channel=channel_id, ts=message["ts"])
                    deleted_count += 1
                except Exception as e:
                    error_msg = f"Failed to delete message {message['ts']}: {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)

            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

    except Exception as e:
        error_msg = f"Failed to fetch messages from channel {channel_id}: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)

    return {
        "success": len(errors) == 0,
        "deleted_count": deleted_count,
        "errors": errors,
    }
