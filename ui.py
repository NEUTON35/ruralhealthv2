# -*- coding: utf-8 -*-
"""El vocabulario visual del sistema, en un solo sitio.

Por qué existe este archivo
---------------------------
Cuarenta y cuatro plantillas escribían sus propios botones. El resultado
previsible: el mismo "Guardar" en azul en una pantalla, negro en otra y verde
en una tercera, y ningún sitio donde arreglarlo de una vez. Cambiar el aspecto
de un botón significaba buscar y reemplazar por todo el repositorio y confiar
en no haberse dejado ninguno.

Ahora cada cadena de clases vive aquí, se registra como variable global de
Jinja en `app.register_jinja`, y las plantillas la usan por su nombre:

    <button class="{{ BOTON }}">Guardar</button>
    <a class="{{ BOTON_2 }}" href="...">Volver</a>

Cambiar el sistema visual entero es cambiar este archivo.

El criterio
-----------
Sale de un sistema de diseño hecho para esto: *sobriedad clínica*. Sin
sombras, la sombra simula profundidad, y en una pantalla barata a plena luz no
se ve, solo ensucia el borde. La jerarquía la dan el contorno, el tono de fondo
y el peso tipográfico, que sí sobreviven al sol.

Reglas que no se negocian:

- **El color significa algo.** Azul acción, rojo riesgo para el paciente, ámbar
  requiere revisión, verde completado. Nada decorativo lleva color. Si todo
  está resaltado, la alerta de alergia queda al mismo nivel que el formulario
  de agregar una farmacia.
- **Una acción principal por bloque.** Lo demás es secundario y lo parece.
- **La etiqueta de un campo siempre visible.** Un `placeholder` desaparece al
  escribir: quien vuelve a un formulario a medio llenar deja de saber qué era
  cada casilla. En un formulario de prescripción eso es peligroso.
- **Área táctil de 44px como mínimo.** Se usa de pie, en movimiento, a veces
  con guantes.
"""

# --- Botones ---------------------------------------------------------------
# La forma es común a los tres; cambia el relleno, que es lo que comunica el
# rango. `min-h-control` es el área táctil mínima.
_BOTON_BASE = ('inline-flex items-center justify-center gap-2 min-h-control '
               'px-4 py-2.5 rounded-lg font-semibold text-sm '
               'transition-colors focus:outline-none focus-visible:ring-2 '
               'focus-visible:ring-accent focus-visible:ring-offset-2')

CLASES = {
    # Acción principal del bloque. Una.
    'BOTON': _BOTON_BASE + ' bg-accent text-white hover:bg-accent-hover',
    # Todo lo demás: existe, se pulsa, no compite.
    'BOTON_2': (_BOTON_BASE + ' bg-white text-slate-700 border border-linea '
                'hover:bg-lienzo-seccion'),
    # Solo eliminar y anular. Lo que no tiene vuelta atrás.
    'BOTON_PELIGRO': (_BOTON_BASE + ' bg-white text-critical '
                      'border border-critical-border hover:bg-critical-subtle'),
    # Dentro de una fila de tabla o de una lista: mismo criterio, menos aire.
    'BOTON_MINI': ('inline-flex items-center justify-center gap-1.5 '
                   'min-h-[2.25rem] px-3 py-1.5 rounded-lg font-semibold '
                   'text-xs transition-colors bg-accent text-white '
                   'hover:bg-accent-hover'),
    'BOTON_MINI_2': ('inline-flex items-center justify-center gap-1.5 '
                     'min-h-[2.25rem] px-3 py-1.5 rounded-lg font-semibold '
                     'text-xs transition-colors bg-white text-slate-700 '
                     'border border-linea hover:bg-lienzo-seccion'),
    'BOTON_MINI_PELIGRO': ('inline-flex items-center justify-center gap-1.5 '
                           'min-h-[2.25rem] px-3 py-1.5 rounded-lg '
                           'font-semibold text-xs transition-colors bg-white '
                           'text-critical border border-critical-border '
                           'hover:bg-critical-subtle'),

    # --- Superficies ---
    # Contenedor de contenido. Contorno de 1px, sin sombra.
    'TARJETA': 'bg-white border border-linea rounded-lg',
    # El que manda en la pantalla, o el que está activo.
    'TARJETA_FUERTE': 'bg-white border border-linea-fuerte rounded-lg',
    # Banda de sección y cabecera de tabla.
    'BANDA': 'bg-lienzo-seccion',

    # --- Campos ---
    'CAMPO': ('w-full min-h-control px-3 py-2.5 rounded-lg border border-linea '
              'bg-white text-slate-900 placeholder:text-slate-400 '
              'focus:outline-none focus:border-accent focus:ring-1 '
              'focus:ring-accent disabled:bg-lienzo disabled:text-slate-500'),
    # La etiqueta va siempre encima del campo, siempre visible.
    'ROTULO': 'block text-xs font-semibold text-slate-600 mb-1.5',
    # La aclaración de debajo: qué formato, qué pasa si se deja vacío.
    'PISTA': 'mt-1 text-xs text-slate-500',

    # --- Tipografía de estructura ---
    'TITULO_PAGINA': 'text-2xl font-bold text-slate-900 tracking-tight',
    'TITULO_SECCION': 'text-base font-bold text-slate-900 tracking-tight',
    'SUBTITULO': 'text-sm text-slate-500',
    # Rótulo de dato: pequeño, en versalitas, gris.
    'ROTULO_DATO': ('block text-xs font-semibold text-slate-500 uppercase '
                    'tracking-wider'),
    'VALOR_DATO': 'block text-sm font-bold text-slate-900',

    # --- Tablas ---
    'TABLA': 'w-full text-sm border-collapse',
    'TABLA_CABECERA': ('text-left text-xs font-bold uppercase tracking-wider '
                       'text-slate-600 bg-lienzo-seccion'),
    'TABLA_TH': 'px-3 py-2.5 border-b border-linea',
    'TABLA_TD': 'px-3 py-2.5 border-b border-linea-suave align-middle',
}

# --- Chips de estado -------------------------------------------------------
# Rectángulo con contorno del color funcional y texto del mismo color sobre
# blanco. Nunca relleno: un chip relleno pesa como un botón y no lo es.
_CHIP_BASE = ('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md '
              'border text-xs font-semibold whitespace-nowrap')

CHIPS = {
    'neutro': _CHIP_BASE + ' border-linea text-slate-600 bg-white',
    'accion': _CHIP_BASE + ' border-accent-border text-accent bg-accent-subtle',
    'riesgo': _CHIP_BASE + ' border-critical-border text-critical bg-critical-subtle',
    'revisar': _CHIP_BASE + ' border-caution-border text-caution bg-caution-subtle',
    'completado': _CHIP_BASE + ' border-positive-border text-positive bg-positive-subtle',
}

# --- Avisos ----------------------------------------------------------------
# Contorno de 2px: es el único sitio donde el grosor comunica algo, y comunica
# gravedad. Un aviso de riesgo tiene que verse antes que el resto de la
# pantalla, de reojo, sin leerlo.
_AVISO_BASE = 'rounded-lg border-2 px-4 py-3'

AVISOS = {
    'riesgo': _AVISO_BASE + ' border-critical bg-critical-subtle text-critical-text',
    'revisar': _AVISO_BASE + ' border-caution-border bg-caution-subtle text-caution-text',
    'completado': _AVISO_BASE + ' border-positive-border bg-positive-subtle text-positive-text',
    'info': _AVISO_BASE + ' border-accent-border bg-accent-subtle text-slate-700',
    'neutro': _AVISO_BASE + ' border-linea bg-lienzo-seccion text-slate-700',
}


def registrar(jinja_env):
    """Deja el vocabulario disponible en todas las plantillas, sin importar."""
    jinja_env.globals.update(CLASES)
    jinja_env.globals['CHIP'] = CHIPS
    jinja_env.globals['AVISO'] = AVISOS


# Tailwind solo genera las clases que encuentra escritas. Las de este archivo
# no están en ninguna plantilla, así que hay que declararlas: `tailwind.config`
# escanea `ui.py` por esto mismo. Si se mueve el archivo, hay que actualizar
# el `content` de la configuración o el CSS saldrá sin estas clases y la
# aplicación aparecerá sin estilos.
