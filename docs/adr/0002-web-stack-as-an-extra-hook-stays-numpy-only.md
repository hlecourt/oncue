# The web stack is an optional extra; the engine and the Bazarr hook stay numpy only

The API runs on FastAPI, Pydantic and uvicorn, installed through a `web` extra that the Docker image includes, because a generated OpenAPI contract and typed validation are worth a handful of locked dependencies for a real web front. The engine, the CLI and the Bazarr hook must import nothing from the web stack: the hook runs inside Bazarr's own Python, which has numpy but not FastAPI, so web imports are lazy (only `ui` and `serve` load them) and a CI job runs the hook in a numpy-only environment.

## Considered Options

- Keep the stdlib server with a hand-written OpenAPI file: no new dependencies, but hand-rolled validation for about twenty endpoints and TypeScript types that drift from the real API.
