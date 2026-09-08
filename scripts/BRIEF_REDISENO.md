# Rediseño de la interfaz — instrucciones de trabajo

Sistema visual generado con Google Stitch para RuralHealth Connect y adaptado
al código existente. Este documento es la referencia para cualquiera —persona o
agente— que rediseñe una pantalla.

## El criterio: sobriedad clínica

Lo usan médicos, personal de farmacia y pacientes en puestos de salud
veredales, con conexión mala y pantallas baratas que se miran **a plena luz**.

- **Sin sombras. Ninguna.** Ya están anuladas en `tailwind.config.js`: una
  clase `shadow-*` no pinta nada. No las escribas. La jerarquía la dan el
  contorno, el tono de fondo y el peso tipográfico, que sí sobreviven al sol.
- **El color significa algo, y solo cuatro cosas:** azul acción, rojo riesgo
  para el paciente, ámbar requiere revisión, verde completado. Nada decorativo
  lleva color. Si todo está resaltado, la alerta de alergia queda al mismo
  nivel que el formulario de agregar una farmacia.
- **Una acción principal por bloque.** Lo demás es secundario y lo parece.
- **La etiqueta de un campo, siempre visible y encima.** Un `placeholder`
  desaparece al escribir; quien vuelve a un formulario a medio llenar deja de
  saber qué era cada casilla. En una prescripción eso es peligroso.
- **Densidad alta pero legible.** Es una herramienta de trabajo, no una
  landing. Una tabla densa suele ser mejor que una pila de tarjetas.
- **Ancho máximo 1280px** para el contenido de lectura.

## El vocabulario: úsalo, no escribas clases a mano

Las cadenas de clases están en `ui.py` y llegan a Jinja como variables
globales. **No hay que importar nada** para usarlas:

```html
<button class="{{ BOTON }}">Emitir orden</button>
<a class="{{ BOTON_2 }}" href="...">Volver</a>
<button class="{{ BOTON_PELIGRO }}">Anular</button>
<div class="{{ TARJETA }}">…</div>
<input class="{{ CAMPO }}" name="…">
<label class="{{ ROTULO }}" for="…">Cédula</label>
```

Disponibles: `BOTON`, `BOTON_2`, `BOTON_PELIGRO`, `BOTON_MINI`,
`BOTON_MINI_2`, `BOTON_MINI_PELIGRO`, `TARJETA`, `TARJETA_FUERTE`, `BANDA`,
`CAMPO`, `ROTULO`, `PISTA`, `TITULO_PAGINA`, `TITULO_SECCION`, `SUBTITULO`,
`ROTULO_DATO`, `VALOR_DATO`, `TABLA`, `TABLA_CABECERA`, `TABLA_TH`,
`TABLA_TD`, más los diccionarios `CHIP[...]` y `AVISO[...]`.

Colores de estructura: `border-linea` (contorno normal), `border-linea-fuerte`
(contenedor principal o activo), `border-linea-suave` (separador interno),
`bg-lienzo-seccion` (banda de sección y cabecera de tabla).

## Las piezas con estructura: `templates/_ui.html`

```jinja
{% from '_ui.html' import cabecera, seccion, panel, tira, dato, cifra,
                          campo, area, seleccion, chip, aviso, vacio, tabla %}

{% call cabecera('Historia clínica', 'María Elena Pérez Gómez') %}
  <a class="{{ BOTON }}" href="…">Agendar cita</a>
{% endcall %}

{% call tira(4) %}
  {{ dato('Edad', '41 años') }}
  {{ dato('Gestación', 'No registrada') }}
{% endcall %}

{% call aviso('riesgo', 'Alergias registradas', 'alert-triangle') %}
  Penicilina, mariscos.
{% endcall %}

{{ chip('Atendida', 'completado') }}
{{ campo('Cédula', 'cedula', valor=usuario.cedula, requerido=True) }}
```

Las piezas que envuelven contenido se invocan con `{% call %}`, no con `{{ }}`.

## Patrón de pantalla clínica

El arquetipo que salió del sistema de diseño, de arriba abajo:

1. Cabecera con el nombre y las acciones a la derecha; debajo, una fila de
   **chips compactos** con los datos de identificación (documento, teléfono).
2. **Lo que puede matar al paciente, primero.** La alerta de alergias va antes
   que nada más, a todo el ancho, con `aviso('riesgo', …)`.
3. **Tira de contexto**: una sola fila con contorno y celdas separadas por
   líneas verticales (edad, gestación, condiciones crónicas, tratamiento
   activo). Ocupa la cuarta parte que cuatro tarjetas y se lee de un vistazo.
4. Contenido principal en tabla densa; a la derecha, una columna estrecha con
   la línea de tiempo y los estados.

## Reglas que NO se pueden romper

Esto es una aplicación clínica real. Un cambio de estilo no puede alterar el
comportamiento:

1. **No toques ningún `name=` de un campo de formulario.** Ni lo quites, ni lo
   renombres, ni lo muevas fuera de su `<form>`.
2. **No toques `{{ csrf_token() }}`, `action=`, `method=`, ni ningún
   `url_for(...)`.**
3. **No cambies la lógica de Jinja.** Los `{% if %}` y `{% for %}` se
   conservan tal cual; si reorganizas el marcado, la condición viaja con su
   contenido.
4. **No toques ningún `id=` al que apunte JavaScript**, ni los atributos
   `data-*`, ni los `onclick`.
5. **No cambies `{% extends %}` ni el nombre de los `{% block %}`.**
6. **No añadas recursos externos.** Sin CDN, sin fuentes de Google, sin
   `<script src>` de fuera. Todo está vendorizado en `static/vendor/` a
   propósito: la aplicación tiene que funcionar sin conexión.
7. **Conserva los `aria-label`** y no bajes de 12px ningún texto.

## Comprobación obligatoria

Antes de dar por terminada una plantilla:

```bash
python scripts/contrato_plantillas.py comparar
```

Compara contra la foto de lo que cada plantilla prometía —campos, `url_for`,
tokens CSRF, bloques, ids, variables— y falla si se perdió algo. Nada de eso
da error por sí solo: el formulario se envía, el servidor recibe un campo
menos, y el dato del paciente se pierde en silencio.

**No ejecutes `npm run build:css`.** El CSS se regenera una sola vez al final;
si varios procesos lo escriben a la vez, el archivo queda a medias.
