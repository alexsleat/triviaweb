FROM python:3.8-slim

# Copy the current folder content into the docker image
COPY . ./

# Install the required packages of the application
RUN pip install -r requirements.txt

# Run gunicorn, with eventlet for socketio
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--worker-class", "eventlet", "app:app"]