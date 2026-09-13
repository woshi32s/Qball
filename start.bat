@echo off
cd /d %~dp0
if exist Qball.exe (
  start "" Qball.exe
) else (
  if exist EmotionBall.exe (
    start "" EmotionBall.exe
  ) else (
    python server.py
  )
)
