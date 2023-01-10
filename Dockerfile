FROM python:3.8-slim
# Use the python latest image
COPY . ./
# Copy the current folder content into the docker image
RUN pip install -r requirements.txt
# Install the required packages of the application
# CMD gunicorn --bind :$PORT app:app
CMD exec gunicorn -k gevent --bind :$PORT --workers 1 --timeout 0 main:app
# Bind the port and refer to the app.py app
