@echo off
cd /d "%~dp0"
python value_betting_analyzer.py >> analyzer.log 2>&1
