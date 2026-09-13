@echo off
cd /d %~dp0
if exist EmotionBall.exe (
  start "" EmotionBall.exe
) else (
  python server.py
)
