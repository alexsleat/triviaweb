from threading import Lock
from queue import Queue
import random

from flask import Flask, render_template, session, request, \
    copy_current_request_context
from flask_socketio import SocketIO, emit, join_room, leave_room, \
    close_room, rooms, disconnect
import urllib.request, json 
from html.parser import HTMLParser


# Set this variable to "threading", "eventlet" or "gevent" to test the
# different async modes, or leave it set to None for the application to choose
# the best option based on installed packages.
async_mode = None

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode=async_mode)
thread = None
thread_lock = Lock()

h = HTMLParser()

room_thread = [ ]

threads_dict = {}

def add_user_to_room(room, username, publish=True):

    threads_dict[room]["points"][username] = 0
    # 
    if(publish):
        convert_and_send_json(room, 'my_leaderboard', {'data': threads_dict[room]["points"]})

def update_room_list(room, running=False, quiz_flag=5, quiz_timer=10):

    global threads_dict

    ## Check if room already exists:
    if room in threads_dict.keys():
        print("Room already created")
        if threads_dict[room]["running"]:
            print("Room already running")
        else:
            if(running):
                threads_dict[room]["running"] = True
                print(threads_dict)
                threads_dict[room]["thread"] = socketio.start_background_task( room_quiz_thread(room, quiz_flag, quiz_timer) )

            else:
                threads_dict[room]["running"] = False
                print(threads_dict)
    else:

        threads_dict[room] = {}
        threads_dict[room]["queue"] = Queue
        threads_dict[room]["answers"] = {}
        threads_dict[room]["points"] = { }

        if(running):
            threads_dict[room]["running"] = True
            print(threads_dict)
            threads_dict[room]["thread"] = socketio.start_background_task( room_quiz_thread(room, quiz_flag, quiz_timer) )

        else:
            threads_dict[room]["running"] = False
            print(threads_dict)




def convert_to_json(input_list):

    json_string = json.dumps(input_list, separators=(',', ':'))
    return json_string

#############################################
#
#   input: (str) room, (str) broadcaster, (dict) payload
# 
def convert_and_send_json(room, broadcast_title, input_dict):


    output_dict = {}
    for key, value in input_dict.items():
        output_dict[key] = convert_to_json(value)
    
    print("OD::::: ", output_dict)
    socketio.emit(broadcast_title,
                    output_dict,
                    to=room)


###############################################
# MAIN Thread, spun off when a room starts a game
#
#################################################
def room_quiz_thread(room, quiz_flag, quiz_timer):
    """Example of how to send server generated events to clients."""
    print("QUIZ TIME")

    QUESTION_URL = "https://opentdb.com/api.php?amount=" + str(quiz_flag)# + "&encode=url3986"
    QUESTIONS = None

    current_question = ""
    correct_answer = ""


    with urllib.request.urlopen(QUESTION_URL) as url:
        data = json.load(url)

        QUESTIONS = data["results"]
        # print(QUESTIONS)

    count = 0
    ### Check if there is questions in the list
    if quiz_flag: 
        for i in range(quiz_flag):
            count = i
            if count >= len(QUESTIONS):
                count = 0

            #############################################
            # Send the questions
            print("QUESTION ::: ", QUESTIONS[count])

            current_question = QUESTIONS[count]["question"]
            correct_answer = QUESTIONS[count]["correct_answer"]
            
            answers = QUESTIONS[count]["incorrect_answers"]
            answers.insert(0, correct_answer )
            random.shuffle(answers)

            question_l = ["Q"+str(count), "text_question", current_question, answers]
            convert_and_send_json(room, 'my_question', {'data': question_l, 'count': count})
            
            #############################################
            # Send the countdown
            for i in range(quiz_timer):
                print("Time left: ", str(quiz_timer - i))
                data_string = ["time_left", str(quiz_timer - i)]
                convert_and_send_json(room, 'my_countdown', {'data': data_string})
                
                socketio.sleep(1)
            
            #############################################
            # Send the answer and scoreboard
            print("ANSWER TIME")
            #### Send Real Answer and if they were correct:

            data_string = ["The correct answer was ", correct_answer]
            convert_and_send_json(room, 'my_question_answer', {'data': data_string})

            global room_thread
            for username, answer in threads_dict[room]["answers"].items():

                correct =  True if answer == correct_answer else False
                if(correct):
                    if(username in threads_dict[room]["points"].keys()):
                        threads_dict[room]["points"][username] = threads_dict[room]["points"][username] + 10
                    else:
                        threads_dict[room]["points"][username] = 10

                print("USER: ", username, " ANSWERED: ", answer, correct)


            ##### Send everyones points in leaderboard

            convert_and_send_json(room, 'my_leaderboard', {'data': threads_dict[room]["points"]})
            socketio.sleep(quiz_timer)

    #### When no 
    # else:
    #     socketio.emit('my_question',
    #                 {'data': "", 'count': -1},
    #                     to="hello_world")#

    threads_dict[room]["running"] = False

        

@app.route('/')
def index():
    return render_template('index.html', async_mode=socketio.async_mode)


# @socketio.event
# def my_event(message):
#     session['receive_count'] = session.get('receive_count', 0) + 1
#     emit('my_response',
#          {'data': message['data'], 'count': session['receive_count']})


# @socketio.event
# def my_broadcast_event(message):
#     session['receive_count'] = session.get('receive_count', 0) + 1
#     emit('my_response',
#          {'data': message['data'], 'count': session['receive_count']},
#          broadcast=True)


# @socketio.event
# def join(message):
#     join_room(message['room'])
#     session['receive_count'] = session.get('receive_count', 0) + 1
#     emit('my_response',
#          {'data': 'In rooms: ' + ', '.join(rooms()),
#           'count': session['receive_count']})


@socketio.event
def leave(message):
    print("Leave button, leaving: ", message['room'])
    leave_room(message['room'])
    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response',
         {'data': 'In rooms: ' + ', '.join(rooms()),
          'count': session['receive_count']})


@socketio.on('close_room')
def on_close_room(message):
    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response', {'data': 'Room ' + message['room'] + ' is closing.',
                         'count': session['receive_count']},
         to=message['room'])
    close_room(message['room'])


@socketio.event
def my_room_event(message):
    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response',
         {'data': message['data'], 'count': session['receive_count']},
         to=message['room'])


@socketio.event
def disconnect_request():
    @copy_current_request_context
    def can_disconnect():
        disconnect()

    session['receive_count'] = session.get('receive_count', 0) + 1
    # for this emit we use a callback function
    # when the callback function is invoked we know that the message has been
    # received and it is safe to disconnect
    emit('my_response',
         {'data': 'Disconnected!', 'count': session['receive_count']},
         callback=can_disconnect)


@socketio.event
def my_ping():
    emit('my_pong')


@socketio.event
def connect():
    global thread
    # with thread_lock:
    #     if thread is None:
    #         thread = socketio.start_background_task(background_thread)
    emit('my_response', {'data': 'Connected', 'count': 0})


@socketio.on('disconnect')
def test_disconnect():
    print('Client disconnected', request.sid)


@socketio.event
def name_join(message):

    username = message["username"]
    room = message["room"]
    print(username + " is Joining Room " + room)

    print("Currently in: ", rooms())
    for i in rooms():
        print("Leaving: ", i)
        leave_room(i)

    join_room(room)
    print("Now in: ", rooms())

    session['receive_count'] = session.get('receive_count', 0) + 1

    ## Check if room already exists:
    update_room_list(room, False)
    add_user_to_room(room, username)


@socketio.event
def start_room(message):
    print(message)
    room = message["room"]
    numofq =  message["numofq"]
    print(room + " is starting with " + numofq + " questions")

    quiz_flag = int(numofq)

    global room_thread
    print(room_thread)

    global threads_dict
    ## Check if room already exists:
    update_room_list(room, True, quiz_flag, 10)

    

@socketio.event
def my_answer(message):

    room = message["room"]
    username = message["username"]
    answer = message["answer"]

    print(message["username"] + " of " + message["room"] + " answered ", message["answer"]) 

    global room_thread
    threads_dict[room]["answers"][username] = answer


if __name__ == '__main__':
    socketio.run(app)