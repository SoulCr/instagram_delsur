"""
Genera una historia de Instagram por cada combo de ofertas del día y
(opcionalmente) las publica.

Uso:
    python main.py                       -> solo genera las imágenes (modo prueba)
    python main.py --fecha 2026-10-14    -> prueba con las ofertas de otra fecha
    python main.py --publicar            -> genera, sube a Cloudinary y publica

ofertas.csv:  fecha,producto,precio,imagen
    - Cada fila es UNA historia. Todas las filas con la fecha del día se publican.
    - En "producto" separá los items con "+"  (ej: 2 kg pata muslo + 1 kg chori)
    - "imagen" es la foto de esa oferta (opcional; si falta, queda un recuadro claro)
"""
import argparse
import csv
import os
import time
from datetime import date
from functools import lru_cache
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

load_dotenv()

IG_USER_ID = os.getenv("IG_USER_ID")
TOKEN = os.getenv("ACCESS_TOKEN")
BASE = "https://graph.facebook.com/v21.0"

# ---- Ajustes de diseño (cambialos a gusto) ----
NOMBRE_NEGOCIO = "DEL SUR"
TITULO = "OFERTA"
TEXTO_PIE = "@delsurmercados_"
LOGO = "fotos/logo.png"  # insignia circular con fondo transparente
FUENTE = "fuentes/Poppins-ExtraBold.ttf"

AMARILLO = (244, 233, 53)        # amarillo del logo
AMARILLO_CLARO = (252, 244, 120)
AMARILLO_OSCURO = (240, 220, 35)
ROJO = (200, 45, 32)
OSCURO = (30, 26, 22)
BLANCO = (255, 255, 255)

CSV_OFERTAS = "ofertas.csv"
CARPETA_SALIDA = Path("salida")
W, H = 1080, 1920  # formato vertical 9:16


# ---------- Lectura de datos ----------
def leer_ofertas(fecha_iso):
    if not Path(CSV_OFERTAS).is_file():
        raise SystemExit(
            f"No encuentro {CSV_OFERTAS}. Crealo en la carpeta del proyecto "
            "con las columnas: fecha,producto,precio,imagen"
        )
    # utf-8-sig tolera archivos guardados con BOM (Excel, Bloc de notas, PowerShell)
    with open(CSV_OFERTAS, newline="", encoding="utf-8-sig") as f:
        primera_linea = f.readline()
        f.seek(0)
        # Excel en español suele guardar con ";" en vez de ","
        sep = ";" if primera_linea.count(";") > primera_linea.count(",") else ","
        return [fila for fila in csv.DictReader(f, delimiter=sep)
                if (fila.get("fecha") or "").strip() == fecha_iso]


# ---------- Utilidades de dibujo ----------
@lru_cache(maxsize=None)
def cargar_fuente(tamano):
    if Path(FUENTE).exists():
        return ImageFont.truetype(FUENTE, tamano)
    return ImageFont.load_default(tamano)


def fuente_que_entra(draw, texto, tamano_max, ancho_max):
    """Achica la letra hasta que el texto entre en el ancho disponible."""
    tamano = tamano_max
    while tamano > 30:
        fuente = cargar_fuente(tamano)
        if draw.textlength(texto, font=fuente) <= ancho_max:
            return fuente
        tamano -= 5
    return cargar_fuente(30)


def formatear_precio(valor):
    try:
        return "$" + f"{int(float(valor)):,}".replace(",", ".")
    except ValueError:
        return f"${valor}"


def mascara_redondeada(tam, radio, ss=3):
    """Máscara con esquinas redondeadas y bordes suaves."""
    m = Image.new("L", (tam[0] * ss, tam[1] * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (0, 0, tam[0] * ss - 1, tam[1] * ss - 1), radio * ss, fill=255)
    return m.resize(tam, Image.LANCZOS)


def rect_redondeado(base, caja, radio, color):
    x0, y0, x1, y1 = caja
    base.paste(color + (255,), (x0, y0, x1, y1),
               mascara_redondeada((x1 - x0, y1 - y0), radio))


def sombra(base, caja, radio, dy=14, blur=20, alpha=100):
    capa = Image.new("RGBA", base.size, (0, 0, 0, 0))
    x0, y0, x1, y1 = caja
    ImageDraw.Draw(capa).rounded_rectangle(
        (x0, y0 + dy, x1, y1 + dy), radio, fill=(0, 0, 0, alpha))
    base.alpha_composite(capa.filter(ImageFilter.GaussianBlur(blur)))


def crear_fondo():
    """Degradado amarillo con círculos decorativos suaves."""
    degradado = Image.linear_gradient("L").resize((W, H))
    fondo = Image.composite(Image.new("RGB", (W, H), AMARILLO_OSCURO),
                            Image.new("RGB", (W, H), AMARILLO_CLARO),
                            degradado).convert("RGBA")
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dc = ImageDraw.Draw(capa)
    dc.ellipse((-260, 1150, 460, 1870), fill=(255, 255, 255, 50))
    dc.ellipse((680, 250, 1420, 990), fill=(255, 255, 255, 45))
    dc.ellipse((-220, -220, 420, 420), fill=(200, 45, 32, 28))
    fondo.alpha_composite(capa)
    return fondo


# ---------- Generación de la imagen ----------
def crear_imagen(oferta, ruta_salida):
    base = crear_fondo()
    d = ImageDraw.Draw(base)
    margen = 90

    # --- Logo ---
    diam, cy = 290, 195
    caja_logo = ((W - diam) // 2, cy - diam // 2, (W + diam) // 2, cy + diam // 2)
    ruta_logo = Path(LOGO)
    if ruta_logo.is_file():
        sombra(base, caja_logo, diam // 2, dy=10, blur=16, alpha=110)
        logo = Image.open(ruta_logo).convert("RGBA").resize((diam, diam), Image.LANCZOS)
        base.paste(logo, (caja_logo[0], caja_logo[1]), logo)
    else:
        print(f"Aviso: no se encontró el logo {LOGO}, se usa el nombre del negocio.")
        d.text((W // 2, cy), NOMBRE_NEGOCIO, font=cargar_fuente(120),
               fill=ROJO, anchor="mm")

    # --- Cinta roja con el título ---
    fuente_t = cargar_fuente(78)
    ancho_t = int(d.textlength(TITULO, font=fuente_t)) + 150
    caja_t = ((W - ancho_t) // 2, 365, (W + ancho_t) // 2, 475)
    sombra(base, caja_t, 55, dy=8, blur=12, alpha=90)
    rect_redondeado(base, caja_t, 55, ROJO)
    d.text((W // 2, 420), TITULO, font=fuente_t, fill=BLANCO, anchor="mm")

    # --- Foto con marco blanco ---
    caja_foto = (margen, 520, W - margen, 1110)
    sombra(base, caja_foto, 44, dy=16, blur=22, alpha=110)
    rect_redondeado(base, caja_foto, 44, BLANCO)
    interior = (caja_foto[0] + 14, caja_foto[1] + 14, caja_foto[2] - 14, caja_foto[3] - 14)
    tam = (interior[2] - interior[0], interior[3] - interior[1])
    nombre_foto = (oferta.get("imagen") or "").strip()
    ruta_foto = Path(nombre_foto)
    if nombre_foto and ruta_foto.is_file():
        foto = Image.open(ruta_foto).convert("RGB")
        foto = ImageOps.fit(foto, tam).convert("RGBA")
        base.paste(foto, (interior[0], interior[1]), mascara_redondeada(tam, 34))
    else:
        print(f"Aviso: sin foto para '{oferta['producto']}' (imagen: '{nombre_foto}').")
        rect_redondeado(base, interior, 34, (250, 246, 205))

    # --- Tarjeta blanca con los items del combo ---
    caja_card = (margen, 1150, W - margen, 1500)
    sombra(base, caja_card, 44, dy=14, blur=20, alpha=100)
    rect_redondeado(base, caja_card, 44, BLANCO)
    partes = [p.strip() for p in oferta["producto"].split("+") if p.strip()]
    lineas = [partes[0]] + ["+ " + p for p in partes[1:]]
    centro_card = (caja_card[1] + caja_card[3]) / 2 - 25  # deja lugar al precio
    for i, linea in enumerate(lineas):
        y = centro_card + (i - (len(lineas) - 1) / 2) * 100
        fuente = fuente_que_entra(d, linea, 84, W - 2 * margen - 80)
        color = ROJO if i > 0 else OSCURO
        d.text((W // 2, y), linea, font=fuente, fill=color, anchor="mm")

    # --- Precio en una píldora roja ---
    precio = formatear_precio(oferta["precio"])
    fuente_p = fuente_que_entra(d, precio, 170, W - 2 * margen - 160)
    ancho_p = int(d.textlength(precio, font=fuente_p)) + 150
    caja_p = ((W - ancho_p) // 2, 1450, (W + ancho_p) // 2, 1670)
    sombra(base, caja_p, 110, dy=14, blur=18, alpha=120)
    rect_redondeado(base, caja_p, 110, ROJO)
    d.text((W // 2, 1560), precio, font=fuente_p, fill=BLANCO, anchor="mm")

    # --- Pie ---
    d.rectangle((0, 1790, W, H), fill=OSCURO)
    d.text((W // 2, 1855), TEXTO_PIE, font=cargar_fuente(50),
           fill=AMARILLO, anchor="mm")

    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(ruta_salida, quality=95)
    return ruta_salida


# ---------- Publicación (solo con --publicar) ----------
def subir_a_cloudinary(ruta):
    import cloudinary.uploader  # lee CLOUDINARY_URL del entorno
    return cloudinary.uploader.upload(str(ruta))["secure_url"]


def _post(endpoint, datos):
    r = requests.post(f"{BASE}/{IG_USER_ID}/{endpoint}", data=datos, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Error de Instagram ({r.status_code}): {r.text}")
    return r.json()


def publicar_historia(image_url):
    contenedor = _post("media", {
        "image_url": image_url,
        "media_type": "STORIES",
        "access_token": TOKEN,
    })
    return _post("media_publish", {
        "creation_id": contenedor["id"],
        "access_token": TOKEN,
    })


# ---------- Programa principal ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha", default=date.today().isoformat(),
                        help="fecha de las ofertas (AAAA-MM-DD), por defecto hoy")
    parser.add_argument("--publicar", action="store_true",
                        help="subir a Cloudinary y publicar en Instagram")
    args = parser.parse_args()

    if not Path(FUENTE).exists():
        print(f"Aviso: no se encontró {FUENTE}, se usa la fuente por defecto.")

    ofertas = leer_ofertas(args.fecha)
    if not ofertas:
        print(f"No hay ofertas cargadas para {args.fecha}.")
        return

    print(f"{len(ofertas)} historia(s) para {args.fecha}.")
    for i, oferta in enumerate(ofertas, start=1):
        ruta = crear_imagen(oferta, CARPETA_SALIDA / f"historia_{args.fecha}_{i}.jpg")
        print(f"[{i}/{len(ofertas)}] Imagen generada: {ruta}")

        if args.publicar:
            url = subir_a_cloudinary(ruta)
            print("    Subida a Cloudinary:", url)
            print("    Publicado en Instagram:", publicar_historia(url))
            time.sleep(5)  # pausa corta entre historias, para que salgan en orden


if __name__ == "__main__":
    main()