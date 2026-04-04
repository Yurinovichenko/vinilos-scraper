"""
send_email.py — Envío del reporte semanal por Gmail SMTP.

Lógica de adjunto:
  - Excel < 20MB → adjunto directo
  - Excel 20–25MB → comprimir a .zip
  - Excel > 25MB → link a GitHub Pages, sin adjunto

Configuración via variables de entorno:
  GMAIL_USER         → yyurac@gmail.com
  GMAIL_APP_PASSWORD → contraseña de aplicación (16 chars)
  GITHUB_PAGES_URL   → URL base de GitHub Pages (opcional)
"""

import logging
import os
import smtplib
import zipfile
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from scrapers.base import Product, ScrapeError

logger = logging.getLogger(__name__)

RECIPIENT = "yyurac@gmail.com"
MAX_ATTACH_MB = 20
MAX_ZIP_MB = 25

GITHUB_PAGES_URL = os.environ.get(
    "GITHUB_PAGES_URL",
    "https://tu-usuario.github.io/vinilos-scraper"
)


def send_report(
    xlsx_path: Path,
    products: list[Product],
    errors: list[ScrapeError],
    sanity_alerts: list[str],
    run_date: str,
    results_by_store: dict[str, int],
    last_stats: dict,
) -> bool:
    """
    Envía el reporte semanal por email.
    Retorna True si el envío fue exitoso.
    """
    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not gmail_user or not gmail_pass:
        logger.warning("GMAIL_USER / GMAIL_APP_PASSWORD no configurados. Email no enviado.")
        return False

    subject = f"Vinilos Chile — Actualización {run_date}"
    html_body = _build_html_body(
        products, errors, sanity_alerts, run_date, results_by_store, last_stats
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = RECIPIENT
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Determinar si adjuntar Excel
    attach_path, attach_note = _resolve_attachment(xlsx_path)
    if attach_path:
        try:
            with open(attach_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=attach_path.name)
            part["Content-Disposition"] = f'attachment; filename="{attach_path.name}"'
            msg.attach(part)
            logger.info(f"Adjuntando {attach_path.name} ({attach_path.stat().st_size / 1e6:.1f} MB)")
        except Exception as e:
            logger.error(f"Error adjuntando archivo: {e}")
            attach_note = f"Error al adjuntar archivo: {e}"
    else:
        logger.info(f"Excel no adjuntado: {attach_note}")

    # Añadir nota sobre adjunto al final del cuerpo si es necesario
    if attach_note and not attach_path:
        extra = f"<p style='color:#888;font-size:12px'>{attach_note}</p>"
        msg_with_note = MIMEMultipart("alternative")
        msg_with_note["Subject"] = subject
        msg_with_note["From"] = gmail_user
        msg_with_note["To"] = RECIPIENT
        msg_with_note.attach(MIMEText(html_body + extra, "html", "utf-8"))
        msg = msg_with_note

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(gmail_user, gmail_pass)
            smtp.sendmail(gmail_user, [RECIPIENT], msg.as_string())
        logger.info(f"Email enviado a {RECIPIENT}")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("Error de autenticación Gmail. Verificar GMAIL_APP_PASSWORD.")
        return False
    except Exception as e:
        logger.error(f"Error enviando email: {e}")
        return False


def _resolve_attachment(xlsx_path: Path) -> tuple[Optional[Path], str]:
    """
    Retorna (ruta_a_adjuntar, nota).
    - Si Excel < 20MB → (xlsx_path, "")
    - Si 20–25MB → comprimir y retornar (zip_path, "")
    - Si > 25MB → (None, nota con link)
    """
    if not xlsx_path or not xlsx_path.exists():
        return None, "Archivo Excel no encontrado."

    size_mb = xlsx_path.stat().st_size / (1024 * 1024)

    if size_mb < MAX_ATTACH_MB:
        return xlsx_path, ""

    # Intentar comprimir
    zip_path = xlsx_path.with_suffix(".xlsx.zip")
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(xlsx_path, xlsx_path.name)
        zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
        if zip_size_mb < MAX_ZIP_MB:
            return zip_path, ""
        else:
            zip_path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"No se pudo comprimir Excel: {e}")

    return None, (
        f"El archivo Excel ({size_mb:.0f} MB) supera el límite de adjunto. "
        f"Descárgalo desde: {GITHUB_PAGES_URL}"
    )


def _build_html_body(
    products: list[Product],
    errors: list[ScrapeError],
    sanity_alerts: list[str],
    run_date: str,
    results_by_store: dict[str, int],
    last_stats: dict,
) -> str:
    """Construye el cuerpo HTML del email."""
    total = len(products)
    available = sum(1 for p in products if p.available)
    pct_available = f"{available / total:.0%}" if total > 0 else "0%"
    stores_ok = len(results_by_store)

    prev_stores = last_stats.get("stores", {})
    prev_total = sum(s.get("count", 0) for s in prev_stores.values())
    delta_total = total - prev_total
    delta_str = f"+{delta_total:,}" if delta_total >= 0 else f"{delta_total:,}"

    # Tabla por tienda
    store_rows = ""
    for store_name in sorted(results_by_store.keys()):
        count = results_by_store[store_name]
        prev_count = prev_stores.get(store_name, {}).get("count", 0)
        delta = count - prev_count
        delta_cell = f"+{delta}" if delta > 0 else str(delta)
        delta_color = "#4caf50" if delta > 0 else ("#f44336" if delta < 0 else "#888")
        store_rows += f"""
        <tr>
          <td style="padding:5px 10px;border-bottom:1px solid #333">{store_name}</td>
          <td style="padding:5px 10px;border-bottom:1px solid #333;text-align:right">{count:,}</td>
          <td style="padding:5px 10px;border-bottom:1px solid #333;text-align:right;color:{delta_color}">{delta_cell}</td>
        </tr>"""

    # Sección de errores
    error_section = ""
    if errors:
        error_rows = "".join(
            f"<li style='margin:3px 0'><b>{e.store_name}</b>: {e.error_type} — {e.message[:100]}</li>"
            for e in errors
        )
        error_section = f"""
        <h3 style="color:#f44336;margin:20px 0 8px">⚠️ Tiendas con problemas ({len(errors)})</h3>
        <ul style="color:#ccc;font-size:13px">{error_rows}</ul>"""

    # Alertas sanity check
    alerts_section = ""
    if sanity_alerts:
        alerts_rows = "".join(
            f"<li style='margin:3px 0'>{a}</li>"
            for a in sanity_alerts
        )
        alerts_section = f"""
        <h3 style="color:#ff9800;margin:20px 0 8px">⚠️ Alertas de datos</h3>
        <ul style="color:#ccc;font-size:13px">{alerts_rows}</ul>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="background:#111;color:#e8e8e8;font-family:-apple-system,sans-serif;max-width:640px;margin:0 auto;padding:20px">

  <h1 style="color:#e8c840;border-bottom:2px solid #333;padding-bottom:12px">
    🎵 Vinilos Chile — {run_date}
  </h1>

  <div style="display:flex;gap:16px;margin:16px 0;flex-wrap:wrap">
    <div style="background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:14px 20px;flex:1;min-width:120px">
      <div style="font-size:28px;font-weight:700;color:#e8c840">{total:,}</div>
      <div style="color:#888;font-size:12px">vinilos ({delta_str} vs anterior)</div>
    </div>
    <div style="background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:14px 20px;flex:1;min-width:120px">
      <div style="font-size:28px;font-weight:700;color:#4caf50">{available:,}</div>
      <div style="color:#888;font-size:12px">disponibles ({pct_available})</div>
    </div>
    <div style="background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:14px 20px;flex:1;min-width:120px">
      <div style="font-size:28px;font-weight:700;color:#e8c840">{stores_ok}</div>
      <div style="color:#888;font-size:12px">tiendas activas</div>
    </div>
  </div>

  <p style="margin:12px 0">
    <a href="{GITHUB_PAGES_URL}" style="color:#e8c840">🔍 Ver y buscar todos los vinilos online →</a>
  </p>

  <h3 style="margin:20px 0 8px">Resultados por tienda</h3>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="color:#888">
        <th style="padding:5px 10px;text-align:left;border-bottom:1px solid #444">Tienda</th>
        <th style="padding:5px 10px;text-align:right;border-bottom:1px solid #444">Productos</th>
        <th style="padding:5px 10px;text-align:right;border-bottom:1px solid #444">Δ vs anterior</th>
      </tr>
    </thead>
    <tbody>{store_rows}</tbody>
  </table>

  {error_section}
  {alerts_section}

  <p style="color:#555;font-size:11px;margin-top:30px;border-top:1px solid #333;padding-top:12px">
    Generado automáticamente por Vinilos Scraper v2.0 · GitHub Actions
  </p>
</body>
</html>"""
