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
    - "imagen" es la foto de esa oferta (opcional; si falta, se deja un recuadro gris)
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
from PIL import Image, ImageDraw, ImageFont, ImageOps

load_dotenv()

IG_USER_ID = os.getenv("IG_USER_ID")
TOKEN = os.getenv("ACCESS_TOKEN")
BASE = "https://graph.facebook.com/v21.0"

# ---- Ajustes de diseño (cambialos a gusto) ----
NOMBRE_NEGOCIO = "DEL SUR"
TITULO = "OFERTA"
TEXTO_PIE = "@delsurmercados_"
LOGO = "fotos/logo.png"  # insignia circular con fondo transparente
FUENTE = "fuentes/Montserrat-Bold.ttf"  # cambiá por la fuente que pusiste en /fuentes
COLOR_FONDO = (25, 25, 25)
COLOR_TEXTO = (255, 255, 255)
COLOR_PRECIO = (244, 233, 53)  # amarillo del logo

CSV_OFERTAS = "ofertas.csv"
CARPETA_SALIDA = Path("salida")
W, H = 1080, 1920  # formato vertical 9:16


# ---------- Lectura de datos ----------
def leer_ofertas(fecha_iso):
    with open(CSV_OFERTAS, newline="", encoding="utf-8") as f:
        return [fila for fila in csv.DictReader(f) if fila["fecha"] == fecha_iso]


# ---------- Generación de la imagen ----------
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


def pegar_logo(fondo, d, diametro, centro_y):
    ruta = Path(LOGO)
    if ruta.is_file():
        logo = Image.open(ruta).convert("RGBA").resize((diametro, diametro), Image.LANCZOS)
        fondo.paste(logo, ((W - diametro) // 2, centro_y - diametro // 2), logo)
    else:
        print(f"Aviso: no se encontró el logo {LOGO}, se usa el nombre del negocio.")
        d.text((W // 2, centro_y), NOMBRE_NEGOCIO, font=cargar_fuente(110),
               fill=COLOR_PRECIO, anchor="mm")


def crear_imagen(oferta, ruta_salida):
    fondo = Image.new("RGB", (W, H), COLOR_FONDO)
    d = ImageDraw.Draw(fondo)
    margen = 80
    ancho_texto = W - 2 * margen

    # Logo y título
    pegar_logo(fondo, d, diametro=300, centro_y=210)
    d.text((W // 2, 420), TITULO, font=cargar_fuente(85),
           fill=COLOR_TEXTO, anchor="mm")

    # Foto de la oferta (recortada para llenar el recuadro)
    caja = (margen, 500, W - margen, 1120)
    nombre_foto = (oferta.get("imagen") or "").strip()
    ruta_foto = Path(nombre_foto)
    if nombre_foto and ruta_foto.is_file():
        foto = Image.open(ruta_foto).convert("RGB")
        foto = ImageOps.fit(foto, (caja[2] - caja[0], caja[3] - caja[1]))
        fondo.paste(foto, (caja[0], caja[1]))
    else:
        print(f"Aviso: sin foto para '{oferta['producto']}' (imagen: '{nombre_foto}').")
        d.rectangle(caja, fill=(55, 55, 55))

    # Items del combo, uno por línea
    partes = [p.strip() for p in oferta["producto"].split("+") if p.strip()]
    lineas = [partes[0]] + ["+ " + p for p in partes[1:]]
    for i, linea in enumerate(lineas):
        y = 1280 + (i - (len(lineas) - 1) / 2) * 115
        d.text((W // 2, y), linea,
               font=fuente_que_entra(d, linea, 95, ancho_texto),
               fill=COLOR_TEXTO, anchor="mm")

    # Precio
    precio = formatear_precio(oferta["precio"])
    d.text((W // 2, 1610), precio,
           font=fuente_que_entra(d, precio, 210, ancho_texto),
           fill=COLOR_PRECIO, anchor="mm")

    # Pie
    d.text((W // 2, 1830), TEXTO_PIE, font=cargar_fuente(48),
           fill=(150, 150, 150), anchor="mm")

    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    fondo.save(ruta_salida, quality=95)
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