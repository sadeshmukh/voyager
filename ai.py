import requests
from logging import getLogger

logger = getLogger("voyager_ai")


def verify(given_answer: str, correct_answer: str) -> bool:
    # return str([char.lower() for char in given_answer if char.isalpha()]) == str(
    #     [char.lower() for char in correct_answer if char.isalpha()]
    # )

    return verify_ai(given_answer, correct_answer)


def verify_ai(given_answer: str, correct_answer: str) -> bool:
    endpoint = "https://ai.hackclub.com/chat/completions"
    response = requests.post(
        endpoint,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": f"Is ```{given_answer}``` correct, if the correct answer is ```{correct_answer}```? Respond with only `yes` or `no`.",
                }
            ]
        },
    )
    answer = response.json()["choices"][0]["message"]["content"]
    if len(answer) > 3:
        logger.warning(f"AI response too long: {answer}")
    if "yes" in answer and "no" in answer:
        logger.error(
            f"AI response is ambiguous: {answer} and probably will cause issues! fix now!"
        )
    return "yes" in answer.lower()
