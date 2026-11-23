@echo off
rem Runs a host and a client of the patched game on one Windows PC, to test online play without a second PC.
rem Both copies share the save folder %LOCALAPPDATA%\DELTARUNE, so each one is started with DRONLINE_ROLE set:
rem it then reads its own dronline_<role>.ini and writes its own drnet_<role>.log there (see docs/DESIGN.md).
rem The two ini files are created on the first run; edit them afterwards (for example lag=40) as you like.
rem Keyboard input goes to the focused window: focus the host window to play P1, the client window for P2.
rem In each window choose Chapter 5 in the chapter select; the host waits up to 2 minutes for the client.
rem
rem Usage: tools\local_test.bat "<game folder with DELTARUNE.exe and the patched chapter5_windows>"
rem        (without an argument: the current folder)
setlocal
set "GAME=%~1"
if "%GAME%"=="" set "GAME=%CD%"
if not exist "%GAME%\DELTARUNE.exe" (
    echo No DELTARUNE.exe in "%GAME%"
    exit /b 1
)
set "SAVE=%LOCALAPPDATA%\DELTARUNE"
if not exist "%SAVE%" mkdir "%SAVE%"
if not exist "%SAVE%\dronline_host.ini" (
    > "%SAVE%\dronline_host.ini" (
        echo [online]
        echo mode=host
        echo players=2
        echo port=7845
        echo delay=auto
        echo seed=1234
    )
)
if not exist "%SAVE%\dronline_client.ini" (
    > "%SAVE%\dronline_client.ini" (
        echo [online]
        echo mode=client
        echo player=2
        echo players=2
        echo host=127.0.0.1
        echo port=7845
        echo delay=auto
        echo seed=1234
    )
)
set "DRONLINE_ROLE=host"
start "" /D "%GAME%" "%GAME%\DELTARUNE.exe"
rem wait 3 s (ping instead of timeout, which fails when started from another script)
ping -n 4 127.0.0.1 >nul
set "DRONLINE_ROLE=client"
start "" /D "%GAME%" "%GAME%\DELTARUNE.exe"
echo Started host and client.
echo Settings: "%SAVE%\dronline_host.ini" and "%SAVE%\dronline_client.ini"
echo Logs:     "%SAVE%\drnet_host.log" and "%SAVE%\drnet_client.log"
