# -*- coding: utf-8 -*-
"""Extrae del sistema lo que cada especialista tiene que revisar.

Por que existe este script
--------------------------
Los entregables remitian a la aplicacion: «los textos estan en /privacidad»,
«puedo pasarle las tablas en Excel». Eso obliga a dar acceso al proyecto para
una revision que no lo necesita, y ademas convierte el documento en una
promesa en vez de en un entregable.

Aqui se saca el contenido y se pega dentro del propio documento. El abogado
recibe los cinco textos legales completos; el quimico farmaceutico, las nueve
tablas enteras. Ninguno de los dos ve una linea de codigo, ni la necesita.

Se genera en vez de copiarse a mano por una razon concreta: copiado a mano, el
anexo se queda viejo en cuanto alguien toque una tabla o corrija un parrafo, y
nadie se entera. Generado, basta con volver a ejecutarlo.

    python entregables/generar_anexos.py
    python entregables/generar_pdf.py

Se ejecuta antes que el generador de PDF.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(RAIZ), 'ruralhealth')
sys.path.insert(0, APP)

MARCA_INICIO = '<!-- ANEXO GENERADO: no editar a mano, sale de generar_anexos.py -->'
MARCA_FIN = '<!-- FIN DEL ANEXO GENERADO -->'


def reemplazar_anexo(ruta, contenido):
    """Sustituye el anexo del archivo, o lo anade al final si no lo tiene."""
    texto = io.open(ruta, encoding='utf-8').read()
    bloque = '%s\n\n%s\n\n%s\n' % (MARCA_INICIO, contenido.strip(), MARCA_FIN)

    if MARCA_INICIO in texto and MARCA_FIN in texto:
        antes = texto[:texto.index(MARCA_INICIO)]
        despues = texto[texto.index(MARCA_FIN) + len(MARCA_FIN):]
        texto = antes + bloque + despues
    else:
        texto = texto.rstrip() + '\n\n---\n\n' + bloque

    io.open(ruta, 'w', encoding='utf-8', newline='').write(texto)


# =============================================================================
# Anexo del abogado: los cinco textos legales, completos
# =============================================================================

def anexo_legal():
    from legal_documents import DOCUMENTS, PLACEHOLDER_LABELS, render_document

    ORDEN = ['privacy', 'privacy_notice', 'terms', 'telemedicine_consent',
             'transparency']
    claves = [k for k in ORDEN if k in DOCUMENTS]
    claves += [k for k in DOCUMENTS if k not in claves]

    partes = [
        '# Anexo · Los cinco textos legales, completos',
        '',
        'Esto es lo que hay que revisar. Va aquí dentro para que no haga falta '
        'entrar a ningún sistema.',
        '',
        'Donde dice «PENDIENTE», el dato lo tiene que aportar el prestador y '
        'todavía no está. No es un error del texto: el sistema marca esos '
        'huecos a propósito en lugar de dejar un corchete crudo, para que quien '
        'lea note que falta un dato.',
        '',
    ]

    for clave in claves:
        doc = render_document(clave, values=None)
        if not doc:
            continue
        partes.append('---')
        partes.append('')
        partes.append('## %s' % doc['title'])
        partes.append('')
        partes.append('**Versión %s** · vigente desde %s'
                      % (doc['version'], doc['effective_date']))
        partes.append('')
        if doc['summary']:
            partes.append('> %s' % doc['summary'].replace('\n', '\n> '))
            partes.append('')
        if doc['legal_basis']:
            partes.append('**Fundamento normativo:** %s'
                          % ' · '.join(doc['legal_basis']))
            partes.append('')
        if doc['pending']:
            etiquetas = [PLACEHOLDER_LABELS.get(p, (p, ''))[0]
                         for p in doc['pending']]
            partes.append('**Datos que faltan en este documento:** %s'
                          % ', '.join(etiquetas))
            partes.append('')

        for numero, (titulo, cuerpo) in enumerate(doc['sections'], 1):
            partes.append('### %d. %s' % (numero, titulo))
            partes.append('')
            partes.append(cuerpo)
            partes.append('')

    return '\n'.join(partes)


# =============================================================================
# Anexo del quimico farmaceutico: las nueve tablas, enteras
# =============================================================================

def _tabla(cabeceras, filas):
    salida = ['| ' + ' | '.join(cabeceras) + ' |',
              '| ' + ' | '.join([':---'] * len(cabeceras)) + ' |']
    for fila in filas:
        salida.append('| ' + ' | '.join(str(c) for c in fila) + ' |')
    return '\n'.join(salida)


def anexo_clinico():
    import clinical_safety as cs

    etiqueta = getattr(cs, 'class_label', lambda c: c)
    partes = [
        '# Anexo · Las nueve tablas, completas',
        '',
        'Esto es lo que hay que revisar. Va aquí dentro para no tener que '
        'entrar a ningún sistema ni leer código.',
        '',
        '**Versión de la base de conocimiento: %s**'
        % getattr(cs, 'KNOWLEDGE_BASE_VERSION', 'sin versión'),
        '',
    ]

    # --- 1. Clasificacion por familia ---
    porclase = {}
    for principio, clase in sorted(cs.DRUG_CLASSES.items()):
        porclase.setdefault(clase, []).append(principio)

    partes += ['---', '',
               '## 1. Principios activos por clase terapéutica (%d)'
               % len(cs.DRUG_CLASSES), '',
               'De esta clasificación dependen la detección de duplicidad y las '
               'interacciones por clase. Un medicamento en la clase equivocada '
               'genera alertas falsas o, peor, calla donde debería avisar.', '',
               _tabla(['Clase', 'Principios activos'],
                      [(etiqueta(c), ', '.join(sorted(p)))
                       for c, p in sorted(porclase.items())]), '']

    # --- 2. Interacciones ---
    def _desglosar(datos):
        """(gravedad, mecanismo, consecuencia) -> texto legible."""
        if isinstance(datos, (tuple, list)):
            gravedad = datos[0] if len(datos) > 0 else ''
            mecanismo = datos[1] if len(datos) > 1 else ''
            consecuencia = datos[2] if len(datos) > 2 else ''
        else:
            gravedad, mecanismo, consecuencia = '', str(datos), ''
        efecto = 'BLOQUEA' if str(gravedad).lower() == 'block' else 'Advierte'
        return efecto, str(mecanismo), str(consecuencia)

    filas = []
    for par, datos in sorted(cs.INTERACTIONS.items()):
        a, b = par if isinstance(par, tuple) else (par, '')
        efecto, mecanismo, consecuencia = _desglosar(datos)
        filas.append((a, b, efecto, mecanismo, consecuencia))

    partes += ['---', '',
               '## 2. Interacciones entre principios activos (%d)'
               % len(cs.INTERACTIONS), '',
               'Marcar como bloqueo algo que se prescribe junto a diario genera '
               'fatiga de alerta, y el médico empieza a justificar sin leer. '
               'Marcar como advertencia algo grave es el error contrario.', '',
               _tabla(['Principio A', 'Principio B', 'Efecto',
                       'Mecanismo', 'Consecuencia clínica'], filas), '']

    # --- 3. Interacciones entre clases ---
    filas = []
    for par, datos in sorted(cs.CLASS_INTERACTIONS.items()):
        a, b = par if isinstance(par, tuple) else (par, '')
        efecto, mecanismo, consecuencia = _desglosar(datos)
        filas.append((etiqueta(a), etiqueta(b), efecto, mecanismo, consecuencia))
    partes += ['---', '', '## 3. Interacciones entre clases completas (%d)'
               % len(cs.CLASS_INTERACTIONS), '',
               _tabla(['Clase A', 'Clase B', 'Efecto', 'Mecanismo',
                       'Consecuencia clínica'], filas), '']

    # --- 4 y 5. Embarazo ---
    for nombre, tabla, titulo in (
            ('contraindicados', cs.PREGNANCY_CONTRAINDICATED,
             '4. Contraindicados en embarazo'),
            ('precaucion', cs.PREGNANCY_CAUTION,
             '5. De precaución en embarazo')):
        filas = [(k, str(v)[:220]) for k, v in sorted(tabla.items())]
        partes += ['---', '', '## %s (%d)' % (titulo, len(tabla)), '',
                   _tabla(['Principio activo', 'Motivo'], filas), '']

    # --- 6. Reactividad cruzada ---
    filas = []
    for familia, cruces in sorted(cs.CROSS_REACTIVITY.items()):
        for cruce in (cruces if isinstance(cruces, (list, tuple)) else [cruces]):
            if isinstance(cruce, (tuple, list)):
                otra = cruce[0] if len(cruce) > 0 else ''
                gravedad = cruce[1] if len(cruce) > 1 else ''
                nota = cruce[2] if len(cruce) > 2 else ''
                efecto = ('BLOQUEA' if str(gravedad).lower() == 'block'
                          else 'Advierte')
                filas.append((etiqueta(familia), etiqueta(str(otra)), efecto,
                              str(nota)))
            else:
                filas.append((etiqueta(familia), str(cruce), '', ''))
    partes += ['---', '', '## 6. Reactividad cruzada entre familias (%d)'
               % len(cs.CROSS_REACTIVITY), '',
               'Es lo que hace que una alergia registrada a una familia bloquee '
               'también otra emparentada.', '',
               _tabla(['Familia', 'Reacciona también con', 'Efecto',
                       'Motivo'], filas), '']

    # --- 7. Control especial ---
    controlados = cs.CONTROLLED_SUBSTANCES
    if isinstance(controlados, dict):
        filas = [(k, str(v)[:180]) for k, v in sorted(controlados.items())]
        cabeceras = ['Principio activo', 'Nota']
    else:
        filas = [(x,) for x in sorted(controlados)]
        cabeceras = ['Principio activo']
    partes += ['---', '', '## 7. Medicamentos de control especial (%d)'
               % len(controlados), '',
               'Debería corresponder a lo que exige la Resolución 1478 de 2006 '
               'y sus modificaciones. Hoy superar el tope de estos produce '
               'advertencia, no bloqueo: es la pregunta 4 del documento.', '',
               _tabla(cabeceras, filas), '']

    # --- 8. Sinonimos ---
    filas = [(k, v) for k, v in sorted(cs.SYNONYMS.items())]
    partes += ['---', '', '## 8. Marcas y sinónimos (%d)' % len(cs.SYNONYMS), '',
               'Lo que permite reconocer un principio activo cuando se escribe '
               'con otro nombre. Si falta una marca de uso corriente, el sistema '
               'no la reconoce y no avisa de nada.', '',
               _tabla(['Se escribe', 'Se entiende como'], filas), '']

    # --- 9. Topes de cantidad ---
    partes += ['---', '', '## 9. Topes de cantidad', '',
               _tabla(['Tope', 'Unidades', 'Efecto'], [
                   ('General', getattr(cs, 'MAX_UNITS_DEFAULT', '?'),
                    'Advierte'),
                   ('Control especial', getattr(cs, 'MAX_UNITS_CONTROLLED', '?'),
                    'Advierte'),
                   ('Cifra absurda', getattr(cs, 'ABSURD_QUANTITY', '?'),
                    'BLOQUEA'),
               ]), '']

    return '\n'.join(partes)


def main():
    tareas = [
        ('1-abogado/revision-juridica.md', anexo_legal),
        ('2-quimico-farmaceutico/revision-seguridad-clinica.md', anexo_clinico),
    ]
    for relativo, generador in tareas:
        ruta = os.path.join(RAIZ, relativo)
        contenido = generador()
        reemplazar_anexo(ruta, contenido)
        print('  %-52s %d líneas de anexo'
              % (relativo, contenido.count('\n')))
    print('\nAnexos regenerados. Ahora: python entregables/generar_pdf.py')


if __name__ == '__main__':
    main()
