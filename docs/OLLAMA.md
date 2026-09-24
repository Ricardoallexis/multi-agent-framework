# Using Ollama for local inference

Multi-Agent Framework can use Ollama as its local model provider. Cloud API keys are not required for workflows that can be completed entirely with local models.

## 1. Install Ollama

Install Ollama for your operating system from the official Ollama distribution and make sure its service is running.

Verify the CLI is available:

```bash
ollama --version
```

## 2. Download a model

The public baseline defaults to `qwen3:8b`:

```bash
ollama pull qwen3:8b
```

Confirm that the model is installed:

```bash
ollama list
```

You may use another compatible model by changing `OLLAMA_MODEL` in `.local/.env`.

## 3. Configure Multi-Agent Framework

Project setup creates `.local/.env` from `.env.example`. The default Ollama configuration is:

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:8b
OLLAMA_NUM_CTX=4096
OLLAMA_KEEP_ALIVE=15m
OLLAMA_TIMEOUT_SECONDS=120
OLLAMA_CONTEXT_SOFT_LIMIT=0.75
```

`OLLAMA_MODEL` should match a model reported by `ollama list`.

If Ollama is exposed at another reachable API endpoint, update `OLLAMA_BASE_URL` accordingly.

## 4. Check the connection

Windows:

```powershell
.venv\Scripts\multiagent.exe doctor
```

Linux / macOS:

```bash
multiagent doctor
```

The `ollama` section of the diagnostic report includes availability, installed models, the configured model, and whether that model is installed.

For a real structured-generation probe, run:

Windows:

```powershell
.venv\Scripts\multiagent.exe doctor --deep
```

Linux / macOS:

```bash
multiagent doctor --deep
```

`doctor --deep` performs an actual local model request when Ollama is available.

## 5. Run a workflow in local mode

The normal `run` command communicates with the backend, so start it first.

Windows:

```powershell
run_backend.bat
```

Linux / macOS:

```bash
python main.py
```

Then, from another terminal, force local routing with `--mode local`.

Windows:

```powershell
.venv\Scripts\multiagent.exe run `
  --project "Local Example" `
  --objective "Create an educational post" `
  --topic "local AI orchestration" `
  --platform LinkedIn `
  --mode local
```

Linux / macOS:

```bash
multiagent run \
  --project "Local Example" \
  --objective "Create an educational post" \
  --topic "local AI orchestration" \
  --platform LinkedIn \
  --mode local
```

The workflow still follows its normal contracts and human checkpoints; `--mode local` tells the router to use local model execution rather than cloud execution.

## Windows GPU-friendly defaults

The repository includes:

```powershell
configure_ollama_windows.bat
```

It configures these user-level Ollama environment variables for new Windows sessions:

```text
OLLAMA_MAX_LOADED_MODELS=1
OLLAMA_NUM_PARALLEL=1
```

Restart Ollama after running the script so the settings take effect.

These conservative defaults are intended to reduce simultaneous model loading and parallel requests on development machines with limited GPU memory.

## Troubleshooting

### Ollama is reported as unavailable

Check that Ollama is running and that `OLLAMA_BASE_URL` points to its reachable API endpoint.

### The configured model is not installed

If `doctor` reports `expected_model_installed: false`, install the configured model:

```bash
ollama pull qwen3:8b
```

or change `OLLAMA_MODEL` to one already shown by:

```bash
ollama list
```

### A local request times out

Try a smaller model or increase `OLLAMA_TIMEOUT_SECONDS` in `.local/.env`.

### Windows settings do not appear in `doctor`

Environment variables created by `setx` are visible only to new processes. Restart Ollama and open a new terminal after running `configure_ollama_windows.bat`.

## Privacy note

Local inference reduces the need to send model prompts to a cloud provider, but the framework may still use cloud services when a workflow or routing mode explicitly requires them. Use `--mode local` for runs that should stay on the local model path, and review workflow/provider configuration when handling sensitive material.
