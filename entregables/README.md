# Entregables

Lo que hay que darle a cada persona, separado por destinatario. Cada carpeta es
un encargo cerrado: quien lo recibe no necesita leer nada más ni tocar código.

| Carpeta | Para quién | Qué le pides |
| :--- | :--- | :--- |
| [`1-abogado/`](1-abogado/) | Abogado con tarjeta profesional | Seis decisiones jurídicas y la determinación del RNBD |
| [`2-quimico-farmaceutico/`](2-quimico-farmaceutico/) | Químico farmacéutico | Validar la base de conocimiento clínico |
| [`3-tramites-del-prestador/`](3-tramites-del-prestador/) | Representante legal de la IPS | Cinco trámites ante el Ministerio, la DIAN y la SIC |
| [`pendientes/`](pendientes/) | Tú | La lista completa, para ir tachando |

---

## Cómo mandarlos

Cada documento está en Markdown, que es la fuente. GitHub lo renderiza y se
puede editar sin herramientas.

Para enviarlo a alguien que no vive en GitHub, genera los PDF:

```bash
cd entregables
python generar_pdf.py
```

Deja un `.pdf` junto a cada `.md`. Los PDF no se versionan: se regeneran en
segundos y versionar binarios que cambian con cada retoque ensucia el
historial.

---

## Un consejo sobre el orden

No los mandes los tres a la vez.

**El del prestador va primero**, y dentro de él, las credenciales del IHCE. Es
lo único que bloquea la operación, y el trámite es el más lento porque depende
del Ministerio. Todo lo demás puede avanzar en paralelo mientras esperas.

**El del abogado y el del químico farmacéutico** se pueden mandar el mismo día.
No dependen uno del otro.

Y antes de vincular esto a un semillero, un trabajo de grado o cualquier
programa de la universidad: mira lo que dice la lista de pendientes sobre
propiedad intelectual. Esa conversación es más fácil de tener antes que después.
