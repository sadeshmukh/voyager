import csv
import typing as t
import random
import requests
import logging
import html
from config import TRIVIA_CATEGORIES, RIDDLES_CSV_PATH


def get_trivia_question(category: str = "") -> t.Tuple[str, str]:
    try:
        if not category:
            category_info = random.choice(TRIVIA_CATEGORIES)
            category_id = category_info["id"]
        else:
            # assume it's an index into trivia_categories
            category_info = TRIVIA_CATEGORIES[int(category)]
            category_id = category_info["id"]

        for _ in range(5):
            response = requests.get(
                f"https://opentdb.com/api.php?amount=1&category={category_id}"
            )
            data = response.json()
            question = html.unescape(data["results"][0]["question"])
            answer = html.unescape(data["results"][0]["correct_answer"])

            if not question.lower().startswith(
                "which of the"
            ):  # filters "which of these" and "which of the following"
                return question, [answer]

        # when I start seeing these I know I screwed up
        return "What is the capital of France?", ["Paris"]
    except Exception:
        return "What is the capital of France?", ["Paris"]


def get_riddle() -> t.Tuple[str, str]:
    # couldn't find a decent api, so read from CSV
    # csv is in gitignore but source is here: https://github.com/crawsome/riddles/blob/main/riddles.csv
    try:
        with open(RIDDLES_CSV_PATH, "r", encoding="utf-8") as file:
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
