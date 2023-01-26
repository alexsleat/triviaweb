#!/bin/bash

docker run \
    -e TW_IP=0.0.0.0 -e TW_PORT=6666 \
    -p 6666:6666 \
    --rm \
    --name triviaweb \
    docker_triviaweb       