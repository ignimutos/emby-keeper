#!/bin/bash

set -e

exec "embykeeper" "--basedir" "/app" "$@"
