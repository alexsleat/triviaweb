from threading import Lock
from flask import Flask, render_template, session, request, \
    copy_current_request_context
from flask_socketio import SocketIO, emit, join_room, leave_room, \
    close_room, rooms, disconnect
import urllib.request, json 
from html.parser import HTMLParser


# QUESTIONS = [
#     ["Q01", "text_question", "what is the answer", ["one", "two","three", "four", "five"]],
#     ["Q02", "text_question", "vad ar svaret", ["ett", "tva", "tre", "fyra", "fem"]],
#     ["Q03", "text_question", "tre svarar fragar", ["1", "2","3"]],
#     ["Q04", "text_question", "who is champion", ["alex", "mom","dad"]],
#     ["Q05", "text_question", "who is the dog", ["ted", "snowy"]],
#     ["Q06", "text_question", "who is the cat", ["snowy", "ted"]],
# ]

# QUESTION_URL = "https://opentdb.com/api.php?amount=10"
# QUESTIONS_JSON = None
# with urllib.request.urlopen(QUESTION_URL) as url:
#     data = json.load(url)
#     QUESTIONS_JSON = data["results"]
#     print(QUESTIONS_JSON)



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

# quiz_flag = None
# quiz_timer = 5

def background_thread():
    """Example of how to send server generated events to clients."""
    count = 0
    while True:

        ### Check if there is questions in the list
        if True: 
            for i in range(5):
                count = i
                if count >= len(QUESTIONS):
                    count = 0

                q = json.dumps(QUESTIONS[count], separators=(',', ':'))
                print("QUESTION TIME: ", QUESTIONS[count], q)
                socketio.emit('my_question',
                            {'data': q, 'count': count},
                                to="hello_world")

        #### When no 
        else:
            socketio.emit('my_question',
                        {'data': "", 'count': -1},
                            to="hello_world")

        socketio.sleep(5)

###############################################
# MAIN Thread
#
#################################################
def room_quiz_thread(room, quiz_flag, quiz_timer):
    """Example of how to send server generated events to clients."""
    print("QUIZ TIME")

    QUESTION_URL = "https://opentdb.com/api.php?amount=" + str(quiz_flag)# + "&encode=url3986"
    QUESTIONS = None
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

            #q = json.dumps(QUESTIONS[count], separators=(',', ':'))
            print("QUESTION ::: ", QUESTIONS[count])
            answers = QUESTIONS[count]["incorrect_answers"]
            answers.insert(0, QUESTIONS[count]["correct_answer"] )
            q_string = ["Q"+str(count), "text_question", QUESTIONS[count]["question"], answers]

            print(q_string)
            q = json.dumps(q_string, separators=(',', ':'))

            print("QUESTION TIME: ", QUESTIONS[count], q)
            socketio.emit('my_question',
                            {'data': q, 'count': count},
                            to=room)

            socketio.sleep(quiz_timer)

    #### When no 
    else:
        socketio.emit('my_question',
                    {'data': "", 'count': -1},
                        to="hello_world")

        

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
    print(message["username"] + " is Joining Room " + message["room"])

    print("Currently in: ", rooms())
    for i in rooms():
        print("Leaving: ", i)
        leave_room(i)

    join_room(message['room'])
    print("Now in: ", rooms())

    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response',
         {'data': 'In rooms: ' + ', '.join(rooms()),
          'count': session['receive_count']})

@socketio.event
def start_room(message):
    print(message)
    room = message["room"]
    numofq =  message["numofq"]
    print(room + " is starting with " + numofq + " questions")

    quiz_flag = int(numofq)

    global room_thread
    print(room_thread)

    room_thread.append( socketio.start_background_task(room_quiz_thread(room, quiz_flag, 5)) )
    

@socketio.event
def my_answer(message):
    print("Answer: ", message)    

if __name__ == '__main__':
    socketio.run(app)