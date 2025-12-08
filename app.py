"""Trivia webapp server module.

This module implements a simple Flask + Flask-SocketIO server that runs
multiplayer trivia games. It manages rooms, broadcasts questions and
countdowns, and provides a resynchronization endpoint so clients can
rejoin a running game after a reconnect.

Key concepts:
- `threads_dict` holds per-room state (points, answers, current question).
- Socket.IO events implement join/start/answer flows and a `resync_request`
    to restore client state after reconnection.

The file aims to be lightweight and single-process. For scaling to
multiple workers, move state to a shared store (Redis) and enable the
message queue support in Flask-SocketIO.
"""

from threading import Lock
from queue import Queue
import random
import time
import os

from flask import Flask, render_template, session, request, copy_current_request_context
from flask_cors import CORS, cross_origin

from flask_socketio import SocketIO, emit, join_room, leave_room, close_room, rooms, disconnect
import urllib.request
import json
from html.parser import HTMLParser
import logging


# Set this variable to "threading", "eventlet" or "gevent" to test the
# different async modes, or leave it set to None for the application to choose
# the best option based on installed packages.
async_mode = None

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key')
#socketio = SocketIO(app, async_mode=async_mode, cors_allowed_origins='https://triviaweb.deta.dev')
# socketio = SocketIO(app, async_mode=async_mode, async_handlers=False, cors_allowed_origins='*')
socketio = SocketIO(app, async_mode=async_mode, async_handlers=False, 
                     ping_timeout=120, ping_interval=10, 
                     cors_allowed_origins="*")

@app.route('/')
def index():
    return render_template('index.html', async_mode=socketio.async_mode)


@app.route('/sessions')
def sessions_page():
    """Serve the sessions admin/listing page."""
    return render_template('sessions.html')

# Configure module-level logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

thread = None
thread_lock = Lock()

h = HTMLParser()

threads_dict = {}
sid_room_map = {}

#############################################
#
#   input: (str) room, (int) quiz_timer, (int) count_type
# 

def countdown_timer(room, quiz_timer, count_type):
    """Broadcast a countdown for a question or inter-question period.

    Args:
        room (str): Room name to broadcast to.
        quiz_timer (int): Seconds for the countdown.
        count_type (str): Identifier for countdown type (e.g. "question_countdown").

    The function emits a `my_countdown` event every second with the
    remaining seconds so clients can update progress bars. It uses a
    blocking `time.sleep(1)` loop but keeps the sleep short so the
    Socket.IO heartbeat can be processed between iterations.
    """

    for i in range(quiz_timer):
        logger.debug("Time left: %s", str(quiz_timer - i))
        data_string = [count_type, quiz_timer - i, quiz_timer]
        convert_and_send_json(room, "my_countdown", {"data": data_string})

        # Short sleep allows server to remain responsive to heartbeats.
        time.sleep(1)

    data_string = [count_type, 0, quiz_timer]
    convert_and_send_json(room, "my_countdown", {"data": data_string})

#############################################
#
#   input: (str) room, (str) username, (bool) publish
# 

def add_user_to_room(room, username, publish=True):
    """Add a user to a room and optionally publish the leaderboard.

    Args:
        room (str): Room name.
        username (str): Username to add.
        publish (bool): If True, broadcast the updated leaderboard.
    """

    threads_dict[room]["points"][username] = 0

    if publish:
        # Build leaderboard data with both points and games_won
        leaderboard_data = {}
        for player in threads_dict[room]["points"].keys():
            leaderboard_data[player] = {
                "points": threads_dict[room]["points"][player],
                "games_won": threads_dict[room]["games_won"].get(player, 0)
            }
        convert_and_send_json(room, "my_leaderboard", {"data": leaderboard_data})

#############################################
#
#   input: (str) room, (bool) running, (int) quiz_flag, (int) quiz_timer
# 
def update_room_list(room, running=False, quiz_flag=5, quiz_timer=10, category="0", gametype="quiz"):
    """Create or update the stored data for a room.

    This routine ensures a room entry exists in ``threads_dict`` and can
    optionally start the room's background thread when ``running`` is
    set to True.

    Args:
        room (str): Room identifier.
        running (bool): Whether the game should be running.
        quiz_flag (int): Number of questions.
        quiz_timer (int): Seconds per question.
        category (str): Question category id (string).
        gametype (str): Either 'quiz' or 'liar'.
    """

    global threads_dict

    # If the room already exists, update runtime flags and possibly
    # start/stop the room background thread.
    if room in threads_dict:
        logger.info("Room already created: %s", room)
        if threads_dict[room]["running"]:
            logger.info("Room already running: %s", room)
            if not running:
                logger.info("Stopping the room: %s", room)
                threads_dict[room]["running"] = False
        else:
            if running:
                threads_dict[room]["running"] = True
                threads_dict[room]["gametype"] = gametype
                threads_dict[room]["category"] = category
                logger.debug("Room state after update: %s", threads_dict.get(room))
                if gametype == "quiz":
                    threads_dict[room]["thread"] = socketio.start_background_task(
                        room_quiz_thread, room, quiz_flag, quiz_timer, category
                    )
                elif gametype == "liar":
                    threads_dict[room]["thread"] = socketio.start_background_task(
                        room_liar_thread, room, quiz_flag, quiz_timer, category
                    )
            else:
                threads_dict[room]["running"] = False
                logger.debug("Room stopped: %s", room)

    # Create a fresh room entry with default structures.
    else:
        threads_dict[room] = {}
        threads_dict[room]["queue"] = Queue
        threads_dict[room]["answers"] = {}
        threads_dict[room]["points"] = {}
        threads_dict[room]["games_won"] = {}
        threads_dict[room]["liar_answers"] = {}
        threads_dict[room]["sids"] = set()
        threads_dict[room]["users"] = {}
        # Track the current host for the room (socket id) and last active time
        threads_dict[room]["host_sid"] = None
        threads_dict[room]["host_username"] = None
        threads_dict[room]["last_active"] = time.time()
        # Default timers (seconds)
        threads_dict[room]["quiz_timer"] = 10
        threads_dict[room]["answer_timer"] = 3
        threads_dict[room]["liar_submit_timer"] = 10
        threads_dict[room]["running"] = running
        threads_dict[room]["gametype"] = ""
        threads_dict[room]["category"] = ""

        # ## If it's set to run, spawn it:
        # if(running):
        #     threads_dict[room]["running"] = True
        #     print(threads_dict)
        #     #threads_dict[room]["thread"] = socketio.start_background_task( room_quiz_thread(room, quiz_flag, quiz_timer) )
        #     threads_dict[room]["thread"] = socketio.start_background_task( room_liar_thread(room, quiz_flag, quiz_timer) )
        # ## Otherwise just make it, no spawning:
        # else:
        #     threads_dict[room]["running"] = False
        #     print(threads_dict)

#############################################
#
#   input: list to turn in to json for the server
# 
def convert_to_json(input_list):
    """Serialize a Python object into a compact JSON string.

    Args:
        input_list: Any JSON-serializable Python object.

    Returns:
        str: Compact JSON string representation.
    """

    json_string = json.dumps(input_list, separators=(",", ":"))
    return json_string

#############################################
#
#   input: (str) room, (str) broadcaster, (dict) payload
# 
def convert_and_send_json(room, broadcast_title, input_dict):
    """Convert payload fields to JSON strings and emit to a room.

    Args:
        room (str): Room name to emit to.
        broadcast_title (str): Event name to emit.
        input_dict (dict): Mapping of keys to Python objects that will be
            JSON-serialized before emission.
    """

    try:
        # Skip emit if room has no connected clients.
        if room in threads_dict and not threads_dict[room].get("sids"):
            logger.debug("No clients in room %s, skipping emit %s", room, broadcast_title)
            return

        output_dict = {key: convert_to_json(value) for key, value in input_dict.items()}
        logger.debug("Emitting %s to %s: %s", broadcast_title, room, output_dict)
        socketio.emit(broadcast_title, output_dict, to=room, skip_sid=None)
    except Exception as exc:  # pragma: no cover - runtime guard
        logger.exception("Error emitting to room %s: %s", room, exc)


###############################################
# MAIN Thread, spun off when a room starts a game
#
#################################################
def room_quiz_thread(room, quiz_flag, quiz_timer, category):
    global threads_dict
    logger.info("Starting quiz thread for room %s", room)

    QUESTION_URL = "https://opentdb.com/api.php?amount=" + str(quiz_flag)
    if(category != "0"):
        QUESTION_URL = QUESTION_URL + "&category=" + str(category)
    QUESTIONS = None

    logger.debug("Fetching questions from URL: %s", QUESTION_URL)

    current_question = ""
    correct_answer = ""


    try:
        with urllib.request.urlopen(QUESTION_URL, timeout=10) as url:
            data = json.load(url)
            QUESTIONS = data["results"]
    except Exception as e:
        print(f"Error fetching questions: {e}")
        QUESTIONS = []

    count = 0
    ### Check if there is questions in the list
    if quiz_flag: 

        start_l = ["start"]
        convert_and_send_json(room, 'my_start', {'data': start_l, 'count': count})

        for i in range(quiz_flag):
            count = i
            if count >= len(QUESTIONS):
                count = 0

            # Clear previous answers
            threads_dict[room]["answers"] = {}

            # set current question metadata for resyncs
            threads_dict[room]["current_question_index"] = count
            threads_dict[room]["current_question"] = None
            threads_dict[room]["current_answers"] = None
            threads_dict[room]["question_ends_at"] = None

            #############################################
            # Send the questions
            logger.debug("Question payload: %s", QUESTIONS[count] if QUESTIONS and count < len(QUESTIONS) else None)

            if QUESTIONS:
                current_question = QUESTIONS[count]["question"]
                correct_answer = QUESTIONS[count]["correct_answer"]
                answers = QUESTIONS[count]["incorrect_answers"]
            else:
                current_question = "(No question available)"
                correct_answer = ""
                answers = []
            answers.insert(0, correct_answer )
            random.shuffle(answers)

            # Determine timers from room state (allow overrides set at start)
            q_timer = threads_dict[room].get("quiz_timer", quiz_timer)
            a_timer = threads_dict[room].get("answer_timer", q_timer)

            # store question/answers and when it will end for resync
            threads_dict[room]["current_question"] = current_question
            threads_dict[room]["current_answers"] = answers
            threads_dict[room]["question_ends_at"] = time.time() + q_timer
            threads_dict[room]["last_quiz_duration"] = q_timer

            question_l = ["Q"+str(count), "text_question", current_question, answers]
            convert_and_send_json(room, 'my_question', {'data': question_l, 'count': count})
            
            #############################################
            # Send the countdown for the question period
            countdown_timer(room, q_timer, "question_countdown")
            
            #############################################
            # Send the answer and scoreboard
            logger.info("Answer reveal for room %s (question %s)", room, count)
            #### Send Real Answer and if they were correct:

            data_string = ["The correct answer was ", correct_answer]
            convert_and_send_json(room, 'my_question_answer', {'data': data_string})

            for username, answer in threads_dict[room]["answers"].items():

                correct =  True if answer == correct_answer else False
                if(correct):
                    if(username in threads_dict[room]["points"].keys()):
                        threads_dict[room]["points"][username] = threads_dict[room]["points"][username] + 10
                    else:
                        threads_dict[room]["points"][username] = 10

                logger.info("USER: %s ANSWERED: %s correct=%s", username, answer, correct)


            ##### Send everyones points in leaderboard
            leaderboard_data = {}
            for player in threads_dict[room]["points"].keys():
                leaderboard_data[player] = {
                    "points": threads_dict[room]["points"][player],
                    "games_won": threads_dict[room]["games_won"].get(player, 0)
                }
            convert_and_send_json(room, 'my_leaderboard', {'data': leaderboard_data})
            # Wait answer/show period before the next question
            countdown_timer(room, a_timer, "next_question")

    #### When no 
    # else:
    #     socketio.emit('my_question',
    #                 {'data': "", 'count': -1},
    #                     to="hello_world")#

    # Emit game end event with final leaderboard
    logger.info("Quiz game ended for room %s", room)
    
    # Find winners (handle ties) and update games_won
    if threads_dict[room]["points"]:
        max_score = max(threads_dict[room]["points"].values())
        winners = [p for p, s in threads_dict[room]["points"].items() if s == max_score]
        for winner in winners:
            threads_dict[room]["games_won"][winner] = threads_dict[room]["games_won"].get(winner, 0) + 1
        logger.info("Quiz game winners for room %s: %s", room, winners)
    
    # Build game_end data with both points and games_won
    game_end_data = {}
    for player in threads_dict[room]["points"].keys():
        game_end_data[player] = {
            "points": threads_dict[room]["points"][player],
            "games_won": threads_dict[room]["games_won"].get(player, 0)
        }
    convert_and_send_json(room, 'game_end', {'data': game_end_data})
    threads_dict[room]["running"] = False

###############################################
# MAIN Thread, spun off when a room starts a game
#
#################################################
def room_liar_thread(room, quiz_flag, quiz_timer, category):
    """Run the "liar" game mode for a room.

    The liar mode requests user-written lies and then presents a merged
    set of answers. This function manages question flow and scoring.
    """

    logger.info("Starting liar thread for room %s", room)

    global threads_dict

    QUESTION_URL = "https://opentdb.com/api.php?amount=" + str(quiz_flag) + "&type=multiple"
    if category != "0":
        QUESTION_URL = QUESTION_URL + "&category=" + str(category)
    QUESTIONS = None

    current_question = ""
    correct_answer = ""

    # Load the questions from opentdb
    try:
        with urllib.request.urlopen(QUESTION_URL, timeout=10) as url:
            data = json.load(url)
            QUESTIONS = data["results"]
    except Exception as e:
        logger.exception("Error fetching questions for liar mode: %s", e)
        QUESTIONS = []

    # Send initial start signal
    if quiz_flag and QUESTIONS:
        start_l = ["start", "liar"]
        convert_and_send_json(room, 'my_start', {'data': start_l, 'count': 0})

        # Single loop through the questions
        for i in range(quiz_flag):
            count = i
            if count >= len(QUESTIONS):
                count = count % len(QUESTIONS)

            # Clear previous answers and prepare resync metadata
            threads_dict[room]["answers"] = {}
            threads_dict[room]["liar_answers"] = {}
            threads_dict[room]["current_question_index"] = count
            threads_dict[room]["current_question"] = None
            threads_dict[room]["current_answers"] = None
            threads_dict[room]["question_ends_at"] = None

            logger.debug("Question payload (liar): %s", QUESTIONS[count])

            current_question = QUESTIONS[count]["question"]
            correct_answer = QUESTIONS[count]["correct_answer"]

            # Phase 1: Ask players to write their lies
            question_l = ["Q" + str(count), "text_question", current_question, ["liar"]]
            threads_dict[room]["current_question"] = current_question
            threads_dict[room]["current_answers"] = ["liar"]

            # Use configured liar submit duration
            liar_submit = threads_dict[room].get("liar_submit_timer", 10)
            threads_dict[room]["question_ends_at"] = time.time() + liar_submit
            threads_dict[room]["last_quiz_duration"] = liar_submit

            convert_and_send_json(room, "my_liar_question", {"data": question_l, "count": count})

            # Wait for users to submit lies
            countdown_timer(room, liar_submit, "question_countdown")

            # Phase 2: Build answer list combining incorrect answers (from API) and user lies
            # De-duplicate: if a user's lie matches something already in the list, don't add duplicate
            # Also track which usernames submitted each lie for attribution
            answers = list(QUESTIONS[count]["incorrect_answers"]) if QUESTIONS else []
            lie_submitters = {}  # maps lie text -> list of usernames who submitted it

            for username, lie in threads_dict[room]["liar_answers"].items():
                # Only add the lie if it's not already in the answer list (case-insensitive comparison)
                if not any(ans.lower() == lie.lower() for ans in answers):
                    answers.append(lie)
                    lie_submitters[lie] = [username]
                else:
                    # Lie text already exists; track this submitter
                    if lie not in lie_submitters:
                        lie_submitters[lie] = []
                    lie_submitters[lie].append(username)

            # Add correct answer and shuffle
            answers.insert(0, correct_answer)
            random.shuffle(answers)
            
            # Store lie submitters in room state for use during answer reveal
            threads_dict[room]["lie_submitters"] = lie_submitters

            # Send the combined answer list to players
            question_l = ["Q" + str(count), "text_question", current_question, answers]
            threads_dict[room]["current_answers"] = answers
            threads_dict[room]["question_ends_at"] = time.time() + quiz_timer
            threads_dict[room]["last_quiz_duration"] = quiz_timer

            convert_and_send_json(room, "my_question", {"data": question_l, "count": count})

            # Phase 3: Allow time for users to answer
            countdown_timer(room, quiz_timer, "question_countdown")

            # Phase 4: Reveal correct answer and calculate scoring
            logger.info("Answer reveal (liar mode) for room %s (question %s)", room, count)
            data_string = ["The correct answer was ", correct_answer]
            convert_and_send_json(room, "my_question_answer", {"data": data_string})

            # In liar mode, also send lie attribution data so client can display who submitted each lie
            lie_attribution = threads_dict[room].get("lie_submitters", {})
            socketio.emit("liar_lie_attribution", {"lies": lie_attribution}, to=room)

            # Track which lies were selected and by whom
            lie_selections = {}  # maps lie -> list of usernames who selected it
            for username, answer in threads_dict[room]["answers"].items():
                correct = (answer == correct_answer)
                if correct:
                    # Correct answer: 10 points
                    threads_dict[room]["points"][username] = (
                        threads_dict[room]["points"].get(username, 0) + 10
                    )
                else:
                    # Wrong answer: check if it matches any user's lie
                    for liar, lie in threads_dict[room]["liar_answers"].items():
                        if answer == lie and liar != username:
                            # Liar gets 5 points for fooling this user
                            threads_dict[room]["points"][liar] = (
                                threads_dict[room]["points"].get(liar, 0) + 5
                            )

                logger.info("USER: %s ANSWERED: %s correct=%s", username, answer, correct)

            # Send updated leaderboard
            leaderboard_data = {}
            for player in threads_dict[room]["points"].keys():
                leaderboard_data[player] = {
                    "points": threads_dict[room]["points"][player],
                    "games_won": threads_dict[room]["games_won"].get(player, 0)
                }
            convert_and_send_json(room, "my_leaderboard", {"data": leaderboard_data})

            # Wait before next question
            countdown_timer(room, 3, "next_question")

        threads_dict[room]["running"] = False

        # Emit game end event with final leaderboard
        logger.info("Liar game ended for room %s", room)
        
        # Find winners (handle ties) and update games_won
        if threads_dict[room]["points"]:
            max_score = max(threads_dict[room]["points"].values())
            winners = [p for p, s in threads_dict[room]["points"].items() if s == max_score]
            for winner in winners:
                threads_dict[room]["games_won"][winner] = threads_dict[room]["games_won"].get(winner, 0) + 1
            logger.info("Liar game winners for room %s: %s", room, winners)
        
        # Build game_end data with both points and games_won
        game_end_data = {}
        for player in threads_dict[room]["points"].keys():
            game_end_data[player] = {
                "points": threads_dict[room]["points"][player],
                "games_won": threads_dict[room]["games_won"].get(player, 0)
            }
        convert_and_send_json(room, 'game_end', {'data': game_end_data})


def cleanup_rooms_thread():
    """Background thread: periodically remove stale rooms.

    Removes rooms that have no connected clients (`sids` empty), are
    not running, and haven't been active for a configurable expiry
    period. Runs forever in background with a sleep interval.
    """
    ROOM_EXPIRY_SECONDS = 300  # 5 minutes
    SLEEP_INTERVAL = 60
    logger.info("Room cleanup thread started (expiry=%s seconds)", ROOM_EXPIRY_SECONDS)
    while True:
        try:
            now = time.time()
            for room in list(threads_dict.keys()):
                room_state = threads_dict.get(room)
                if not room_state:
                    continue
                sids = room_state.get("sids", set())
                running = room_state.get("running", False)
                last_active = room_state.get("last_active", now)
                if len(sids) == 0 and not running and (now - last_active) > ROOM_EXPIRY_SECONDS:
                    logger.info("Cleaning up stale room: %s (last_active=%s)", room, last_active)
                    try:
                        del threads_dict[room]
                    except Exception:
                        logger.exception("Error deleting room %s", room)
        except Exception:
            logger.exception("Error during room cleanup loop")
        time.sleep(SLEEP_INTERVAL)


@socketio.on('close_room')
def on_close_room(message):
    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response', {'data': 'Room ' + message['room'] + ' is closing.',
                         'count': session['receive_count']},
         to=message['room'])
    close_room(message['room'])


@socketio.event
def my_room_event(message):
    """Send a message to a specific room.

    Args:
        message (dict): Expected keys: 'room' and 'data'.
    """

    session["receive_count"] = session.get("receive_count", 0) + 1
    emit(
        "my_response",
        {"data": message["data"], "count": session["receive_count"]},
        to=message["room"],
    )


@socketio.event
def disconnect_request():
    @copy_current_request_context
    def can_disconnect():
        disconnect()
    session["receive_count"] = session.get("receive_count", 0) + 1

    # Inform client and then disconnect in the callback handler.
    emit(
        "my_response",
        {"data": "Disconnected!", "count": session["receive_count"]},
        callback=can_disconnect,
    )


@socketio.event
def my_ping():
    """Respond to a ping from the client with a pong.

    Used by the client to measure round-trip latency.
    """

    emit("my_pong")


@socketio.event
def resync_request(message):
    """Client requests current room state to resynchronize after reconnect."""
    room = message.get('room')
    username = message.get('username')
    sid = request.sid
    logger.info("Resync request from sid=%s user=%s room=%s", sid, username, room)

    if room not in threads_dict:
        socketio.emit('resync_response', {'running': False}, to=sid)
        return

    room_state = threads_dict[room]
    # compute time remaining
    ends_at = room_state.get('question_ends_at')
    if ends_at:
        time_remaining = max(0, int(ends_at - time.time()))
    else:
        time_remaining = 0

    resp = {
        'running': room_state.get('running', False),
        'current_question_index': room_state.get('current_question_index'),
        'current_question': room_state.get('current_question'),
        'current_answers': room_state.get('current_answers'),
        'time_remaining': time_remaining,
        'leaderboard': room_state.get('points', {}),
        'last_quiz_duration': room_state.get('last_quiz_duration')
    }

    socketio.emit('resync_response', resp, to=sid)


@socketio.event
def connect(auth=None):
    """Handle a new client connection.

    Emits an initial `my_response` message to acknowledge the connection.
    """

    global thread
    # Start the global cleanup thread once when the first client connects.
    with thread_lock:
        if thread is None:
            thread = socketio.start_background_task(cleanup_rooms_thread)

    emit("my_response", {"data": "Connected", "count": 0})


@socketio.on('disconnect')
def test_disconnect():
    logger.info('Client disconnected %s', request.sid)
    # Find which room this user was in and try to notify others
    global threads_dict
    sid = request.sid
    # remove sid mapping and notify any room it belonged to
    for room in list(threads_dict.keys()):
        if sid in threads_dict[room].get("sids", set()):
            username = threads_dict[room]["users"].get(sid)
            try:
                threads_dict[room]["sids"].remove(sid)
            except Exception:
                pass
            threads_dict[room]["users"].pop(sid, None)
            # remove sid->room mapping
            sid_room_map.pop(sid, None)
            # update last active timestamp
            threads_dict[room]["last_active"] = time.time()
            # If the departing client was the host, try to hand over host to another active client
            try:
                if threads_dict[room].get("host_sid") == sid:
                    remaining = threads_dict[room].get("sids", set())
                    if remaining:
                        new_host_sid = next(iter(remaining))
                        new_host_username = threads_dict[room]["users"].get(new_host_sid)
                        threads_dict[room]["host_sid"] = new_host_sid
                        threads_dict[room]["host_username"] = new_host_username
                        logger.info("Reassigned host for room %s to %s (sid=%s)", room, new_host_username, new_host_sid)
                        # Notify the new host directly
                        socketio.emit('host_status', {'is_host': True, 'username': new_host_username}, to=new_host_sid)
                        # Broadcast host change to the room
                        socketio.emit('host_changed', {'username': new_host_username}, to=room)
                    else:
                        # No remaining clients: clear host info
                        threads_dict[room]["host_sid"] = None
                        threads_dict[room]["host_username"] = None
            except Exception:
                logger.exception("Error during host reassignment for room %s", room)
            logger.debug("Removed sid %s (user=%s) from room %s", sid, username, room)
            socketio.emit('user_disconnected', 
                         {'data': f'Player {username} disconnected'},
                         to=room)
            
            # If room is now empty and not running, delete it
            if len(threads_dict[room]["sids"]) == 0 and not threads_dict[room].get("running", False):
                logger.info("Cleaning up empty room: %s", room)
                del threads_dict[room]
    # end test_disconnect


@socketio.event
def name_join(message):

    username = message["username"]
    room = message["room"]
    logger.info("%s is joining room %s", username, room)

    logger.debug("Currently in rooms: %s", rooms())
    for i in rooms():
        logger.debug("Leaving room: %s", i)
        leave_room(i)

    join_room(room)
    # add sid -> room and room -> sid mappings
    sid = request.sid
    if room not in threads_dict:
        update_room_list(room, False)
    
    # Add this socket to the room state
    threads_dict[room].setdefault("sids", set()).add(sid)
    threads_dict[room].setdefault("users", {})[sid] = username
    threads_dict[room].setdefault("games_won", {})[username] = 0
    sid_room_map[sid] = room
    # mark room as active now
    threads_dict[room]["last_active"] = time.time()

    # Determine host assignment: if there's no recorded host or the host is
    # not currently connected, promote this joiner to host. Otherwise keep
    # the existing host.
    current_host_sid = threads_dict[room].get("host_sid")
    is_host = False
    if not current_host_sid or current_host_sid not in threads_dict[room].get("sids", set()):
        # If there was a previous host still connected, clear their status
        prev = current_host_sid
        prev_username = threads_dict[room].get('host_username')
        threads_dict[room]["host_sid"] = sid
        threads_dict[room]["host_username"] = username
        is_host = True
        logger.info("Assigned host for room %s to %s (sid=%s)", room, username, sid)
        try:
            if prev and prev in threads_dict[room].get("sids", set()):
                socketio.emit('host_status', {'is_host': False, 'username': prev_username}, to=prev)
        except Exception:
            logger.exception("Failed to notify previous host (sid=%s)", prev)
        # Notify room about host change
        socketio.emit('host_changed', {'username': username}, to=room)
    else:
        is_host = (current_host_sid == sid)

    logger.debug("Now in rooms: %s", rooms())
    logger.info("Player %s (sid=%s) is host: %s", username, sid, is_host)

    # Tell this client whether they are host
    socketio.emit('host_status', {'is_host': is_host, 'username': username}, to=sid)

    # Broadcast player count update to everyone in the room (including this new player)
    socketio.emit('room_player_update', {'player_count': len(threads_dict[room]["users"])}, to=room)

    convert_and_send_json(room, 'my_response', {'data': room, 'count': 666})
    add_user_to_room(room, username)


@socketio.event
def start_room(message):
    logger.debug("start_room message: %s", message)
    room = message["room"]
    numofq =  message["numofq"]
    room_type = message["gametype"]
    category = message["category"]
    logger.info("%s is starting with %s questions category: %s", room, numofq, category)

    global threads_dict
    
    # Check if a game is already running in this room
    if room in threads_dict and threads_dict[room].get("running"):
        logger.warning("Game already running in room %s, start request rejected", room)
        emit('game_start_error', {'data': 'A game is already in progress in this room!'}, to=room)
        return

    quiz_flag = int(numofq)
    logger.debug("Threads dict prior to start: %s", threads_dict)

    # Reset scores for all players at the start of a new game
    if room in threads_dict:
        for username in threads_dict[room]["points"]:
            threads_dict[room]["points"][username] = 0
        logger.info("Reset scores for all players in room %s", room)
    
    ## Check if room already exists:
    # Use timers provided by the client if available
    quiz_timer = int(message.get('quiz_timer', 10))
    answer_timer = int(message.get('answer_timer', 3))
    liar_submit_timer = int(message.get('liar_submit_timer', 10))

    update_room_list(room, True, quiz_flag, quiz_timer, category, room_type)
    # store timers in the room state for use by threads
    threads_dict[room]["quiz_timer"] = quiz_timer
    threads_dict[room]["answer_timer"] = answer_timer
    threads_dict[room]["liar_submit_timer"] = liar_submit_timer
    # set room-level metadata for resync
    threads_dict[room].setdefault("current_question_index", None)
    threads_dict[room].setdefault("current_question", None)
    threads_dict[room].setdefault("current_answers", None)
    threads_dict[room].setdefault("question_ends_at", None)
    

@socketio.event
def my_answer(message):
    """Record a player's answer for the current question.

    Args:
        message (dict): Expected keys: 'room', 'username', 'answer'.
    """

    room = message["room"]
    username = message["username"]
    answer = message["answer"]

    logger.info("%s of %s answered %s", message["username"], message["room"], message["answer"]) 

    global threads_dict
    threads_dict[room]["answers"][username] = answer


@socketio.event
def my_liar_answer(message):
    """Record a player's submitted lie for liar mode.

    Args:
        message (dict): Expected keys: 'room', 'username', 'answer'.
    """

    room = message["room"]
    username = message["username"]
    answer = message["answer"]

    logger.info("%s of %s answered %s", message["username"], message["room"], message["answer"]) 

    global threads_dict
    threads_dict[room]["liar_answers"][username] = answer


if __name__ == "__main__":
    # Enable CORS and run the development server. For production,
    # run under Gunicorn with the eventlet worker and ensure the
    # same async library is installed (e.g. eventlet).
    CORS(app)
    socketio.run(app, port=6666, debug=False, use_reloader=False)