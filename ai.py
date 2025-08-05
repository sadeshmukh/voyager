import requests
import os
from logging import getLogger
from config import AI_ENDPOINT

logger = getLogger("voyager_ai")

AI_PROVIDER = os.getenv("AI_PROVIDER", "hackclub")  # "hackclub" or "ollama"
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma2:2b")


def verify(given_answer: str, correct_answer: str) -> bool:
    # return str([char.lower() for char in given_answer if char.isalpha()]) == str(
    #     [char.lower() for char in correct_answer if char.isalpha()]
    # )

    return verify_ai(given_answer, correct_answer)


def verify_ai(given_answer: str, correct_answer: str) -> bool:
    if AI_PROVIDER == "ollama":
        return verify_ollama(given_answer, correct_answer)
    return verify_hackclub(given_answer, correct_answer)


def verify_hackclub(given_answer: str, correct_answer: str) -> bool:
    endpoint = AI_ENDPOINT
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


def verify_ollama(given_answer: str, correct_answer: str) -> bool:
    """Verify answer using Ollama endpoint"""
    endpoint = f"{OLLAMA_ENDPOINT}/api/generate"
    prompt = f"Is ```{given_answer}``` correct, if the correct answer is ```{correct_answer}```? Respond with only `yes` or `no`."

    try:
        response = requests.post(
            endpoint,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "top_p": 0.9, "max_tokens": 10},
            },
            timeout=10,
        )
        response.raise_for_status()

        answer = response.json()["response"].strip()
        if len(answer) > 3:
            logger.warning(f"Ollama response too long: {answer}")
        if "yes" in answer and "no" in answer:
            logger.error(
                f"Ollama response is ambiguous: {answer} and probably will cause issues! fix now!"
            )
        return "yes" in answer.lower()

    except requests.exceptions.RequestException as e:
        logger.error(f"Ollama request failed: {e}")
        return given_answer.lower().strip() == correct_answer.lower().strip()
    except (KeyError, ValueError) as e:
        logger.error(f"Ollama response PARSING failed: {e}")
        return given_answer.lower().strip() == correct_answer.lower().strip()
