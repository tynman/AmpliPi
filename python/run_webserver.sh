#!/bin/bash
# for some reason flask loads the app twice if reload option enabled (by default with debugging)
FLASK_ENV=development FLASK_DEBUG=1 flask run --host 0.0.0.0 --no-reload
