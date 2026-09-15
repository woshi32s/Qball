' Qball 监督进程启动器(登录时静默运行)
Set sh = CreateObject("WScript.Shell")
repo = "E:\proiects\emotion-ball"
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & repo & "\scripts\supervisor-daemon.ps1""", 0, False
