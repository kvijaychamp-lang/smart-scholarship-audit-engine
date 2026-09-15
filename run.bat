@echo off
setlocal
cd /d "%~dp0"

echo Starting MoTA Scholarship Verification Dashboard...

if exist "venv\Scripts\activate.bat" (
    echo Activating virtual environment: venv
    call "venv\Scripts\activate.bat"
) else if exist ".venv\Scripts\activate.bat" (
    echo Activating virtual environment: .venv
    call ".venv\Scripts\activate.bat"
) else (
    echo No virtual environment found. Using the global Python environment.
)

echo Opening dashboard at http://localhost:8501
start "" "http://localhost:8501"

echo Launching Streamlit...
streamlit run app.py

if errorlevel 1 (
    echo.
    echo ERROR: The Streamlit dashboard could not be started.
    echo Check that Python, Streamlit, and the project dependencies are installed.
    pause
) else (
    echo.
    echo The dashboard has stopped.
    pause
)
endlocal
