"""Route d'export : GET /api/communes/{insee}/rapport.pdf"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import report

router = APIRouter(prefix="/api/communes/{insee}", tags=["rapport"])


@router.get("/rapport.pdf")
def rapport_pdf(insee: str):
    try:
        pdf_bytes = report.build_report_pdf(insee)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="firemap-{insee}.pdf"'},
    )
