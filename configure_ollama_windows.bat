@echo off
setlocal EnableExtensions
echo Configuring Ollama for local development with one loaded GPU/model...
setx OLLAMA_MAX_LOADED_MODELS 1 >nul
setx OLLAMA_NUM_PARALLEL 1 >nul
echo Environment variables configured for new Windows sessions.
echo Restart Ollama to apply the changes.
