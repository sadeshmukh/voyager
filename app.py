import datetime
import os
import sys
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from instance import Instance, GameState
import yaml

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

scheduler = BackgroundScheduler()


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
    INSTANCES[channel_id] = Instance(channel_id, name)
    app.client.conversations_invite(channel=channel_id, users=[ADMIN_ID, BOT_ID])


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
                                text=f"Welcome <@{user}>! 👋 You can invite others to join this game using `/invitevoyage`",
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
    channel_id, user_id = command["channel_id"], command["user_id"]

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
        game_state = instance.start_game()
        say(f"Game started! Current state: {game_state}")
    except Exception as e:
        respond(f"Failed to start game: {e}", response_type="ephemeral")


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
            f"*Current Status*",
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
        status_msg = [
            "*Game Status*",
            f"State: {game_state['state']}",
            f"Players: {game_state['player_count']}",
            f"Time elapsed: {game_state['time_elapsed']}",
        ]
        respond("\n".join(status_msg), response_type="ephemeral")


@app.command("/invitevoyage")
def invite_to_game(ack, command, respond):
    """Invite others to join the current game instance"""
    ack()
    channel_id, user_id = command["channel_id"], command["user_id"]

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

    global INSTANCES, CURRENTLY_WAITING
    INSTANCES = {}
    CURRENTLY_WAITING = []

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
            INSTANCES[channel["id"]] = Instance(channel["id"], channel["name"])
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
