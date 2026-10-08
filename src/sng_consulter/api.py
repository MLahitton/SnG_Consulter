from fastapi import FastAPI

from sng_consulter import __version__

app = FastAPI(title="SnG Consulter", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
