from threading import Lock
from flask import Flask, render_template, session, request, \
    copy_current_request_context
from flask_socketio import SocketIO, emit, join_room, leave_room, \
    close_room, rooms, disconnect


# list of questions.
# ## Each questions is a list: 0: UID, 1: title, 2: response options
QUESTIONS = [
    ["Q01", "radio_text", "Questions 1", ["A1", "A2", "A3", "A4", "A5"]],
    ["Q03", "radio_img", "Emoji", ["1", "2","3", "4", "5"]]
]

async_mode = None

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode=async_mode)
thread = None
thread_lock = Lock()


def background_thread():
    """Example of how to send server generated events to clients."""
    count = 0
    while True:
        socketio.sleep(10)
        count += 1
        socketio.emit('my_response',
                      {'data': 'Server generated event', 'count': count})

######################
# Routes
######################

## Home ####################
@app.route('/', methods=['POST', 'GET'])
def home():
    if request.method == 'POST':
        user = request.form['nm']
        return redirect(url_for('success', name = user))

    return render_template('start.html')

## Bell ####################
@app.route('/bell')
def bell():
    return render_template('bell.html')

## Questions Page ####################
@app.route('/questions', methods=['POST', 'GET'])
def showQuestions():
    data = {}

    # Check if data is coming back from the form:
    if request.method == 'POST':
        print("Responses: ")
        response = {}
        for i in range(len(QUESTIONS)):
            current_q = QUESTIONS[i][0]
            content = request.form[current_q]

            ### Responses from the webpage are handled here:
            print(current_q, content)

        try:
            #return jsonify({'page': 'questions', 'success': True, 'responses' : data})
            #return render_template('submittedandredirect.html')
            return render_template('bell.html')
        except Exception as exc:
            print("Error executing SQL: %s"%exc)
            return jsonify({'page': 'list', 'success': False})
            
    # Otherwise we show the questions:
    return render_template('index.html', questions=QUESTIONS)

## Room ####################
@app.route('/room')
def room():
    return render_template('quiz.html',  async_mode=socketio.async_mode)

@socketio.event
def my_event(message):
    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response',
         {'data': message['data'], 'count': session['receive_count']})


@socketio.event
def my_broadcast_event(message):
    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response',
         {'data': message['data'], 'count': session['receive_count']},
         broadcast=True)


@socketio.event
def join(message):
    join_room(message['room'])
    session['receive_count'] = session.get('receive_count', 0) + 1
    emit('my_response',
         {'data': 'In rooms: ' + ', '.join(rooms()),
          'count': session['receive_count']})


@socketio.event
def leave(message):
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
    with thread_lock:
        if thread is None:
            thread = socketio.start_background_task(background_thread)
    emit('my_response', {'data': 'Connected', 'count': 0})


@socketio.on('disconnect')
def test_disconnect():
    print('Client disconnected', request.sid)


if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0")