@echo off
pyinstaller --onefile --additional-hooks-dir=. main.py
