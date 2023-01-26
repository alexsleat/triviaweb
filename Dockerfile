FROM python:3.8-slim
# Use the python latest image
COPY . ./
# Copy the current folder content into the docker image
RUN pip install -r requirements.txt
# Install the required packages of the application
# CMD gunicorn app:app -w 2 --threads 2 -b $TW_IP:$TW_PORT

CMD ["gunicorn"  , "-b", "0.0.0.0:8000", "app:app"]