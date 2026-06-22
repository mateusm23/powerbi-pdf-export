import asyncio
import json
import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.exporter import export_to_pdf

OBRAS_PATH = Path(__file__).resolve().parent.parent / "obras.json"
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://mateusm23.github.io")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["GET"],
    allow_headers=["*"],
)

export_lock = asyncio.Lock()


def _load_obras() -> list[dict]:
    with open(OBRAS_PATH, encoding="utf-8") as f:
        return json.load(f)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/obras")
def list_obras():
    obras = _load_obras()
    return [{"slug": o["slug"], "name": o["name"]} for o in obras]


@app.get("/export")
async def export(obra: str):
    obras = _load_obras()
    match = next((o for o in obras if o["slug"] == obra), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Obra '{obra}' não encontrada")

    if export_lock.locked():
        return JSONResponse(
            status_code=429,
            content={"error": "Já existe uma exportação em andamento. Tente novamente em alguns minutos."},
        )

    async with export_lock:
        loop = asyncio.get_event_loop()
        try:
            pdf_bytes = await asyncio.wait_for(
                loop.run_in_executor(None, export_to_pdf, match["url"]),
                timeout=480,
            )
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=504,
                content={"error": "A geração do PDF demorou demais e foi cancelada. Tente novamente."},
            )
        except PlaywrightTimeoutError:
            return JSONResponse(
                status_code=502,
                content={"error": "Não foi possível carregar o relatório do Power BI. Tente novamente."},
            )
        except Exception:
            return JSONResponse(
                status_code=502,
                content={"error": "Falha ao gerar o PDF. Tente novamente."},
            )

    filename = f"{obra}-{date.today().isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
