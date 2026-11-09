from fastapi import FastAPI

app = FastAPI(title="benefit-be")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
