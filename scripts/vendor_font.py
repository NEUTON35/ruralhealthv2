"""Descarga la tipografía Inter para servirla en local.

Google Fonts es un recurso externo más. Sin él la interfaz no se rompe -cae al
tipo de letra del sistema- pero en una aplicación para zonas de baja
conectividad conviene que nada dependa de una petición que puede no llegar.
"""

import os
import re
import urllib.request

os.chdir(r'C:\Users\juand\Downloads\ruralhealth')
DESTINO = 'static/vendor/fonts'
os.makedirs(DESTINO, exist_ok=True)

# Se pide con un User-Agent de navegador moderno para recibir woff2 y el
# subconjunto latino, que es el que necesita el español.
URL = ('https://fonts.googleapis.com/css2?'
       'family=Inter:wght@400;500;600;700&display=swap')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

peticion = urllib.request.Request(URL, headers={'User-Agent': UA})
css = urllib.request.urlopen(peticion, timeout=30).read().decode('utf-8')

# Solo interesan los bloques latin y latin-ext.
bloques = re.findall(r'/\*\s*([\w-]+)\s*\*/\s*(@font-face\s*\{[^}]+\})', css)
salida = []
descargados = 0

for subconjunto, bloque in bloques:
    if subconjunto not in ('latin', 'latin-ext'):
        continue
    url_fuente = re.search(r"url\((https://[^)]+\.woff2)\)", bloque)
    peso = re.search(r'font-weight:\s*(\d+)', bloque)
    if not url_fuente or not peso:
        continue

    nombre = f'inter-{peso.group(1)}-{subconjunto}.woff2'
    ruta = os.path.join(DESTINO, nombre)
    if not os.path.exists(ruta):
        datos = urllib.request.urlopen(
            urllib.request.Request(url_fuente.group(1), headers={'User-Agent': UA}),
            timeout=30).read()
        with open(ruta, 'wb') as f:
            f.write(datos)
        descargados += 1

    salida.append(
        bloque.replace(url_fuente.group(1), f'../vendor/fonts/{nombre}')
    )

with open('static/css/fonts.css', 'w', encoding='utf-8') as f:
    f.write('/* Inter, servida en local.\n'
            '   Generada por scripts/vendor_font.py. No editar a mano. */\n\n')
    f.write('\n\n'.join(salida) + '\n')

print(f'archivos woff2 descargados: {descargados}')
print(f'bloques @font-face escritos: {len(salida)}')
print(f'total en {DESTINO}: {len(os.listdir(DESTINO))} archivos')
