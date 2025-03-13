#!/bin/bash
gunicorn -b 0.0.0.0:5000 oura_oauth_server:app
