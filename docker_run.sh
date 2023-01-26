#!/bin/bash

docker run \
    -p 80:8000 \
    --rm \
    --name triviaweb \
    docker_triviaweb       