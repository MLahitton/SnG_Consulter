from fastapi import FastAPI

from sng_consulter import __version__
from sng_consulter.review.routes import router as review_router

app = FastAPI(title="SnG Consulter", version=__version__)
app.include_router(review_router)

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
