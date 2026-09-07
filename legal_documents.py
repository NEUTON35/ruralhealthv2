"""Textos legales de la plataforma, versionados.

Por qué viven aquí y no en la plantilla
---------------------------------------
El consentimiento que otorga un paciente solo es defensible si se puede
demostrar **qué texto** aceptó. Si el documento vive incrustado en el HTML y se
edita, no queda rastro de la versión anterior y toda constancia previa pierde
valor probatorio.

Aquí cada documento tiene versión, fecha de entrada en vigencia y un hash de su
contenido. `InformedConsentLog` guarda esos tres datos junto a la aceptación, de
modo que años después se puede reconstruir exactamente qué se aceptó.

**Al modificar cualquier texto hay que subir su `version` y su
`effective_date`.** Editar sin versionar rompe la trazabilidad de todos los
consentimientos que apuntaban a la versión anterior.

Alcance
-------
Estos textos cubren la estructura que exige la normativa colombiana y dejan
marcados con `[[...]]` los datos que solo el prestador puede completar: razón
social, NIT, domicilio, canal de atención. La aplicación avisa mientras queden
sin completar.

No sustituyen la revisión de un abogado. Son la base sobre la que esa revisión
trabaja, en lugar de las 931 palabras de folleto que había antes.
"""

import hashlib
from datetime import date

# Marcador de los datos que debe completar el prestador.
PLACEHOLDER_PATTERN = r'\[\[([^\]]+)\]\]'


class LegalDocument:
    """Un documento legal con su versión y su huella."""

    def __init__(self, key, title, version, effective_date, summary, sections,
                 legal_basis=()):
        self.key = key
        self.title = title
        self.version = version
        self.effective_date = effective_date
        self.summary = summary
        self.sections = sections
        self.legal_basis = legal_basis

    @property
    def content_hash(self):
        """Huella del contenido. Cambia si cambia una sola palabra."""
        material = '|'.join(
            [self.key, self.version, str(self.effective_date), self.title, self.summary]
            + [f'{titulo}::{cuerpo}' for titulo, cuerpo in self.sections]
        )
        return hashlib.sha256(material.encode('utf-8')).hexdigest()

    @property
    def pending_placeholders(self):
        """Datos que el prestador todavía no ha completado."""
        import re
        encontrados = []
        for _titulo, cuerpo in self.sections:
            encontrados.extend(re.findall(PLACEHOLDER_PATTERN, cuerpo))
        return sorted(set(encontrados))

    @property
    def is_complete(self):
        return not self.pending_placeholders


# =============================================================================
# Términos y condiciones
# =============================================================================

TERMS = LegalDocument(
    key='terms',
    title='Términos y Condiciones de Uso',
    version='3.0',
    effective_date=date(2026, 9, 6),
    summary=(
        'Condiciones que rigen el acceso y uso de la plataforma, alcance del '
        'servicio, obligaciones de las partes y régimen de responsabilidad.'
    ),
    legal_basis=(
        'Ley 1751 de 2015, estatutaria del derecho fundamental a la salud',
        'Resolución 2654 de 2019, telesalud y telemedicina',
        'Ley 1480 de 2011, Estatuto del Consumidor',
        'Ley 527 de 1999, comercio electrónico y mensajes de datos',
    ),
    sections=[
        ('Cláusula 1. Identificación del operador', """
La plataforma RuralHealth Connect es operada por [[RAZON_SOCIAL_OPERADOR]],
identificada con NIT [[NIT_OPERADOR]] y domicilio principal en
[[DOMICILIO_OPERADOR]], en adelante EL OPERADOR.

EL OPERADOR no presta servicios de salud. Los servicios asistenciales son
prestados por las instituciones prestadoras de servicios de salud y por los
profesionales inscritos en la plataforma, cada uno bajo su propia habilitación
en el Registro Especial de Prestadores de Servicios de Salud y bajo su propia
responsabilidad profesional.
"""),
        ('Cláusula 2. Definiciones', """
Para efectos del presente documento se entiende por:

1.  PLATAFORMA: el conjunto de aplicaciones y servicios informáticos operados por
    EL OPERADOR bajo la denominación RuralHealth Connect.
2.  USUARIO: toda persona natural que accede a la plataforma con credenciales
    propias, en cualquiera de los perfiles habilitados.
3.  PACIENTE: el usuario que recibe o solicita atención en salud.
4.  PRESTADOR: la institución o el profesional independiente que presta el
    servicio de salud a través de la plataforma.
5.  TELEMEDICINA: la prestación de servicios de salud a distancia en los
    componentes de promoción, prevención, diagnóstico, tratamiento y
    rehabilitación, conforme a la Resolución 2654 de 2019.
"""),
        ('Cláusula 3. Objeto y alcance del servicio', """
La plataforma facilita el agendamiento de citas, la comunicación asincrónica y
sincrónica entre paciente y profesional, el registro de la historia clínica, la
emisión de órdenes médicas, la coordinación de la entrega de medicamentos y la
generación de los reportes exigidos por la normativa vigente.

La plataforma no diagnostica, no prescribe ni sustituye el criterio del
profesional tratante. Toda decisión clínica corresponde de manera exclusiva al
profesional de la salud, quien la adopta bajo su propia responsabilidad.

Las verificaciones automáticas de alergias, interacciones medicamentosas y
contraindicaciones constituyen apoyo a la decisión clínica. La ausencia de
alertas no acredita la seguridad de una prescripción, sino únicamente que no se
identificaron los patrones contenidos en la base de conocimiento del sistema.
"""),
        ('Cláusula 4. Exclusión de urgencias', """
La plataforma no está destinada a la atención de urgencias ni de emergencias
vitales. No opera en forma continua ni garantiza tiempo de respuesta.

Ante dolor torácico, dificultad respiratoria, pérdida de conciencia, hemorragia
que no cede, signos de enfermedad cerebrovascular, intoxicación o cualquier
situación que comprometa la vida, el usuario debe acudir de inmediato al
servicio de urgencias más cercano o comunicarse con la línea 123.

El usuario reconoce que el uso de la plataforma en una situación de urgencia es
contrario a su destinación y asume las consecuencias que de ello se deriven.
"""),
        ('Cláusula 5. Atención por telemedicina', """
La atención por telemedicina se presta conforme a la Resolución 2654 de 2019 y
requiere el otorgamiento previo del consentimiento informado específico previsto
para esa modalidad, distinto del consentimiento para el tratamiento de datos
personales.

El profesional tratante puede determinar en cualquier momento que el caso
requiere valoración presencial y proceder a la remisión correspondiente. La
disponibilidad de agenda en modalidad remota no implica que dicha modalidad sea
idónea para todo motivo de consulta.

El usuario debe disponer de conectividad suficiente, de un espacio que permita
preservar la confidencialidad de la consulta y de su documento de identidad.
"""),
        ('Cláusula 6. Capacidad y representación', """
Podrán registrarse como usuarios las personas naturales mayores de edad con
capacidad legal para obligarse.

Las cuentas de menores de edad serán gestionadas por quien ejerza la patria
potestad o la representación legal, condición que deberá acreditarse ante el
prestador. El representante responde por el uso de la cuenta. Se garantizará el
derecho del menor a ser escuchado, conforme a su edad y grado de madurez, en los
términos del artículo 26 de la Ley 1098 de 2006.

Los profesionales de la salud deberán acreditar registro médico profesional
vigente. La plataforma no permite la emisión de órdenes médicas a cuentas que
carezcan de dicho registro.
"""),
        ('Cláusula 7. Obligaciones del usuario', """
El usuario se obliga a:

1.  Suministrar información veraz, completa y actualizada, en especial la
    relativa a antecedentes y alergias. La omisión o inexactitud de un dato
    clínico puede derivar en daño a su propia salud.
2.  Custodiar sus credenciales de acceso y abstenerse de compartirlas o de
    utilizar las de terceros.
3.  Abstenerse de suplantar la identidad de otra persona y de cargar archivos
    que puedan comprometer la seguridad del sistema.
4.  Abstenerse de emplear la plataforma para obtener medicamentos por fuera de
    una indicación médica legítima.

El incumplimiento de estas obligaciones faculta al operador para suspender la
cuenta, sin perjuicio de las acciones legales que correspondan.
"""),
        ('Cláusula 8. Órdenes médicas y medicamentos', """
Las órdenes médicas emitidas a través de la plataforma incorporan la firma del
profesional, su número de registro médico y un sello criptográfico que permite
verificar su integridad.

Toda orden tiene fecha de vencimiento. Vencida o anulada, no puede ser objeto de
dispensación.

Los medicamentos sometidos a control especial se rigen por la Resolución 1478 de
2006 y requieren receta oficial numerada, documento que la plataforma no emite.

La disponibilidad de medicamentos depende del inventario físico de cada
establecimiento farmacéutico. La información de existencias que muestra el
sistema es indicativa y no constituye garantía de entrega.
"""),
        ('Cláusula 9. Disponibilidad del servicio', """
El operador desarrolla la plataforma para su funcionamiento en condiciones de
conectividad limitada, sin que ello constituya garantía de disponibilidad
continua.

El servicio puede interrumpirse por labores de mantenimiento, por fallas de
conectividad, por causas atribuibles a los proveedores de infraestructura o por
circunstancias constitutivas de fuerza mayor o caso fortuito.

Ninguna funcionalidad de la plataforma sustituye la atención presencial cuando
esta resulte necesaria.
"""),
        ('Cláusula 10. Geolocalización y desplazamientos', """
Las distancias y rutas que muestra la plataforma son estimaciones calculadas a
partir de coordenadas geográficas. No consideran el estado de la vía, las
condiciones climáticas, la disponibilidad de transporte ni las condiciones de
seguridad de la zona.

El usuario debe verificar las condiciones reales antes de emprender cualquier
desplazamiento. El operador no responde por los daños derivados del traslado.

Los datos de ubicación se tratan únicamente previa autorización expresa del
usuario, revocable en cualquier momento desde la configuración del navegador o
desde el módulo de ajustes de la plataforma.
"""),
        ('Cláusula 11. Condiciones económicas', """
Los servicios que causen costo se informarán con anterioridad a su contratación,
con indicación del valor total en pesos colombianos.

Los servicios de salud humana se encuentran excluidos del impuesto sobre las
ventas, conforme al numeral 1 del artículo 476 del Estatuto Tributario.

La facturación corresponde a la institución prestadora o al profesional
independiente, según quien preste el servicio, con su propia identificación
tributaria. El operador no es parte de dicha relación económica, salvo mención
expresa en contrario.

El derecho de retracto y la reversión del pago operan en los términos de los
artículos 47 y 51 de la Ley 1480 de 2011, cuando la contratación se haya
efectuado por medios electrónicos y la naturaleza del servicio lo permita. Los
servicios ya prestados no son susceptibles de retracto.
"""),
        ('Cláusula 12. Propiedad intelectual y titularidad de la información', """
El software, los signos distintivos y los contenidos de la plataforma son de
titularidad del operador o de sus licenciantes.

La información clínica es de titularidad del paciente. El prestador la custodia
por mandato legal, en los términos de la Resolución 1995 de 1999 y de la
Resolución 839 de 2017. El titular puede obtener copia íntegra de sus datos en
cualquier momento desde el módulo dispuesto para tal efecto.
"""),
        ('Cláusula 13. Régimen de responsabilidad', """
El operador responde por el correcto funcionamiento de las herramientas que
provee y por la custodia de la información conforme a la normativa aplicable.

El operador no responde por las decisiones clínicas adoptadas por los
profesionales tratantes, por la inexactitud de la información suministrada por
el usuario, por los daños derivados del uso de la plataforma en situaciones de
urgencia, ni por las interrupciones atribuibles a terceros.

Ninguna disposición del presente documento limita la responsabilidad derivada de
dolo o culpa grave, ni afecta los derechos irrenunciables que la ley reconoce al
consumidor y al paciente.
"""),
        ('Cláusula 14. Terminación', """
El usuario puede solicitar el cierre de su cuenta en cualquier momento desde el
módulo de ajustes. El cierre desactiva el acceso y cesa los tratamientos de
datos que no obedezcan a una obligación legal.

La historia clínica no se suprime. El prestador está obligado a conservarla por
un término mínimo de quince años, conforme al artículo 2 de la Resolución 839 de
2017. Este deber legal prevalece sobre la solicitud de supresión, en los
términos del artículo 9 de la Ley 1581 de 2012.

Se recomienda al usuario obtener copia de su información con anterioridad al
cierre de la cuenta.
"""),
        ('Cláusula 15. Modificaciones', """
El operador puede modificar los presentes términos. Cada versión se identifica
con número y fecha de entrada en vigencia, y las versiones anteriores permanecen
archivadas.

Las modificaciones sustanciales se comunicarán dentro de la plataforma con una
antelación no inferior a quince días calendario. El usuario que no acepte las
nuevas condiciones puede solicitar el cierre de su cuenta.
"""),
        ('Cláusula 16. Ley aplicable, reclamaciones y jurisdicción', """
Los presentes términos se rigen por la ley colombiana.

Las peticiones, quejas, reclamos y sugerencias se reciben en [[CANAL_PQRS]] y se
atenderán dentro de los quince días hábiles siguientes a su radicación.

Agotado el trámite ante el operador, el usuario puede acudir a la
Superintendencia de Industria y Comercio cuando la reclamación verse sobre el
tratamiento de datos personales, en los términos del artículo 16 de la Ley 1581
de 2012, o a la Superintendencia Nacional de Salud cuando verse sobre la
prestación del servicio de salud.

Las controversias que no puedan resolverse de común acuerdo se someterán a los
jueces de la República de Colombia.
"""),
    ],
)


# =============================================================================
# Política de tratamiento de datos personales
# =============================================================================
#
# Estructura conforme al artículo 13 del Decreto 1377 de 2013, que enumera el
# contenido mínimo obligatorio de la política.

PRIVACY = LegalDocument(
    key='privacy',
    title='Política de Tratamiento de Datos Personales',
    version='3.0',
    effective_date=date(2026, 9, 6),
    summary=(
        'Finalidades del tratamiento, derechos del titular, procedimiento para '
        'ejercerlos, plazos de conservación y medidas de seguridad adoptadas.'
    ),
    legal_basis=(
        'Ley 1581 de 2012, protección de datos personales',
        'Decreto 1377 de 2013, reglamentario de la Ley 1581',
        'Ley 1266 de 2008, habeas data',
        'Resolución 1995 de 1999, historia clínica',
        'Resolución 839 de 2017, manejo y conservación de la historia clínica',
    ),
    sections=[
        ('Artículo 1. Responsable del tratamiento', """
Razón social: [[RAZON_SOCIAL_OPERADOR]]
NIT: [[NIT_OPERADOR]]
Domicilio: [[DOMICILIO_OPERADOR]]
Correo electrónico para protección de datos: [[CORREO_PROTECCION_DATOS]]
Teléfono: [[TELEFONO_CONTACTO]]
Área responsable de la atención: [[AREA_RESPONSABLE]]

Cada institución prestadora inscrita en la plataforma es responsable del
tratamiento de la historia clínica de sus propios pacientes. Respecto de dichos
datos, el operador actúa en calidad de encargado del tratamiento, en los términos
del contrato suscrito con cada institución.
"""),
        ('Artículo 2. Datos objeto de tratamiento', """
1.  Datos de identificación: nombres y apellidos, tipo y número de documento,
    fecha de nacimiento, sexo, teléfono, correo electrónico, dirección,
    municipio y departamento de residencia.
2.  Datos de salud, que tienen naturaleza sensible: motivo de consulta,
    antecedentes, alergias, diagnósticos codificados según la Clasificación
    Internacional de Enfermedades, procedimientos codificados según la
    Clasificación Única de Procedimientos en Salud, medicamentos prescritos y
    dispensados, notas de evolución, contenido de las consultas por mensajería,
    documentos adjuntos y estado de gestación cuando se registre.
3.  Datos de aseguramiento: entidad responsable de pago, régimen de afiliación
    y plan de beneficios.
4.  Datos de localización: coordenadas geográficas, cuyo tratamiento requiere
    autorización expresa y revocable del titular.
5.  Datos técnicos de acceso: dirección IP, agente de usuario, fecha y hora de
    conexión y registro de las operaciones efectuadas. Su tratamiento es
    necesario para acreditar quién accedió a cada historia clínica.
6.  Datos de pago: comprobante aportado por el titular y valor de la
    transacción. No se almacenan números de tarjeta ni claves bancarias.
"""),
        ('Artículo 3. Autorización para el tratamiento de datos sensibles', """
Los datos de salud tienen naturaleza sensible conforme al artículo 5 de la Ley
1581 de 2012. Su tratamiento requiere autorización previa, expresa e informada
del titular, que se solicita de manera separada de las demás autorizaciones.

El titular no está obligado a autorizar el tratamiento de datos sensibles. La
negativa impide la prestación de atención clínica a través de la plataforma, por
cuanto dicha prestación no es posible sin tratar esa categoría de datos. El
titular conserva el acceso a su cuenta y a la consulta de su información.

La autorización es revocable en cualquier momento desde el módulo dispuesto para
tal efecto. La revocatoria suspende las funcionalidades clínicas. La información
previamente registrada se conserva por el término señalado en el artículo 7 del
presente documento.
"""),
        ('Artículo 4. Finalidades del tratamiento', """
1.  Prestar atención en salud y efectuar su seguimiento.
2.  Conformar y custodiar la historia clínica conforme a la Resolución 1995 de
    1999.
3.  Emitir órdenes médicas y coordinar la dispensación de medicamentos.
4.  Verificar alergias, interacciones y contraindicaciones con anterioridad a la
    prescripción.
5.  Programar citas y remitir recordatorios.
6.  Cumplir las obligaciones de reporte al Sistema General de Seguridad Social
    en Salud, en particular el Registro Individual de Prestación de Servicios de
    Salud.
7.  Facturar los servicios prestados y llevar la contabilidad correspondiente.
8.  Atender peticiones, quejas, reclamos y solicitudes de habeas data.
9.  Conservar el registro de auditoría exigido por la normativa.
10. Elaborar estadísticas de gestión previa anonimización irreversible de los
    datos.

Los datos de salud no se tratan con fines publicitarios, no son objeto de venta
ni se ceden a aseguradoras o empleadores para la adopción de decisiones respecto
del titular.
"""),
        ('Artículo 5. Destinatarios de la información', """
1.  Los profesionales de la salud que prestan la atención, limitados a los
    vinculados a la institución donde el titular se atiende.
2.  El establecimiento farmacéutico de la institución, respecto de los
    medicamentos contenidos en la orden.
3.  Las autoridades del sector salud, en cumplimiento de las obligaciones de
    reporte.
4.  La entidad responsable de pago, cuando resulte necesario para el
    reconocimiento y pago de los servicios.
5.  Las autoridades judiciales y administrativas, en virtud de orden que así lo
    disponga.
6.  Los proveedores de infraestructura tecnológica, en calidad de encargados del
    tratamiento y bajo contrato que les impone el deber de confidencialidad y
    les prohíbe cualquier uso propio de la información.
"""),
        ('Artículo 6. Transferencia internacional de datos', """
Parte de la infraestructura que aloja las bases de datos se encuentra ubicada en
[[PAIS_ALOJAMIENTO]], operada por [[PROVEEDOR_ALOJAMIENTO]].

Esta circunstancia configura una transferencia internacional de datos en los
términos del artículo 26 de la Ley 1581 de 2012, disposición que la restringe a
países que ofrezcan niveles adecuados de protección, salvo que medie
autorización expresa e inequívoca del titular, se suscriban cláusulas
contractuales que garanticen la protección o se obtenga declaración de
conformidad de la Superintendencia de Industria y Comercio.

Fundamento aplicado: [[FUNDAMENTO_TRANSFERENCIA]].

La información se transmite mediante canal cifrado y permanece cifrada en
reposo, con llaves que no están en poder del proveedor de infraestructura.

El titular que no autorice esta transferencia puede manifestarlo por el canal
señalado en el artículo 10.
"""),
        ('Artículo 7. Término de conservación', """
| Información | Término | Fundamento |
| :--- | :--- | :--- |
| Historia clínica | Quince años desde la última atención | Resolución 839 de 2017 |
| Órdenes médicas | Quince años, por integrar la historia clínica | Resolución 839 de 2017 |
| Registro de dispensación | Cinco años | Resolución 1403 de 2007 |
| Consentimientos informados | Quince años | Resolución 839 de 2017 |
| Registro de auditoría | Cinco años | Deber de demostrar el cumplimiento |
| Documentos de facturación | Diez años | Artículo 28 del Código de Comercio |
| Datos de cuenta sin actividad | Hasta la solicitud de cierre | Ley 1581 de 2012 |

Vencido el término aplicable, la información se suprime o se anonimiza de forma
irreversible.
"""),
        ('Artículo 8. Derechos del titular', """
Conforme al artículo 8 de la Ley 1581 de 2012, el titular tiene derecho a:

1.  Conocer, actualizar y rectificar sus datos personales.
2.  Solicitar prueba de la autorización otorgada.
3.  Ser informado sobre el uso dado a sus datos personales.
4.  Presentar quejas ante la Superintendencia de Industria y Comercio por
    infracción a la ley.
5.  Revocar la autorización y solicitar la supresión del dato, con la limitación
    prevista en el artículo 7 del presente documento.
6.  Acceder en forma gratuita a sus datos personales.

Estos derechos se ejercen a través del módulo dispuesto en la plataforma o por
el canal señalado en el artículo siguiente.
"""),
        ('Artículo 9. Medidas de seguridad', """
1.  Cifrado de la historia clínica y de los datos personales en reposo, con
    llaves custodiadas por fuera de la base de datos.
2.  Cifrado en tránsito mediante protocolo seguro con política estricta de
    transporte.
3.  Índice ciego para la búsqueda por número de documento, que evita el
    almacenamiento del dato en forma directamente consultable.
4.  Control de acceso basado en roles y aislamiento entre instituciones.
5.  Registro de auditoría encadenado criptográficamente, que permite detectar la
    alteración o supresión posterior de sus entradas.
6.  Bloqueo temporal de la cuenta ante intentos reiterados de acceso.
7.  Exclusión de las respuestas con contenido clínico del almacenamiento local
    del dispositivo.

Ningún sistema de información es invulnerable. Ante un incidente que comprometa
los datos del titular, el operador se lo informará y reportará el hecho a la
Superintendencia de Industria y Comercio, conforme al literal n del artículo 17
de la Ley 1581 de 2012.
"""),
        ('Artículo 10. Procedimiento para el ejercicio de los derechos', """
El titular puede ejercer sus derechos por cualquiera de las siguientes vías:

1.  A través del módulo de datos personales disponible en la plataforma, que
    permite obtener copia íntegra de la información, solicitar su rectificación
    o supresión y revocar autorizaciones.
2.  Mediante comunicación dirigida a [[CORREO_PROTECCION_DATOS]] o a
    [[DOMICILIO_OPERADOR]], con indicación de su nombre, número de documento,
    derecho que ejerce y canal para la respuesta.

Términos de respuesta, conforme al Decreto 1377 de 2013:

1.  Consultas: diez días hábiles, prorrogables por cinco días hábiles más,
    informando previamente al interesado.
2.  Reclamos: quince días hábiles, prorrogables por ocho días hábiles más, en
    las mismas condiciones.

Vencidos los términos sin respuesta satisfactoria, el titular puede acudir a la
Superintendencia de Industria y Comercio.
"""),
        ('Artículo 11. Tratamiento de datos de menores de edad', """
El tratamiento de datos personales de menores de edad tiene carácter excepcional
y solo procede cuando responda al interés superior del menor y se respete su
derecho a ser escuchado, conforme al artículo 7 de la Ley 1581 de 2012 y al
artículo 12 del Decreto 1377 de 2013.

La autorización es otorgada por quien ejerza la patria potestad o la
representación legal, previa acreditación de dicha calidad.
"""),
        ('Artículo 12. Cookies y almacenamiento local', """
La plataforma emplea únicamente los mecanismos necesarios para su
funcionamiento:

1.  Cookie de sesión, que mantiene la identificación del usuario durante su
    permanencia y se elimina al cerrar la sesión.
2.  Testigo de seguridad contra falsificación de peticiones entre sitios.
3.  Almacenamiento local para conservar los mensajes redactados sin conexión y
    remitirlos al restablecerse el servicio. Se elimina al cerrar la sesión.

La plataforma no emplea cookies publicitarias, herramientas de analítica de
terceros ni tecnologías de rastreo.
"""),
        ('Artículo 13. Registro Nacional de Bases de Datos', """
Las bases de datos administradas por el operador se inscriben en el Registro
Nacional de Bases de Datos de la Superintendencia de Industria y Comercio,
conforme a la Ley 1581 de 2012 y sus decretos reglamentarios.

Estado del registro: [[ESTADO_RNBD]].
"""),
        ('Artículo 14. Vigencia', """
La presente política rige a partir de su fecha de entrada en vigencia y
permanecerá vigente mientras el operador ejerza su actividad.

Las bases de datos se conservarán por los términos señalados en el artículo 7.

Las modificaciones sustanciales se comunicarán dentro de la plataforma con una
antelación no inferior a quince días calendario. Cada versión permanece
archivada con su respectiva fecha.
"""),
    ],
)


# =============================================================================
# Consentimiento informado de telemedicina
# =============================================================================

TELEMEDICINE_CONSENT = LegalDocument(
    key='telemedicine',
    title='Consentimiento Informado para Atención por Telemedicina',
    version='2.0',
    effective_date=date(2026, 9, 6),
    summary=(
        'Información sobre la modalidad de atención a distancia, sus beneficios, '
        'sus limitaciones y las alternativas disponibles, previa a su aceptación.'
    ),
    legal_basis=(
        'Resolución 2654 de 2019, telesalud y telemedicina',
        'Ley 1419 de 2010, lineamientos de telesalud',
        'Ley 1751 de 2015, derecho a la información en salud',
        'Resolución 1995 de 1999, historia clínica',
    ),
    sections=[
        ('1. Naturaleza de la atención', """
La telemedicina es la prestación de servicios de salud a distancia con apoyo de
tecnologías de la información y las comunicaciones.

El profesional que lo atiende cuenta con la misma habilitación y el mismo
registro médico exigidos para la consulta presencial. La atención se incorpora a
su historia clínica en idénticos términos.
"""),
        ('2. Beneficios de la modalidad', """
1.  Evita desplazamientos prolongados, costosos o riesgosos.
2.  Permite el acceso a profesionales que no ejercen en su municipio.
3.  Reduce los tiempos de espera para la atención.
4.  Facilita el seguimiento de tratamientos sin necesidad de traslado.
"""),
        ('3. Limitaciones y riesgos', """
En la atención a distancia el profesional no puede realizar examen físico. No
puede palpar, auscultar, tomar signos vitales ni valorar directamente una lesión.
De ello se derivan las siguientes limitaciones:

1.  Existen diagnósticos que no pueden establecerse por esta modalidad.
2.  El profesional puede determinar que su caso requiere valoración presencial y
    proceder a la remisión correspondiente.
3.  La calidad de la atención depende de la conectividad. Una falla en el audio
    o en el video puede interrumpir o suspender la consulta.
4.  La información incompleta o inexacta que usted suministre puede conducir a
    una orientación equivocada.

La telemedicina no sustituye la atención presencial cuando esta resulte
necesaria. La aceptación del presente documento no lo obliga a atenderse
exclusivamente por esta modalidad.
"""),
        ('4. Exclusión de urgencias', """
Esta modalidad no está destinada a la atención de urgencias. No opera en forma
continua ni garantiza tiempo de respuesta.

Ante una situación que comprometa la vida, acuda de inmediato al servicio de
urgencias más cercano o comuníquese con la línea 123.
"""),
        ('5. Tratamiento de la información', """
La consulta se incorpora a su historia clínica, incluidos los mensajes
intercambiados, los documentos que usted aporte y las notas del profesional.

Las videollamadas no son objeto de grabación, salvo autorización expresa y
separada otorgada por usted para cada ocasión.

La información se cifra en tránsito y en reposo, y su acceso se restringe al
equipo asistencial de la institución donde usted se atiende.
"""),
        ('6. Derechos del paciente', """
1.  Rechazar la atención por telemedicina y solicitar atención presencial, sin
    que ello afecte su acceso al servicio.
2.  Interrumpir la consulta en cualquier momento.
3.  Revocar el presente consentimiento en cualquier momento, desde el módulo de
    datos personales de la plataforma.
4.  Formular las preguntas que estime necesarias con anterioridad a su
    aceptación.
5.  Obtener copia del presente documento y de su historia clínica.
"""),
        ('7. Declaración de aceptación', """
Con la aceptación del presente documento usted declara que:

1.  Leyó y comprendió su contenido.
2.  Tuvo oportunidad de formular preguntas y estas le fueron resueltas.
3.  Comprende las limitaciones de la atención a distancia, en particular la
    imposibilidad de realizar examen físico.
4.  Conoce su derecho a solicitar atención presencial en cualquier momento.
5.  Acepta de manera libre y voluntaria recibir atención por esta modalidad.

De la aceptación quedará constancia con indicación de la fecha, la hora, el
dispositivo empleado y la versión del presente documento.
"""),
    ],
)


# =============================================================================
# Transparencia y derechos del paciente
# =============================================================================

TRANSPARENCY = LegalDocument(
    key='transparency',
    title='Transparencia y Derechos del Paciente',
    version='3.0',
    effective_date=date(2026, 9, 6),
    summary=(
        'Derechos reconocidos al paciente, criterios de funcionamiento del '
        'sistema, alcance de sus automatismos y limitaciones conocidas.'
    ),
    legal_basis=(
        'Ley 1751 de 2015, estatutaria del derecho fundamental a la salud',
        'Ley 1712 de 2014, transparencia y acceso a la información pública',
        'Resolución 1995 de 1999, historia clínica',
    ),
    sections=[
        ('1. Derechos del paciente', """
La Ley 1751 de 2015 reconoce la salud como derecho fundamental. En desarrollo de
dicha ley, el paciente tiene derecho a:

1.  Recibir atención de urgencia sin requisito previo de pago ni de
    autorización.
2.  Elegir libremente su prestador dentro de la red disponible.
3.  Recibir información clara, apropiada y suficiente sobre su estado de salud,
    las alternativas de tratamiento y sus riesgos.
4.  Aceptar o rechazar cualquier procedimiento, previa información suficiente.
5.  Que su información conserve el carácter confidencial.
6.  Acceder a su historia clínica y obtener copia de la misma.
7.  Recibir un trato digno, sin discriminación de ninguna naturaleza.
8.  Presentar reclamaciones y obtener respuesta.
9.  Solicitar una segunda opinión médica.
"""),
        ('2. Alcance de los automatismos del sistema', """
Se informa al paciente qué operaciones ejecuta el sistema de manera automática y
cuáles corresponden a decisión humana.

Operaciones automáticas, sin criterio clínico:

1.  Determinación de los horarios disponibles en agenda.
2.  Ordenamiento de establecimientos farmacéuticos por distancia.
3.  Verificación de existencias de medicamentos.
4.  Control de vigencia de las órdenes médicas.

Operaciones de apoyo a la decisión, con decisión humana:

5.  Alertas de alergia, interacción medicamentosa y contraindicación. El sistema
    las presenta; la decisión de prescribir corresponde al profesional, quien,
    de resolver continuar pese a la alerta, debe consignar por escrito su
    justificación clínica, la cual queda registrada.

Decisiones exclusivamente humanas:

6.  Diagnóstico, tratamiento, remisión y dispensación.

Ningún algoritmo de la plataforma diagnostica, prescribe ni niega la atención.
"""),
        ('3. Funcionamiento de las verificaciones de seguridad', """
Con anterioridad a la firma de una orden médica, el sistema la contrasta con las
alergias registradas del paciente, con su tratamiento activo y con su estado de
gestación, e identifica interacciones conocidas entre los medicamentos
prescritos.

La verificación opera sobre la información registrada. Una alergia que no conste
en la historia clínica no puede ser advertida por el sistema. Por esta razón
resulta necesario que el paciente informe todas sus alergias, incluidas aquellas
que considere de menor entidad.

La ausencia de alertas no acredita la seguridad de la prescripción, sino
únicamente que no se identificaron los patrones contenidos en la base de
conocimiento del sistema.
"""),
        ('4. Registro de accesos', """
Cada consulta a la historia clínica queda registrada con indicación de quien la
efectuó, la fecha y el origen de la conexión.

El registro se encuentra encadenado criptográficamente, de manera que la
alteración o supresión posterior de una entrada resulta detectable.

El paciente puede solicitar el detalle de los accesos a su información.
"""),
        ('5. Reportes obligatorios', """
La normativa vigente impone al prestador el deber de reportar información de las
atenciones al Sistema General de Seguridad Social en Salud, mediante el Registro
Individual de Prestación de Servicios de Salud.

Dicho reporte comprende la identificación del paciente, el diagnóstico y el
procedimiento. Constituye una obligación legal, no requiere autorización
adicional del titular y no admite oposición sin incurrir en incumplimiento
normativo.
"""),
        ('6. Limitaciones conocidas del servicio', """
1.  La plataforma no está destinada a la atención de urgencias.
2.  El servicio puede interrumpirse por condiciones de conectividad.
3.  Las distancias y rutas son estimaciones que no consideran el estado de la
    vía ni las condiciones de seguridad.
4.  La disponibilidad de medicamentos depende del inventario físico del
    establecimiento farmacéutico.
5.  Los medicamentos de control especial requieren receta oficial numerada,
    documento que la plataforma no emite.
6.  La verificación de interacciones no comprende la totalidad de la
    farmacología; se concentra en las alertas de mayor consecuencia clínica en
    el ámbito de la atención primaria.
"""),
        ('7. Canales de reclamación', """
1.  Para asuntos relativos al tratamiento de datos personales: el módulo de
    datos personales de la plataforma.
2.  Para asuntos relativos a la prestación del servicio de salud:
    [[CANAL_PQRS]], con respuesta dentro de los quince días hábiles siguientes.

Agotado el trámite ante el prestador sin respuesta satisfactoria, el interesado
puede acudir a la Superintendencia de Industria y Comercio, tratándose de datos
personales, o a la Superintendencia Nacional de Salud, tratándose de la
prestación del servicio.
"""),
    ],
)


# =============================================================================
# Registro
# =============================================================================

DOCUMENTS = {
    'terms': TERMS,
    'privacy': PRIVACY,
    'telemedicine': TELEMEDICINE_CONSENT,
    'transparency': TRANSPARENCY,
}


def get_document(key):
    return DOCUMENTS.get(key)


def consent_reference(key):
    """Versión y hash del documento, para guardar junto a una aceptación."""
    documento = DOCUMENTS.get(key)
    if not documento:
        return None, None
    return documento.version, documento.content_hash


def pending_configuration(values=None):
    """Datos que el prestador aún debe completar en los textos legales.

    Un documento legal con `[[NIT_OPERADOR]]` sin reemplazar no es un documento
    legal. La aplicación lo señala en lugar de publicarlo tal cual.
    """
    values = values or {}
    pendientes = {}
    for clave, documento in DOCUMENTS.items():
        faltantes = [p for p in documento.pending_placeholders if not values.get(p)]
        if faltantes:
            pendientes[clave] = faltantes
    return pendientes


# Descripción de cada dato, para la pantalla donde se configuran.
PLACEHOLDER_LABELS = {
    'RAZON_SOCIAL_OPERADOR': (
        'Razón social del operador',
        'Nombre legal completo de la entidad que opera la plataforma.',
    ),
    'NIT_OPERADOR': (
        'NIT del operador',
        'Con dígito de verificación. Ejemplo: 900123456-7.',
    ),
    'DOMICILIO_OPERADOR': (
        'Domicilio del operador',
        'Dirección física completa, con ciudad y departamento.',
    ),
    'CORREO_PROTECCION_DATOS': (
        'Correo de protección de datos',
        'Canal por el que se reciben las solicitudes de habeas data. '
        'Debe estar atendido: la ley fija plazos de respuesta.',
    ),
    'TELEFONO_CONTACTO': (
        'Teléfono de contacto',
        'Línea de atención del responsable del tratamiento.',
    ),
    'AREA_RESPONSABLE': (
        'Área responsable del tratamiento',
        'Dependencia o cargo que atiende las solicitudes. '
        'Ejemplo: «Oficina de Atención al Usuario».',
    ),
    'CANAL_PQRS': (
        'Canal de PQRS',
        'Dónde se reciben peticiones, quejas y reclamos: correo, formulario o '
        'dirección física.',
    ),
    'PAIS_ALOJAMIENTO': (
        'País donde se aloja la base de datos',
        'Si es distinto de Colombia, hay transferencia internacional de datos '
        'y el artículo 26 de la Ley 1581 exige un fundamento legal.',
    ),
    'PROVEEDOR_ALOJAMIENTO': (
        'Proveedor de infraestructura',
        'Empresa que aloja los servidores. Ejemplo: Render, AWS, Azure.',
    ),
    'FUNDAMENTO_TRANSFERENCIA': (
        'Fundamento de la transferencia internacional',
        'Cuál de las excepciones del artículo 26 aplica: autorización expresa '
        'del titular, cláusulas contractuales, o declaración de conformidad de '
        'la SIC. Debe definirse con concepto del asesor jurídico.',
    ),
    'ESTADO_RNBD': (
        'Estado del registro ante el RNBD',
        'Registro Nacional de Bases de Datos de la SIC. Ejemplo: '
        '«Registrado el 12/03/2026» o «En trámite».',
    ),
}


def render_document(key, values=None):
    """Devuelve el documento con los marcadores reemplazados.

    Los que sigan sin valor se marcan visiblemente en lugar de dejarse crudos:
    quien lea el documento debe notar que falta un dato, no encontrarse un
    corchete doble sin explicación.
    """
    import re

    documento = DOCUMENTS.get(key)
    if not documento:
        return None

    values = values or {}

    def sustituir(match):
        nombre = match.group(1)
        valor = values.get(nombre)
        if valor:
            return str(valor)
        etiqueta = PLACEHOLDER_LABELS.get(nombre, (nombre, ''))[0]
        return f'«PENDIENTE: {etiqueta}»'

    secciones = [
        (titulo, re.sub(PLACEHOLDER_PATTERN, sustituir, cuerpo).strip())
        for titulo, cuerpo in documento.sections
    ]

    return {
        'key': documento.key,
        'title': documento.title,
        'version': documento.version,
        'effective_date': documento.effective_date,
        'summary': documento.summary,
        'legal_basis': documento.legal_basis,
        'sections': secciones,
        'content_hash': documento.content_hash,
        'pending': [p for p in documento.pending_placeholders if not values.get(p)],
    }
