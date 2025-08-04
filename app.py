import datetime
import os
import sys
import time
import threading
import html
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from instance import (
    Instance,
    GameState,
    # GamePhase,
    PlayerState,
    GameType,
    GameConfig,
    Challenge,
)

# import yaml
from utils import purge_channel_messages, get_trivia_question, get_riddle
from config import (
    two_player_config,
    multi_player_config,
    host_dialogue,
    dialogue_timing,
)
import random

DEBUG = (
    "--debug" in sys.argv
    or "--dev" in sys.argv
    or os.environ.get("DEBUG", "false").lower() == "true"
)
logger = logging.getLogger("voyager")
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


load_dotenv()


ADMIN_ID = os.environ.get("ADMIN_ID")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID environment variable must be set")

LOBBY_CHANNEL_ID = os.environ.get("LOBBY_CHANNEL_ID")
if not LOBBY_CHANNEL_ID:
    raise ValueError("LOBBY_CHANNEL_ID environment variable must be set")

app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET"),
)

BOT_ID = app.client.auth_test()["user_id"]

CURRENTLY_WAITING = []  # [user_id, ...]
INSTANCES = {}  # channel_id: Instance
ROUND_TIMERS = {}  # channel_id: Timer

scheduler = BackgroundScheduler()


def generate_challenge(game_type: GameType) -> Challenge:
    """Generate challenge for a given game type - Slack-specific implementation"""
    if game_type == GameType.QUICK_MATH:
        a, b = random.randint(10, 99), random.randint(10, 99)
        return Challenge(
            challenge_type=game_type,
            question=f"What's {a} + {b}?",
            correct_answer=str(a + b),
            time_limit=10,
        )

    elif game_type == GameType.SPEED_CHALLENGE:
        return Challenge(
            challenge_type=game_type,
            question="First to respond wins! Type ANYTHING and hit enter!",
            correct_answer=None,
            time_limit=5,
            metadata={"speed_based": True},
        )

    elif game_type == GameType.TRIVIA:
        question, answers = get_trivia_question()
        return Challenge(
            challenge_type=game_type,
            question=question,
            correct_answer=answers,
            time_limit=20,
        )

    elif game_type == GameType.RIDDLE:
        riddle, answer = get_riddle()
        return Challenge(
            challenge_type=game_type,
            question=riddle,
            correct_answer=answer,
            time_limit=15,
        )

    else:
        return Challenge(
            challenge_type=GameType.TRIVIA,
            question="What's 2 + 2?",
            correct_answer="4",
            time_limit=10,
        )


def send_host_message(channel_id: str, dialogue_key: str):
    """Send host dialogue to Slack channel"""
    wait_time = dialogue_timing.get(
        dialogue_key, dialogue_timing.get("default_wait", 2.0)
    )
    dialogue_lines = host_dialogue.get(dialogue_key, [])

    for line in dialogue_lines:
        try:
            app.client.chat_postMessage(channel=channel_id, text=f"**HOST:** {line}")
            time.sleep(wait_time)
        except Exception as e:
            logger.error(f"Failed to send host message: {e}")


def create_game_config(player_count: int) -> GameConfig:
    """Create game configuration based on player count"""
    if player_count <= 2:
        main_rounds = two_player_config["main_rounds"]
    elif player_count >= 5:
        main_rounds = multi_player_config["main_rounds"]
    else:
        main_rounds = 5

    return GameConfig(player_count=player_count, main_rounds=main_rounds)


def auto_evaluate_round(channel_id: str):
    """Auto-evaluate the current round after time limit"""
    instance = INSTANCES.get(channel_id)
    if (
        not instance
        or instance.state != GameState.IN_PROGRESS
        or not instance.current_challenge
    ):
        return

    try:
        results = instance.evaluate_current_challenge()

        if "error" in results:
            logger.error(f"Auto-evaluation error in {channel_id}: {results['error']}")
            return

        message_parts = [
            f"**Time's up! Results for {results['challenge_type'].replace('_', ' ').title()}**\n"
        ]

        # Show the correct answer
        if instance.current_challenge and instance.current_challenge.correct_answer:
            correct_answer = instance.current_challenge.correct_answer
            if isinstance(correct_answer, list):
                answer_text = " / ".join([html.unescape(str(ans)) for ans in correct_answer])
            else:
                answer_text = html.unescape(str(correct_answer))
            message_parts.append(f"**Correct Answer:** {answer_text}")

        if results["correct_players"]:
            correct_mentions = " ".join(
                [f"<@{uid}>" for uid in results["correct_players"]]
            )
            message_parts.append(f"**Correct:** {correct_mentions}")

        if results["failed_players"]:
            failed_mentions = " ".join(
                [f"<@{uid}>" for uid in results["failed_players"]]
            )
            message_parts.append(f"**Incorrect/No Answer:** {failed_mentions}")

        game_state = instance.get_game_state()
        rounds_left = instance.config.main_rounds - instance.current_round
        message_parts.append(
            f"\n**Current Status:**\nActive players: {game_state['active_players']}\nRounds left: {rounds_left}"
        )

        app.client.chat_postMessage(channel=channel_id, text="\n".join(message_parts))

        # check for end state
        if instance.current_round >= instance.config.main_rounds:
            final_results = instance.end_game(success=True)
            send_host_message(channel_id, "final_results")

            winners_text = ", ".join([f"<@{uid}>" for uid in final_results["winners"]])
            app.client.chat_postMessage(
                channel=channel_id,
                text=f"**Game Complete!**\n\nWinners: {winners_text}",
            )

            send_host_message(channel_id, "outro")
        else:
            # schedule next round
            def start_next():
                send_host_message(channel_id, "main_round")
                challenge = instance.start_main_round()
                app.client.chat_postMessage(
                    channel=channel_id,
                    text=f"**Round {instance.current_round} - {challenge.challenge_type.value.replace('_', ' ').title()}**\n\n"
                    f"**{challenge.question}**\n\n"
                    f"Time limit: {challenge.time_limit} seconds\n"
                    f"Just type your answer in the chat!",
                )
                # schedule autoeval
                timer = threading.Timer(
                    challenge.time_limit + 2, lambda: auto_evaluate_round(channel_id)
                )
                ROUND_TIMERS[channel_id] = timer
                timer.start()

            timer = threading.Timer(3, start_next)
            ROUND_TIMERS[channel_id] = timer
            timer.start()

    except Exception as e:
        logger.error(f"Auto-evaluation failed in {channel_id}: {e}")
        app.client.chat_postMessage(
            channel=channel_id, text=f"Auto-evaluation failed: {e}"
        )


def create_instance_with_dialogue(channel_id: str, name: str) -> Instance:
    """Create a new instance with framework-specific setup"""
    instance = Instance(channel_id, name)
    instance.set_challenge_generator(generate_challenge)
    return instance


def create_instance_channel(name: str) -> str:
    """Create a new private channel for an instance - do not use dynamically unless controlled!
    Use the admin-create command instead."""
    channel_name = f"v-inst-{name}"
    result = app.client.conversations_create(name=channel_name, is_private=True)
    channel_id = result["channel"]["id"]
    return channel_id


@app.command("/admin-delmessage")
def admin_delmessage(ack, command, respond):
    """Delete a message from the channel using a Slack message link"""
    ack()

    if not command["user_id"] == ADMIN_ID:
        respond("You are not authorized to use this command", response_type="ephemeral")
        return

    message_link = command["text"].strip()
    if not message_link:
        respond(
            "No link provided",
            response_type="ephemeral",
        )
        return

    try:
        # https://hackclub.slack.com/archives/[channel_id]/p[timestamp]
        parts = message_link.split("/")
        channel_id = parts[-2]
        ts = parts[-1][1:]  # grab timestamp
        ts = f"{ts[:-6]}.{ts[-6:]}"  # insert decimal point
        logger.debug(
            f"Deleting message from {channel_id} at {datetime.datetime.fromtimestamp(int(ts))}"
        )

        app.client.chat_delete(channel=channel_id, ts=ts)
        respond(f"Deleted message from {message_link}", response_type="ephemeral")
    except (IndexError, ValueError):
        respond("Invalid message link format.", response_type="ephemeral")
    except Exception as e:
        respond(f"Deletion failed: {str(e)}", response_type="ephemeral")


@app.command("/admin-create")
def admin_create_instance(ack, command, say):
    """Manually create a new instance channel - admin only"""
    ack()
    name = command["text"]
    channel_id = create_instance_channel(name)
    say(f"Created instance {name} in channel {channel_id}")
    INSTANCES[channel_id] = create_instance_with_dialogue(channel_id, name)
    app.client.conversations_invite(channel=channel_id, users=[ADMIN_ID, BOT_ID])


@app.command("/admin-purge")
def admin_purge_instance(ack, command, respond):
    """Purge all messages from the current instance channel - admin only"""
    ack()

    if not command["user_id"] == ADMIN_ID:
        respond("You are not authorized to use this command", response_type="ephemeral")
        return

    channel_id = command["channel_id"]
    user_filter = command["text"].strip() if command["text"].strip() else None

    if channel_id not in INSTANCES:
        respond(
            "This command can only be used in an instance channel",
            response_type="ephemeral",
        )
        return

    try:
        result = purge_channel_messages(app, channel_id, user_filter)

        if result["success"]:
            respond(
                f"Successfully purged {result['deleted_count']} messages from channel",
                response_type="ephemeral",
            )
        else:
            error_summary = "\n".join(result["errors"][:5])  # show first 5 errors
            respond(
                f"Purged {result['deleted_count']} messages with {len(result['errors'])} errors:\n{error_summary}",
                response_type="ephemeral",
            )
    except Exception as e:
        respond(f"Failed to purge messages: {str(e)}", response_type="ephemeral")


def process_waitlist():
    """Auto-assign users in the waitlist to instances"""
    if not CURRENTLY_WAITING:
        logger.debug("No users in waitlist")
        return

    logger.info(f"Processing waitlist with {len(CURRENTLY_WAITING)} users")

    try:
        if len(CURRENTLY_WAITING) >= 1:
            # utilize existing instances - if none available, keep waiting
            for user in CURRENTLY_WAITING:
                for channel_id, instance in INSTANCES.items():
                    if instance.state == GameState.WAITING:
                        instance.add_player(user)
                        try:
                            app.client.conversations_invite(
                                channel=channel_id, users=[user]
                            )
                            app.client.chat_postMessage(
                                channel=channel_id,
                                text=f"Welcome <@{user}>! You can invite others to join this game using `/invitevoyage`",
                            )
                        except Exception as e:
                            error_str = str(e)
                            if "already_in_channel" not in error_str:
                                logger.error(
                                    f"Failed to invite user {user} to channel {channel_id}: {e}"
                                )
                                # alerady_in_channel should only occur if it's admin user - this might be a footgun
                                raise
                        CURRENTLY_WAITING.remove(user)
                        break

    except Exception as e:
        logger.error(f"Failed to process waitlist: {e}")


scheduler.add_job(
    process_waitlist,
    trigger=IntervalTrigger(seconds=30),  # TODO: configurable
    id="process_waitlist",
    name="Process users in waitlist",
    replace_existing=True,
)


@app.command("/start")
def start_game(ack, command, say, respond):
    """Start a game in the current instance"""
    ack()
    channel_id, _ = command["channel_id"], command["user_id"]

    instance = INSTANCES.get(channel_id, None)
    if not instance:
        respond(
            "This command can only be used in an instance.", response_type="ephemeral"
        )
        return

    if instance.state != GameState.WAITING:
        respond("The game has already started!", response_type="ephemeral")
        return

    try:
        config = create_game_config(len(instance.players))
        game_state = instance.start_game(config)

        send_host_message(channel_id, "intro")

        say(
            f"**Game Starting!**\n\n"
            f"Use `/next-round` to begin the first challenge!\n"
            f"After that, rounds will progress automatically.\n\n"
            f"**Game Status:** {game_state}"
        )
    except Exception as e:
        respond(f"Failed to start game: {e}", response_type="ephemeral")


@app.message("")
def handle_message(message, say):
    """Handle natural chat messages as answers and start commands"""
    user_id = message["user"]
    channel_id = message["channel"]
    text = message.get("text", "").strip().lower()

    logging.debug("received message")

    if user_id == BOT_ID or not text:
        return

    # only process messages in instance channels
    instance = INSTANCES.get(channel_id)
    if not instance:
        return

    if text == "start":
        if instance.state != GameState.WAITING:
            say("The game has already started!", thread_ts=message["ts"])
            return

        try:
            config = create_game_config(len(instance.players))
            game_state = instance.start_game(config)

            send_host_message(channel_id, "intro")

            say(
                f"**Game Starting!**\n\n"
                f"Use `/next-round` to begin the first challenge!\n"
                f"After that, rounds will progress automatically.\n\n"
                f"**Game Status:** {game_state}"
            )
        except Exception as e:
            say(f"Failed to start game: {e}", thread_ts=message["ts"])
        return

    if instance.state != GameState.IN_PROGRESS or not instance.current_challenge:
        return

    # only accept answers from active players
    player = instance.players.get(user_id)
    if not player or player.state != PlayerState.ACTIVE:
        return

    instance.submit_answer(user_id, text)

    try:
        app.client.reactions_add(
            channel=channel_id, timestamp=message["ts"], name="white_check_mark"
        )
    except Exception as e:
        logger.debug(f"Failed to add reaction: {e}")


@app.command("/answer")
def submit_answer(ack, command, respond):
    """[ADMIN DEBUG] Submit an answer for the current question"""
    ack()

    if command["user_id"] != ADMIN_ID:
        respond("This is an admin-only debug command", response_type="ephemeral")
        return

    channel_id, user_id = command["channel_id"], command["user_id"]
    answer = command["text"].strip()

    instance = INSTANCES.get(channel_id, None)
    if not instance:
        respond(
            "This command can only be used in an instance.", response_type="ephemeral"
        )
        return

    if instance.state != GameState.IN_PROGRESS:
        respond("No active game in progress!", response_type="ephemeral")
        return

    if not answer:
        respond("Please provide an answer!", response_type="ephemeral")
        return

    player = instance.players.get(user_id)
    if not player:
        respond("You are not an active player in this game!", response_type="ephemeral")
        return

    instance.submit_answer(user_id, answer)
    respond("Answer submitted!", response_type="ephemeral")


@app.command("/waitlist")
def join_game(ack, command, respond):
    """Join the waitlist for a new game"""
    ack()
    channel_id, user_id = command["channel_id"], command["user_id"]

    if channel_id != LOBBY_CHANNEL_ID:
        respond(
            "Please use this command in the lobby channel!", response_type="ephemeral"
        )
        return

    if user_id in CURRENTLY_WAITING:
        respond(f"<@{user_id}> You're already in a queue!", response_type="ephemeral")
        return

    CURRENTLY_WAITING.append(user_id)
    respond(
        f"<@{user_id}> You've been added to the queue! Use `/state` to check queue status.",
        response_type="ephemeral",
    )


@app.command("/state")
def status(ack, command, respond):
    """Show current game or global/waitlist status"""
    ack()
    channel_id = command["channel_id"]

    if channel_id != LOBBY_CHANNEL_ID and channel_id not in INSTANCES:
        respond(
            "Please use this command in the lobby or a game instance!",
            response_type="ephemeral",
        )
        logger.info(f"State command in channel {channel_id}, INSTANCES: {INSTANCES}")
        return

    if channel_id == LOBBY_CHANNEL_ID:
        waiting_count = len(CURRENTLY_WAITING)
        active_instances = sum(
            1 for inst in INSTANCES.values() if inst.state == GameState.IN_PROGRESS
        )
        waiting_instances = sum(
            1 for inst in INSTANCES.values() if inst.state == GameState.WAITING
        )

        status_msg = [
            "**Current Status**",
            f"Players in queue: {waiting_count}",
            f"Active games: {active_instances}",
            f"Games waiting to start: {waiting_instances}",
        ]
        if waiting_count > 0:
            status_msg.append("\nPlayers in queue:")
            for user_id in CURRENTLY_WAITING:
                status_msg.append(f"• <@{user_id}>")

        respond("\n".join(status_msg), response_type="ephemeral")
    else:
        instance = INSTANCES[channel_id]
        game_state = instance.get_game_state()

        player_status = []
        for user_id, player in instance.players.items():
            status_emoji = {
                PlayerState.ACTIVE: "✓",
                PlayerState.WINNER: "★",
            }.get(player.state, "")
            player_status.append(
                f"{status_emoji} <@{user_id}> - Score: {player.score} - Lives: {player.lives}"
            )

        status_msg = [
            "**Game Status**",
            f"State: {game_state['state']}",
            f"Phase: {game_state['phase']}",
            f"Round: {game_state['round']}",
            f"Active Players: {game_state['active_players']}/{game_state['player_count']}",
            f"Time elapsed: {game_state['time_elapsed']}",
            "\n*Player Status*",
        ] + player_status

        respond("\n".join(status_msg), response_type="ephemeral")


@app.command("/invitevoyage")
def invite_to_game(ack, command, respond):
    """Invite others to join the current game instance"""
    ack()
    channel_id, _ = command["channel_id"], command["user_id"]

    instance = INSTANCES.get(channel_id, None)
    if not instance:
        respond(
            "This command can only be used in a game instance.",
            response_type="ephemeral",
        )
        return

    if instance.state != GameState.WAITING:  # TODO: extra stuff
        respond(
            "Cannot invite players - the game has already started!",
            response_type="ephemeral",
        )
        return

    respond(
        f"To begin this game, head to <#{LOBBY_CHANNEL_ID}> and use the `/waitlist` command! Otherwise, you can wait for someone to invite you with `/invitevoyage`",
        response_type="ephemeral",
    )


@app.command("/next-round")
def start_next_round(ack, command, say, respond):
    """Start the next main round"""
    ack()
    channel_id, _ = command["channel_id"], command["user_id"]

    instance = INSTANCES.get(channel_id, None)
    if not instance:
        respond(
            "This command can only be used in an instance.", response_type="ephemeral"
        )
        return

    if instance.state != GameState.IN_PROGRESS:
        respond("No active game in progress!", response_type="ephemeral")
        return

    try:
        # cancel any existing timer
        if channel_id in ROUND_TIMERS:
            ROUND_TIMERS[channel_id].cancel()

        send_host_message(channel_id, "main_round")

        challenge = instance.start_main_round()

        say(
            f"**Round {instance.current_round} - {challenge.challenge_type.value.replace('_', ' ').title()}**\n\n"
            f"**{challenge.question}**\n\n"
            f"Time limit: {challenge.time_limit} seconds\n"
            f"Just type your answer in the chat!"
        )

        # schedule autoeval
        timer = threading.Timer(
            challenge.time_limit + 2, lambda: auto_evaluate_round(channel_id)
        )
        ROUND_TIMERS[channel_id] = timer
        timer.start()

    except Exception as e:
        respond(f"Failed to start round: {e}", response_type="ephemeral")


@app.command("/evaluate")
def evaluate_challenge(ack, command, say, respond):
    """[ADMIN DEBUG] Evaluate the current challenge"""
    ack()

    if command["user_id"] != ADMIN_ID:
        respond("This is an admin-only debug command", response_type="ephemeral")
        return

    channel_id, _ = command["channel_id"], command["user_id"]

    instance = INSTANCES.get(channel_id, None)
    if not instance:
        respond(
            "This command can only be used in an instance.", response_type="ephemeral"
        )
        return

    if instance.state != GameState.IN_PROGRESS or not instance.current_challenge:
        respond("No active challenge to evaluate!", response_type="ephemeral")
        return

    try:
        results = instance.evaluate_current_challenge()

        if "error" in results:
            respond(results["error"], response_type="ephemeral")
            return

        message_parts = [
            f"**Results for {results['challenge_type'].replace('_', ' ').title()}**\n"
        ]

        # Show the correct answer
        if instance.current_challenge and instance.current_challenge.correct_answer:
            correct_answer = instance.current_challenge.correct_answer
            if isinstance(correct_answer, list):
                answer_text = " / ".join([html.unescape(str(ans)) for ans in correct_answer])
            else:
                answer_text = html.unescape(str(correct_answer))
            message_parts.append(f"**Correct Answer:** {answer_text}")

        if results["correct_players"]:
            correct_mentions = " ".join(
                [f"<@{uid}>" for uid in results["correct_players"]]
            )
            message_parts.append(f"**Correct:** {correct_mentions}")

        if results["failed_players"]:
            failed_mentions = " ".join(
                [f"<@{uid}>" for uid in results["failed_players"]]
            )
            message_parts.append(f"**Incorrect:** {failed_mentions}")

        game_state = instance.get_game_state()
        rounds_left = instance.config.main_rounds - instance.current_round
        message_parts.append(
            f"\n**Current Status:**\nActive players: {game_state['active_players']}\nRounds left: {rounds_left}"
        )

        say("\n".join(message_parts))

        if instance.current_round >= instance.config.main_rounds:
            final_results = instance.end_game(success=True)
            send_host_message(channel_id, "final_results")

            winners_text = ", ".join([f"<@{uid}>" for uid in final_results["winners"]])
            say(f"**Game Complete!**\n\nWinners: {winners_text}")

            send_host_message(channel_id, "outro")

    except Exception as e:
        respond(f"Failed to evaluate challenge: {e}", response_type="ephemeral")


@app.command("/host-say")
def trigger_host_dialogue(ack, command, respond):
    """Trigger host dialogue for testing - admin only"""
    ack()
    channel_id, user_id = command["channel_id"], command["user_id"]

    if user_id != ADMIN_ID:
        respond("You are not authorized to use this command", response_type="ephemeral")
        return

    instance = INSTANCES.get(channel_id, None)
    if not instance:
        respond(
            "This command can only be used in an instance.", response_type="ephemeral"
        )
        return

    dialogue_key = command["text"].strip()
    if not dialogue_key:
        respond(
            "Usage: `/host-say <dialogue_key>`\nAvailable keys: intro, main_round, final_results, outro",
            response_type="ephemeral",
        )
        return

    try:
        instance.host_says(dialogue_key)
        respond(f"Triggered '{dialogue_key}' dialogue!", response_type="ephemeral")
    except Exception as e:
        respond(f"Failed to trigger dialogue: {e}", response_type="ephemeral")


@app.command("/game-type")
def start_specific_game_type(ack, command, say, respond):
    """Start a main round with a specific game type"""
    ack()
    channel_id, _ = command["channel_id"], command["user_id"]
    game_type_str = command["text"].strip().upper().replace(" ", "_")

    instance = INSTANCES.get(channel_id, None)
    if not instance:
        respond(
            "This command can only be used in an instance.", response_type="ephemeral"
        )
        return

    if instance.state != GameState.IN_PROGRESS:
        respond("No active game in progress!", response_type="ephemeral")
        return

    try:
        game_type = GameType(game_type_str.lower())
    except ValueError:
        available_types = [gt.value.replace("_", " ").title() for gt in GameType]
        respond(
            f"Invalid game type! Available types: {', '.join(available_types)}",
            response_type="ephemeral",
        )
        return

    try:
        challenge = instance.start_main_round(game_type)

        say(
            f"**Round {instance.current_round} - {challenge.challenge_type.value.replace('_', ' ').title()}**\n\n"
            f"**{challenge.question}**\n\n"
            f"Time limit: {challenge.time_limit} seconds\n"
            f"Just type your answer in the chat!"
        )

    except Exception as e:
        respond(f"Failed to start round: {e}", response_type="ephemeral")


def ensure_lobby_channel():
    """Make sure the lobby channel exists and the bot is a member"""
    try:
        channel_info = app.client.conversations_info(channel=LOBBY_CHANNEL_ID)
        logger.info(
            f"Found lobby channel: {channel_info['channel']['name']} (Private: {channel_info['channel']['is_private']})"
        )
    except Exception as e:
        logger.error(f"Failed to ensure lobby channel's existence: {e}")
        return False

    members = app.client.conversations_members(channel=LOBBY_CHANNEL_ID)["members"]
    if BOT_ID in members:
        logger.debug("Voyager is already in lobby channel")
        return True

    try:
        app.client.conversations_join(channel=LOBBY_CHANNEL_ID)
        logger.info("Successfully joined lobby channel")
        return True
    except Exception as e:
        logger.error(f"Lobby autojoin failed: {e}")
        return False


def initialize_app():
    logger.info("Initializing Voyager...")

    if not ensure_lobby_channel():
        logger.error("Failed to set up lobby channel - exiting")
        sys.exit(1)

    global INSTANCES, CURRENTLY_WAITING, ROUND_TIMERS

    for timer in ROUND_TIMERS.values():
        if timer:
            timer.cancel()

    INSTANCES = {}
    CURRENTLY_WAITING = []
    ROUND_TIMERS = {}

    cursor = None
    while True:
        result = app.client.conversations_list(
            types="private_channel",
            limit=100,
            cursor=cursor,
        )

        for channel in result["channels"]:
            if not channel["is_member"] or not channel["name"].startswith("v-inst-"):
                continue

            logger.info(f"Cleaning up instance {channel['name']}")
            members = app.client.conversations_members(channel=channel["id"])["members"]
            for member in members:
                if member not in [ADMIN_ID, BOT_ID]:
                    try:
                        app.client.conversations_kick(
                            channel=channel["id"], user=member
                        )
                        # purge channel messages
                        result = app.client.conversations_history(
                            channel=channel["id"], limit=1000, inclusive=True
                        )
                        for message in result["messages"]:
                            app.client.chat_delete(
                                channel=channel["id"], ts=message["ts"]
                            )
                    except Exception as e:
                        logger.error(
                            f"Failed to kick user {member} from channel {channel['name']}: {e}"
                        )

            # now add back to INSTANCES
            INSTANCES[channel["id"]] = create_instance_with_dialogue(
                channel["id"], channel["name"]
            )
        cursor = result.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break

    try:
        result = app.client.conversations_history(channel=LOBBY_CHANNEL_ID, limit=1000)
        for message in result["messages"]:
            if message.get("user") == BOT_ID:
                app.client.chat_delete(channel=LOBBY_CHANNEL_ID, ts=message["ts"])
    except Exception as e:
        logger.error(f"Failed to purge previous messages: {e}")

    app.client.chat_postMessage(
        channel=LOBBY_CHANNEL_ID,
        text="Voyager is ready! Use the following commands:\n• `/waitlist` - Join the queue for a game\n• `/state` - Check current game status",
    )

    logger.info("Initialization complete")


if __name__ == "__main__":
    initialize_app()
    scheduler.start()
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    logger.info("Starting Slack bot...")
    handler.start()
