@echo off
cd /d "%~dp0"
py -3.13 -B -X utf8 -m campaign.visualizer %*
if errorlevel 1 pause
