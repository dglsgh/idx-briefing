#!/bin/zsh
# Wrapper so launchd gets the API keys from ~/.zprofile
source ~/.zprofile
exec /usr/bin/python3 /Users/dglsgh/Downloads/idx-briefing/generate_briefing.py
