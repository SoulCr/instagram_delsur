"""
Genera una historia de Instagram por cada combo de ofertas del día y
(opcionalmente) las publica.

Uso:
    python main.py                       -> solo genera las imágenes (modo prueba)
    python main.py --fecha 2026-10-14    -> prueba con las ofertas de otra fecha
    python main.py --publicar            -> genera, sube a Cloudinary y publica
    python main.py --solo 1 --publicar   -> procesa solo la historia número 1 (para probar)
    python main.py --verificar           -> comprueba que el token de Instagram funciona
    python main.py --renovar-token       -> pide un token nuevo por 60 días más

ofertas.csv:  fecha,producto,precio,imagen
    - Cada fila es UNA historia. Todas las filas con la fecha del día se publican.
    - En "producto" separá los items con "+"  (ej: 2 kg pata muslo + 1 kg chori)
    - "imagen" es opcional: si la dejás vacía, las fotos se buscan solas en fotos/
      según el producto (pata muslo, pechuga, chori, picada, mila pollo, mila carne).
      Para indicarlas a mano usá: fotos/a.jpg + fotos/b.jpg
"""
import argparse
import csv
import os
import time
import unicodedata
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from PIL import (Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter,
                 ImageFont, ImageOps, ImageStat)

load_dotenv()

# Instagram API with Instagram Login (no hace falta página de Facebook)
IG_USER_ID = os.getenv("IG_USER_ID") or "me"  # "me" = la cuenta dueña del token
TOKEN = os.getenv("ACCESS_TOKEN")
IG_HOST = os.getenv("IG_HOST", "https://graph.instagram.com")
IG_VERSION = os.getenv("IG_API_VERSION", "v25.0")
BASE = f"{IG_HOST}/{IG_VERSION}"

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

CARPETA_FOTOS = Path("fotos")
EXTENSIONES = (".jpg", ".jpeg", ".png", ".jfif", ".webp")
COLOR_CIRCULO = (255, 249, 222)  # círculo suave detrás de cada producto (muy claro para no teñir la foto)

# Si la columna "imagen" del CSV está vacía, la foto se busca sola por palabras
# clave del producto: (palabras que deben aparecer, nombre del archivo sin extensión)
REGLAS_FOTOS = [
    (("mila", "pollo"), "milanesas_pollo"),
    (("mila", "carne"), "milanesas_carne"),
    (("patita",), "patitas_pollo"),
    (("pata",), "pata_muslo"),
    (("pechuga",), "pechuga"),
    (("chori",), "chorizos"),
    (("picada",), "carne_picada"),
]

ZONA_HORARIA = "America/Argentina/Buenos_Aires"  # para saber qué día es "hoy" en tu zona

CSV_OFERTAS = "ofertas.csv"
CARPETA_SALIDA = Path("salida")
W, H = 1080, 1920  # formato vertical 9:16


# ---------- Lectura de datos ----------
def hoy():
    """Fecha de hoy en Argentina (importa cuando corre en un servidor con otra hora)."""
    try:
        return datetime.now(ZoneInfo(ZONA_HORARIA)).date().isoformat()
    except Exception:
        return date.today().isoformat()



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


# ---------- Fotos de productos ----------
def sin_acentos(texto):
    t = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def foto_para(texto_producto):
    """Busca en fotos/ la foto que corresponde a un producto (ej: '2 kg pata muslo')."""
    t = sin_acentos(texto_producto)
    for palabras, nombre in REGLAS_FOTOS:
        if all(p in t for p in palabras):
            for ext in EXTENSIONES:
                ruta = CARPETA_FOTOS / f"{nombre}{ext}"
                if ruta.is_file():
                    return ruta
    return None


def fotos_de_oferta(oferta, partes):
    """Fotos del combo: las del CSV (separadas por +) o, si faltan, las automáticas."""
    explicitas = [x.strip() for x in (oferta.get("imagen") or "").split("+") if x.strip()]
    if explicitas:
        return [Path(x) for x in explicitas]
    return [foto_para(p) for p in partes]


def tipo_de_fondo(img):
    """'blanco' (fondo blanco), 'recorte' (fondo liso de color) o 'cubrir' (sin fondo liso)."""
    w, h = img.size
    t = max(2, min(w, h) // 100)
    tiras = [img.crop((0, 0, w, t)), img.crop((0, h - t, w, h)),
             img.crop((0, 0, t, h)), img.crop((w - t, 0, w, h))]
    stats = [ImageStat.Stat(x) for x in tiras]
    desv = max(max(st.stddev) for st in stats)
    media = tuple(int(sum(st.mean[i] for st in stats) / 4) for i in range(3))
    brillo = sum(media) / 3
    if brillo >= 230 and desv < 35:
        return "blanco", media
    if desv < 14:
        return "recorte", media
    return "cubrir", None


def retocar(img):
    """Retoque suave: fondo casi blanco a blanco puro, un poco de contraste, color y nitidez."""
    img = img.point(lambda v: min(255, int(v * 255 / 246)))
    img = ImageEnhance.Contrast(img).enhance(1.05)
    img = ImageEnhance.Color(img).enhance(1.08)
    return img.filter(ImageFilter.UnsharpMask(radius=1.6, percent=70, threshold=2))


def ajustar(img, caja_w, caja_h, relleno):
    escala = min(caja_w * relleno / img.width, caja_h * relleno / img.height)
    nuevo = (max(1, round(img.width * escala)), max(1, round(img.height * escala)))
    return img.resize(nuevo, Image.LANCZOS)


def recortar_fondo(img, color_fondo, tolerancia=26):
    """Quita un fondo liso conectado a los bordes y devuelve la imagen con transparencia."""
    dif = ImageChops.difference(img, Image.new("RGB", img.size, color_fondo))
    r, g, b = dif.split()
    maxd = ImageChops.lighter(ImageChops.lighter(r, g), b)
    parecido = maxd.point(lambda v: 255 if v < tolerancia else 0)
    w, h = img.size
    for sx, sy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
                   (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        if parecido.getpixel((sx, sy)) == 255:
            ImageDraw.floodfill(parecido, (sx, sy), 128)
    alfa = parecido.point(lambda v: 0 if v == 128 else 255)
    alfa = alfa.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    out = img.convert("RGBA")
    out.putalpha(alfa)
    return out


def circulo(imagen, centro, diametro, color, ss=3):
    m = Image.new("L", (diametro * ss, diametro * ss), 0)
    ImageDraw.Draw(m).ellipse((0, 0, diametro * ss - 1, diametro * ss - 1), fill=255)
    m = m.resize((diametro, diametro), Image.LANCZOS)
    imagen.paste(color, (centro[0] - diametro // 2, centro[1] - diametro // 2,
                         centro[0] - diametro // 2 + diametro,
                         centro[1] - diametro // 2 + diametro), m)


def armar_foto(rutas, tam):
    """Arma la imagen del recuadro: 1 producto grande o varios lado a lado con un '+'."""
    w, h = tam
    lienzo = Image.new("RGB", tam, BLANCO)
    n = len(rutas)
    ancho_slot = w / n
    for i, ruta in enumerate(rutas):
        x0 = round(i * ancho_slot)
        sw, sh = round((i + 1) * ancho_slot) - x0, h
        if ruta is None or not Path(ruta).is_file():
            print(f"Aviso: no se encontró la foto del producto {i + 1} ({ruta}).")
            continue
        img = ImageOps.exif_transpose(Image.open(ruta)).convert("RGB")
        modo, color = tipo_de_fondo(img)

        if modo == "cubrir":  # foto sin fondo liso: llena todo su espacio
            lienzo.paste(retocar(ImageOps.fit(img, (sw, sh), Image.LANCZOS)), (x0, 0))
            continue

        circulo(lienzo, (x0 + sw // 2, sh // 2), int(min(sw, sh) * 0.94), COLOR_CIRCULO)
        prod = ajustar(img, sw, sh, 0.90)
        px, py = x0 + (sw - prod.width) // 2, (sh - prod.height) // 2

        if modo == "blanco":  # el blanco del fondo desaparece al "multiplicar"
            region = lienzo.crop((px, py, px + prod.width, py + prod.height))
            lienzo.paste(ImageChops.multiply(region, retocar(prod)), (px, py))
        else:  # fondo de color liso: se recorta y se le agrega una sombra suave
            rgba = recortar_fondo(prod, color)
            alfa = rgba.getchannel("A")
            sombra_m = Image.new("L", tam, 0)
            sombra_m.paste(alfa.point(lambda v: int(v * 0.35)), (px, py + 14))
            sombra_m = sombra_m.filter(ImageFilter.GaussianBlur(14))
            lienzo.paste((60, 45, 20), (0, 0, w, h), sombra_m)
            rgb = retocar(rgba.convert("RGB"))
            rgb.putalpha(alfa)
            lienzo.paste(rgb, (px, py), rgb)

    d = ImageDraw.Draw(lienzo)
    for i in range(1, n):  # signo "+" entre productos
        cx = round(i * ancho_slot)
        circulo(lienzo, (cx, h // 2), 104, BLANCO)
        circulo(lienzo, (cx, h // 2), 88, OSCURO)
        d.text((cx, h // 2 - 4), "+", font=cargar_fuente(78), fill=AMARILLO, anchor="mm")
    return lienzo


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
    partes = [p.strip() for p in oferta["producto"].split("+") if p.strip()]
    caja_foto = (margen, 520, W - margen, 1110)
    sombra(base, caja_foto, 44, dy=16, blur=22, alpha=110)
    rect_redondeado(base, caja_foto, 44, BLANCO)
    interior = (caja_foto[0] + 14, caja_foto[1] + 14, caja_foto[2] - 14, caja_foto[3] - 14)
    tam = (interior[2] - interior[0], interior[3] - interior[1])
    foto = armar_foto(fotos_de_oferta(oferta, partes), tam).convert("RGBA")
    base.paste(foto, (interior[0], interior[1]), mascara_redondeada(tam, 34))

    # --- Tarjeta blanca con los items del combo ---
    caja_card = (margen, 1150, W - margen, 1500)
    sombra(base, caja_card, 44, dy=14, blur=20, alpha=100)
    rect_redondeado(base, caja_card, 44, BLANCO)
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


def _llamar(metodo, ruta, **kwargs):
    if not TOKEN:
        raise SystemExit("Falta ACCESS_TOKEN en el archivo .env")
    r = requests.request(metodo, f"{BASE}/{ruta}", timeout=60,
                         headers={"Authorization": f"Bearer {TOKEN}"}, **kwargs)
    if not r.ok:
        raise RuntimeError(f"Error de Instagram ({r.status_code}): {r.text}")
    return r.json()


def esperar_contenedor(id_contenedor, intentos=10, pausa=3):
    """Espera a que Instagram termine de procesar la imagen antes de publicarla."""
    for _ in range(intentos):
        estado = _llamar("GET", id_contenedor, params={"fields": "status_code"}).get("status_code")
        if estado == "FINISHED":
            return
        if estado in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram no pudo procesar la imagen (estado: {estado})")
        time.sleep(pausa)


def publicar_historia(image_url):
    contenedor = _llamar("POST", f"{IG_USER_ID}/media",
                         data={"image_url": image_url, "media_type": "STORIES"})
    esperar_contenedor(contenedor["id"])
    return _llamar("POST", f"{IG_USER_ID}/media_publish",
                   data={"creation_id": contenedor["id"]})


def verificar_token():
    datos = _llamar("GET", "me", params={"fields": "user_id,username"})
    print("Token válido. Cuenta:", datos.get("username"), "| ID:", datos.get("user_id"))


def renovar_token():
    """El token dura 60 días. Esto pide uno nuevo (el actual debe tener más de 24 h)."""
    r = requests.get(f"{IG_HOST}/refresh_access_token", timeout=60,
                     params={"grant_type": "ig_refresh_token", "access_token": TOKEN})
    if not r.ok:
        raise RuntimeError(f"No se pudo renovar el token ({r.status_code}): {r.text}")
    datos = r.json()
    print("Token nuevo (copialo a tu .env como ACCESS_TOKEN):")
    print(datos["access_token"])
    print(f"Vale por {datos.get('expires_in', 0) // 86400} días.")


# ---------- Programa principal ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha", default=hoy(),
                        help="fecha de las ofertas (AAAA-MM-DD), por defecto hoy")
    parser.add_argument("--publicar", action="store_true",
                        help="subir a Cloudinary y publicar en Instagram")
    parser.add_argument("--solo", type=int, metavar="N",
                        help="procesar solo la historia número N del día (útil para probar)")
    parser.add_argument("--verificar", action="store_true",
                        help="comprobar que el token de Instagram funciona")
    parser.add_argument("--renovar-token", action="store_true",
                        help="pedir un token nuevo por 60 días más")
    args = parser.parse_args()

    if args.verificar:
        return verificar_token()
    if args.renovar_token:
        return renovar_token()

    if not Path(FUENTE).exists():
        print(f"Aviso: no se encontró {FUENTE}, se usa la fuente por defecto.")

    ofertas = leer_ofertas(args.fecha)
    if not ofertas:
        print(f"No hay ofertas cargadas para {args.fecha}.")
        return

    if args.solo:
        if not 1 <= args.solo <= len(ofertas):
            raise SystemExit(f"--solo debe estar entre 1 y {len(ofertas)}.")
        ofertas = [ofertas[args.solo - 1]]

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