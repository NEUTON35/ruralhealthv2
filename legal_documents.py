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
    version='2.0',
    effective_date=date(2026, 9, 6),
    summary=(
        'Reglas de uso de la plataforma, alcance del servicio, responsabilidades '
        'de cada parte y límites de la telemedicina.'
    ),
    legal_basis=(
        'Ley 1751 de 2015 (Estatutaria de Salud)',
        'Resolución 2654 de 2019 (Telesalud y telemedicina)',
        'Ley 1480 de 2011 (Estatuto del Consumidor)',
        'Ley 527 de 1999 (Comercio electrónico y firmas digitales)',
    ),
    sections=[
        ('1. Quiénes somos', """
RuralHealth Connect es una plataforma de apoyo a la prestación de servicios de
salud, operada por [[RAZON_SOCIAL_OPERADOR]], identificada con NIT
[[NIT_OPERADOR]], con domicilio en [[DOMICILIO_OPERADOR]].

La plataforma es una herramienta. **No es un prestador de servicios de salud.**
Los servicios asistenciales los prestan las instituciones y los profesionales
inscritos, cada uno bajo su propia habilitación en el Registro Especial de
Prestadores de Servicios de Salud (REPS) y bajo su propia responsabilidad
profesional.
"""),
        ('2. Qué hace y qué no hace la plataforma', """
**Hace:** agendar citas, sostener consultas por mensajería y videollamada,
registrar la historia clínica, emitir órdenes médicas, coordinar la entrega de
medicamentos y generar los reportes que exige la normativa.

**No hace:** diagnosticar, tratar ni decidir nada por el profesional. Toda
decisión clínica —incluido el uso que se dé a las alertas de seguridad que el
sistema muestra— es del profesional tratante y está bajo su exclusiva
responsabilidad.

Las verificaciones automáticas de alergias e interacciones son **apoyo a la
decisión**. Que no aparezca una alerta no significa que la prescripción sea
segura: significa que no se detectó ninguno de los patrones que el sistema
conoce.
"""),
        ('3. La plataforma no atiende urgencias', """
**Esta plataforma no sirve para emergencias vitales.** No hay atención
permanente ni tiempo de respuesta garantizado.

Ante dolor en el pecho, dificultad para respirar, pérdida de conciencia,
sangrado que no cede, signos de accidente cerebrovascular, intoxicación o
cualquier situación que ponga en riesgo la vida, acude de inmediato al servicio
de urgencias más cercano o llama a la línea 123.

No esperes respuesta por la plataforma en una urgencia.
"""),
        ('4. Telemedicina: alcance y límites', """
La atención por telemedicina se presta conforme a la Resolución 2654 de 2019.
Antes de la primera consulta en esta modalidad se solicita un consentimiento
informado específico, distinto del consentimiento general de datos.

La telemedicina tiene límites reales. El profesional puede determinar en
cualquier momento que tu caso requiere examen físico presencial, y en ese caso
debe remitirte. Que una consulta se pueda agendar por esta vía no significa que
sea la vía adecuada para todo motivo de consulta.

Requisitos de tu parte: conexión suficiente, un lugar donde puedas hablar con
privacidad y disposición a identificarte con tu documento.
"""),
        ('5. Quién puede usar la plataforma', """
Puedes registrarte si eres mayor de edad y tienes capacidad legal.

**Menores de edad:** su cuenta la gestiona quien ejerce la patria potestad o la
representación legal, que acredita esa condición ante la institución y responde
por el uso. El menor tiene derecho a ser escuchado según su edad y madurez
(Ley 1098 de 2006).

**Profesionales:** deben acreditar su registro médico profesional vigente. La
plataforma no permite emitir órdenes médicas sin ese registro.
"""),
        ('6. Tus obligaciones', """
- Entregar información veraz, completa y actualizada. Un dato clínico falso o
  incompleto —una alergia que no mencionas— puede causarte daño directo.
- Custodiar tus credenciales. No las compartas ni uses las de otra persona.
- No suplantar a nadie ni cargar archivos maliciosos.
- No usar la plataforma para obtener medicamentos por fuera de una indicación
  médica legítima.

El incumplimiento puede llevar a la suspensión de la cuenta, sin perjuicio de
las acciones legales que correspondan.
"""),
        ('7. Órdenes médicas y medicamentos', """
Las órdenes emitidas llevan la firma del profesional, su registro médico y un
sello criptográfico que permite verificar que no fueron alteradas.

Una orden tiene fecha de vencimiento. Vencida, no puede dispensarse.

Los medicamentos de control especial —estupefacientes y psicotrópicos— se rigen
por la Resolución 1478 de 2006 y requieren receta oficial numerada, que **no**
es la orden que genera esta plataforma.

La disponibilidad de medicamentos depende del inventario real de cada farmacia.
Que el sistema muestre existencias no garantiza la entrega si el inventario
físico cambió entre tanto.
"""),
        ('8. Disponibilidad del servicio', """
La plataforma está diseñada para zonas de baja conectividad, pero **no
garantizamos disponibilidad continua**. Puede haber interrupciones por
mantenimiento, fallas de conectividad, del proveedor de infraestructura o por
causas fuera de nuestro control.

Ninguna funcionalidad de la plataforma sustituye la atención presencial cuando
esta es necesaria.
"""),
        ('9. Ubicación, rutas y desplazamientos', """
Las distancias y rutas son estimaciones calculadas a partir de coordenadas.
**No** consideran el estado real de la vía, el clima, la disponibilidad de
transporte ni las condiciones de seguridad.

Verifica siempre las condiciones reales antes de desplazarte. En zonas rurales,
una ruta que el mapa muestra como corta puede ser intransitable.

Tu ubicación solo se usa si la autorizas expresamente. Puedes revocar esa
autorización en cualquier momento desde tu navegador o desde Ajustes.
"""),
        ('10. Cobros y facturación', """
Los servicios que tengan costo se informan antes de contratarlos, con su valor
total en pesos colombianos.

Los servicios de salud humana están excluidos de IVA conforme al artículo 476
numeral 1 del Estatuto Tributario.

Quien factura es la institución prestadora o el profesional independiente, cada
uno con su propia identificación tributaria. La plataforma no es parte de esa
relación económica salvo que se indique expresamente.

Derecho de retracto y reversión del pago: aplican en los términos de la Ley 1480
de 2011 cuando la contratación se haya hecho por medios electrónicos y proceda
según su naturaleza. Los servicios ya prestados no son susceptibles de retracto.
"""),
        ('11. Propiedad intelectual', """
El software, la marca y los contenidos de la plataforma pertenecen a
[[RAZON_SOCIAL_OPERADOR]] o a sus licenciantes.

**Tu historia clínica es tuya.** La institución la custodia por mandato legal,
pero los datos son del titular. Puedes obtener una copia completa en cualquier
momento desde «Mis datos».
"""),
        ('12. Responsabilidad', """
La plataforma responde por el correcto funcionamiento de las herramientas que
provee y por la custodia de la información conforme a la ley.

**No responde** por: las decisiones clínicas de los profesionales, la exactitud
de la información que suministre el usuario, los daños derivados de usar la
plataforma en una urgencia, ni por las interrupciones atribuibles a terceros.

Nada en este documento limita la responsabilidad por dolo o culpa grave, ni los
derechos irrenunciables que la ley reconoce al consumidor y al paciente.
"""),
        ('13. Suspensión y terminación', """
Puedes cerrar tu cuenta cuando quieras desde Ajustes. Al hacerlo se desactiva el
acceso y cesan los tratamientos no obligatorios.

**Tu historia clínica no se elimina.** La Resolución 839 de 2017 obliga al
prestador a conservarla un mínimo de 15 años. Ese deber legal prevalece sobre la
solicitud de supresión, conforme al artículo 9 de la Ley 1581 de 2012.

Descarga una copia de tus datos antes de cerrar la cuenta: después no podrás
solicitarla por ti mismo.
"""),
        ('14. Modificaciones', """
Podemos actualizar estos términos. Cada versión lleva número y fecha de entrada
en vigencia, y la anterior queda archivada.

Los cambios sustanciales se avisan dentro de la plataforma con al menos 15 días
de anticipación. Si no estás de acuerdo, puedes cerrar tu cuenta.
"""),
        ('15. Ley aplicable, reclamos y jurisdicción', """
Estos términos se rigen por la ley colombiana.

**Peticiones, quejas y reclamos (PQRS):** [[CANAL_PQRS]]. Respuesta dentro de los
15 días hábiles siguientes.

Si el reclamo se relaciona con el tratamiento de tus datos personales, puedes
acudir a la Superintendencia de Industria y Comercio una vez agotado el trámite
ante nosotros (artículo 16 de la Ley 1581 de 2012).

Si se relaciona con la prestación del servicio de salud, puedes acudir a la
Superintendencia Nacional de Salud.

Las controversias se someten a los jueces de la República de Colombia.
"""),
    ],
)


# =============================================================================
# Política de tratamiento de datos personales
# =============================================================================
#
# Estructura conforme al artículo 13 del Decreto 1377 de 2013, que enumera lo
# que la política debe contener como mínimo.

PRIVACY = LegalDocument(
    key='privacy',
    title='Política de Tratamiento de Datos Personales',
    version='2.0',
    effective_date=date(2026, 9, 6),
    summary=(
        'Qué datos recogemos, para qué, con quién los compartimos, cuánto los '
        'conservamos y cómo ejerces tus derechos.'
    ),
    legal_basis=(
        'Ley 1581 de 2012 (Protección de datos personales)',
        'Decreto 1377 de 2013',
        'Ley 1266 de 2008 (Habeas data)',
        'Resolución 1995 de 1999 (Historia clínica)',
        'Resolución 839 de 2017 (Manejo y conservación de la historia clínica)',
        'Ley 1712 de 2014 (Transparencia)',
    ),
    sections=[
        ('1. Responsable del tratamiento', """
**Razón social:** [[RAZON_SOCIAL_OPERADOR]]
**NIT:** [[NIT_OPERADOR]]
**Domicilio:** [[DOMICILIO_OPERADOR]]
**Correo para protección de datos:** [[CORREO_PROTECCION_DATOS]]
**Teléfono:** [[TELEFONO_CONTACTO]]
**Área responsable:** [[AREA_RESPONSABLE]]

Cada institución prestadora inscrita es responsable del tratamiento de la
historia clínica de sus propios pacientes. La plataforma actúa como encargada
del tratamiento respecto de esos datos, conforme al contrato suscrito con cada
institución.
"""),
        ('2. Qué datos recogemos', """
**De identificación:** nombre completo desagregado, tipo y número de documento,
fecha de nacimiento, sexo, teléfono, correo, dirección, municipio y departamento.

**De salud (datos sensibles):** motivo de consulta, antecedentes, alergias,
diagnósticos con código CIE-10, procedimientos con código CUPS, medicamentos
prescritos y dispensados, notas de evolución, mensajes del chat clínico,
archivos que adjuntes, y estado de gestación cuando lo registres.

**De aseguramiento:** entidad, régimen y plan.

**De ubicación:** coordenadas, **solo si las autorizas expresamente**. Puedes
revocar esa autorización en cualquier momento.

**Técnicos:** dirección IP, tipo de navegador, fecha y hora de acceso, y las
acciones que realizas dentro del sistema. Se conservan como registro de
auditoría y son obligatorios para poder demostrar quién accedió a una historia
clínica.

**De pago:** cuando aplique, el comprobante que cargues y el valor. **No
almacenamos números de tarjeta ni claves bancarias.**
"""),
        ('3. Datos sensibles: autorización reforzada', """
Los datos de salud son **datos sensibles** conforme al artículo 5 de la Ley 1581
de 2012. Su tratamiento exige tu autorización previa, expresa e informada, que
solicitamos de forma separada del resto.

Tienes derecho a **no** autorizarlos. Si no lo haces, no podremos prestarte
atención clínica por esta plataforma, porque no hay forma de hacerlo sin tratar
esos datos. Sí podrás mantener tu cuenta y consultar tu información.

Puedes revocar esta autorización cuando quieras desde «Mis datos». Al revocarla
se cierran las funciones clínicas; lo ya registrado se conserva por el deber
legal descrito en la sección 7.
"""),
        ('4. Para qué usamos tus datos', """
1. Prestarte atención en salud y hacer su seguimiento.
2. Llevar tu historia clínica conforme a la Resolución 1995 de 1999.
3. Emitir órdenes médicas y coordinar la entrega de medicamentos.
4. Verificar automáticamente alergias e interacciones antes de recetarte.
5. Agendar y recordarte tus citas.
6. Cumplir los reportes obligatorios al sistema de salud (RIPS).
7. Facturar y llevar la contabilidad de los servicios prestados.
8. Atender tus peticiones, quejas y reclamos.
9. Mantener el registro de auditoría que la ley exige.
10. Producir estadísticas **anonimizadas** de gestión, de las que no es posible
    identificarte.

**No usamos tus datos de salud para publicidad, ni los vendemos, ni los cedemos
a aseguradoras o empleadores para que tomen decisiones sobre ti.**
"""),
        ('5. Con quién los compartimos', """
- **Profesionales que te atienden**, y únicamente los de la institución donde te
  atiendes.
- **Farmacia** de la institución, y solo los medicamentos de tu orden.
- **Autoridades de salud**, en los reportes obligatorios (RIPS ante el
  Ministerio de Salud y las entidades de vigilancia).
- **Tu entidad aseguradora**, cuando sea necesaria para el reconocimiento y pago
  de los servicios.
- **Autoridades judiciales o administrativas**, cuando medie orden que así lo
  disponga.
- **Proveedores de infraestructura**, bajo contrato de encargo que los obliga a
  la misma confidencialidad y les prohíbe cualquier uso propio.
"""),
        ('6. Transferencia internacional de datos', """
**Parte de la infraestructura que aloja la base de datos se encuentra fuera de
Colombia**, en [[PAIS_ALOJAMIENTO]], operada por [[PROVEEDOR_ALOJAMIENTO]].

Esto constituye una transferencia internacional de datos en los términos del
artículo 26 de la Ley 1581 de 2012, que la restringe a países con nivel adecuado
de protección, salvo que medie autorización expresa e inequívoca del titular,
cláusulas contractuales que garanticen la protección, o declaración de
conformidad de la Superintendencia de Industria y Comercio.

Fundamento aplicado en nuestro caso: [[FUNDAMENTO_TRANSFERENCIA]].

Los datos viajan cifrados en tránsito y permanecen cifrados en reposo con llaves
que no están en poder del proveedor de infraestructura.

Si no autorizas esta transferencia, indícalo por el canal de la sección 10.
"""),
        ('7. Cuánto tiempo los conservamos', """
| Información | Conservación | Fundamento |
| :--- | :--- | :--- |
| Historia clínica | Mínimo 15 años desde la última atención | Resolución 839 de 2017 |
| Órdenes médicas | Mínimo 15 años (parte de la historia) | Resolución 839 de 2017 |
| Registro de dispensación | 5 años | Resolución 1403 de 2007 |
| Consentimientos informados | Mínimo 15 años | Resolución 839 de 2017 |
| Registro de auditoría | 5 años | Deber de demostrar cumplimiento |
| Facturación y contabilidad | 10 años | Código de Comercio, artículo 28 |
| Datos de cuenta sin actividad | Hasta que solicites el cierre | — |

Cumplido el plazo, la información se elimina o se anonimiza de forma
irreversible.
"""),
        ('8. Tus derechos', """
Como titular puedes, conforme al artículo 8 de la Ley 1581 de 2012:

- **Conocer** qué datos tuyos tratamos y cómo.
- **Actualizar y rectificar** los que estén incompletos o sean inexactos.
- **Solicitar prueba** de la autorización que otorgaste.
- **Ser informado** del uso que damos a tus datos.
- **Presentar quejas** ante la Superintendencia de Industria y Comercio.
- **Revocar la autorización** y **solicitar la supresión**, con el límite de la
  sección 7: la historia clínica no puede suprimirse mientras corra el plazo de
  conservación.
- **Acceder gratuitamente** a tus datos.

Todo esto se ejerce desde «Mis datos» dentro de la plataforma, o por el canal de
la sección 10.
"""),
        ('9. Cómo protegemos la información', """
- Cifrado de la historia clínica y de los datos personales **en reposo**, con
  llaves fuera de la base de datos.
- Cifrado en tránsito (HTTPS con HSTS).
- Búsqueda por documento mediante índice ciego: el número no se almacena de
  forma que permita buscarlo directamente.
- Control de acceso por rol y aislamiento entre instituciones.
- Registro de auditoría encadenado por hash: alterar una entrada rompe la
  verificación de todas las siguientes.
- Bloqueo de cuenta ante intentos repetidos de acceso.
- Ninguna página con datos clínicos se almacena en el dispositivo.

Ningún sistema es invulnerable. Ante un incidente que comprometa tus datos, te
informaremos y reportaremos a la Superintendencia de Industria y Comercio
conforme al artículo 17 literal n) de la Ley 1581 de 2012.
"""),
        ('10. Cómo ejercer tus derechos', """
**En línea:** entra a «Mis datos» dentro de la plataforma. Puedes descargar una
copia completa, pedir correcciones, solicitar la supresión o revocar
autorizaciones.

**Por escrito:** [[CORREO_PROTECCION_DATOS]] o [[DOMICILIO_OPERADOR]].

Indica tu nombre, documento, el derecho que ejerces y un canal de respuesta.

**Plazos** (Decreto 1377 de 2013):
- Consultas: 10 días hábiles, prorrogables por 5 más.
- Reclamos: 15 días hábiles, prorrogables por 8 más.

Si la respuesta no te satisface o no llega, puedes acudir a la Superintendencia
de Industria y Comercio.
"""),
        ('11. Menores de edad', """
El tratamiento de datos de menores es excepcional y solo procede cuando responde
a su interés superior y se respeta su derecho a ser escuchado (artículo 7 de la
Ley 1581 de 2012 y artículo 12 del Decreto 1377 de 2013).

La autorización la otorga quien ejerce la patria potestad o la representación
legal, acreditando esa condición.
"""),
        ('12. Cookies y almacenamiento local', """
Usamos únicamente lo necesario para que la plataforma funcione:

- **Cookie de sesión:** te mantiene identificado. Se borra al cerrar sesión.
- **Token de seguridad (CSRF):** impide que otro sitio actúe en tu nombre.
- **Almacenamiento local:** guarda mensajes que escribiste sin conexión, para
  enviarlos al recuperar señal. Se borra al cerrar sesión.

**No usamos cookies de publicidad, analítica de terceros ni rastreadores.**
"""),
        ('13. Registro Nacional de Bases de Datos', """
Las bases de datos que administramos se registran ante el Registro Nacional de
Bases de Datos de la Superintendencia de Industria y Comercio, conforme a la Ley
1581 de 2012 y sus decretos reglamentarios.

Estado del registro: [[ESTADO_RNBD]].
"""),
        ('14. Vigencia', """
Esta política rige desde su fecha de entrada en vigencia y se mantiene mientras
la plataforma opere.

Las bases de datos se conservan por los plazos de la sección 7.

Los cambios sustanciales se comunican dentro de la plataforma con al menos 15
días de anticipación. Cada versión queda archivada con su fecha.
"""),
    ],
)


# =============================================================================
# Consentimiento informado de telemedicina
# =============================================================================

TELEMEDICINE_CONSENT = LegalDocument(
    key='telemedicine',
    title='Consentimiento Informado para Atención por Telemedicina',
    version='1.0',
    effective_date=date(2026, 9, 6),
    summary=(
        'Qué implica atenderse a distancia, qué límites tiene y qué alternativas '
        'existen. Se acepta antes de la primera consulta por esta modalidad.'
    ),
    legal_basis=(
        'Resolución 2654 de 2019 (Telesalud y telemedicina)',
        'Ley 1419 de 2010 (Telesalud)',
        'Resolución 1995 de 1999 (Historia clínica)',
    ),
    sections=[
        ('En qué consiste', """
La telemedicina es atención en salud prestada a distancia, con apoyo de
tecnologías de la información. El profesional que te atiende está habilitado y
registrado igual que en una consulta presencial, y la atención queda en tu
historia clínica de la misma forma.
"""),
        ('Qué ganas', """
- Evitas desplazamientos largos, costosos o riesgosos.
- Accedes a profesionales que no están en tu municipio.
- Acortas los tiempos de espera.
- Puedes hacer seguimiento de un tratamiento sin viajar.
"""),
        ('Qué límites tiene — léelo con atención', """
**El profesional no puede examinarte físicamente.** No puede palpar, auscultar,
tomar tu presión ni revisar una lesión con sus manos. Eso significa que:

- Hay diagnósticos que **no** pueden hacerse por esta vía.
- El profesional puede determinar que necesitas consulta presencial y remitirte.
- La calidad de la atención depende de la conexión: si el audio o el video
  fallan, la consulta puede interrumpirse o suspenderse.
- Si la información que das es incompleta, la orientación puede ser equivocada.

**La telemedicina no reemplaza la atención presencial cuando esta es
necesaria.** Aceptar este consentimiento no te obliga a atenderte solo por esta
vía: puedes pedir consulta presencial en cualquier momento.
"""),
        ('No sirve para urgencias', """
**No uses la telemedicina en una emergencia.** No hay atención permanente ni
tiempo de respuesta garantizado.

Ante una situación que ponga en riesgo la vida, acude al servicio de urgencias
más cercano o llama a la línea 123.
"""),
        ('Tus datos en la consulta', """
La consulta queda registrada en tu historia clínica: los mensajes, los archivos
que compartas y las notas del profesional.

**Grabación:** las videollamadas no se graban salvo que lo autorices de forma
expresa y separada para cada ocasión.

Todo se cifra en reposo y en tránsito, y solo accede el equipo que te atiende en
la institución donde te atiendes.
"""),
        ('Tus derechos', """
- Puedes **rechazar** la telemedicina y pedir atención presencial, sin que eso
  afecte tu acceso al servicio.
- Puedes **interrumpir** la consulta en cualquier momento.
- Puedes **revocar** este consentimiento cuando quieras desde «Mis datos».
- Puedes **preguntar** todo lo que necesites antes de aceptar.
- Puedes pedir una **copia** de este documento y de tu historia clínica.
"""),
        ('Declaración', """
Al aceptar declaras que:

1. Leíste y entendiste este documento.
2. Tuviste oportunidad de preguntar y te resolvieron las dudas.
3. Entiendes los límites de la atención a distancia, en especial que **no hay
   examen físico**.
4. Sabes que puedes pedir atención presencial en cualquier momento.
5. Aceptas de forma libre y voluntaria atenderte por esta modalidad.

Queda constancia con la fecha, la hora, tu dispositivo y la versión de este
documento.
"""),
    ],
)


# =============================================================================
# Transparencia y derechos del paciente
# =============================================================================

TRANSPARENCY = LegalDocument(
    key='transparency',
    title='Transparencia y Derechos del Paciente',
    version='2.0',
    effective_date=date(2026, 9, 6),
    summary=(
        'Cómo funciona el sistema por dentro, qué decide un algoritmo y qué '
        'decide una persona, y cuáles son tus derechos como paciente.'
    ),
    legal_basis=(
        'Ley 1751 de 2015 (Estatutaria de Salud)',
        'Ley 1712 de 2014 (Transparencia y acceso a la información)',
        'Resolución 1995 de 1999 (Historia clínica)',
    ),
    sections=[
        ('Tus derechos como paciente', """
La Ley 1751 de 2015 reconoce la salud como derecho fundamental. En particular
tienes derecho a:

- Recibir atención **de urgencia sin requisito previo** de pago ni autorización.
- **Elegir** libremente tu prestador dentro de la red disponible.
- Recibir **información clara y completa** sobre tu estado de salud, las opciones
  de tratamiento y sus riesgos, en lenguaje que entiendas.
- **Aceptar o rechazar** cualquier procedimiento, después de estar informado.
- Que tu información se mantenga **confidencial**.
- Acceder a tu **historia clínica** y obtener copia.
- Recibir un trato **digno**, sin discriminación de ningún tipo.
- **Reclamar** y recibir respuesta.
- Una **segunda opinión** médica.
"""),
        ('Qué decide el sistema y qué decide una persona', """
Es importante que sepas dónde interviene un automatismo:

**Decide el sistema, sin criterio clínico:**
- Qué horarios muestra como disponibles.
- Qué farmacia sugiere según la distancia.
- Si hay existencias de un medicamento.
- Si una orden está vencida.

**Advierte el sistema, decide el profesional:**
- Las alertas de alergia, interacción o contraindicación. El sistema las muestra;
  **la decisión de recetar o no es del profesional**, y si decide continuar pese
  a una alerta debe dejar por escrito su justificación, que queda registrada.

**Decide siempre una persona:**
- Tu diagnóstico.
- Tu tratamiento.
- Si necesitas remisión o atención presencial.
- Si te entregan o no un medicamento.

**Ningún algoritmo de esta plataforma diagnostica, prescribe ni niega atención.**
"""),
        ('Cómo funcionan las alertas de seguridad', """
Antes de que un profesional firme una orden, el sistema la contrasta con tus
alergias registradas, tu tratamiento activo y tu estado de gestación, y busca
interacciones conocidas.

**Límite importante:** el sistema solo conoce lo que está registrado. Si tu
alergia no está en tu historia, no puede advertirla. Por eso es fundamental que
menciones todas tus alergias, incluso las que creas menores.

Que no aparezca una alerta **no significa que sea seguro**: significa que no se
detectó ninguno de los patrones que el sistema conoce.
"""),
        ('Qué registramos de cada acceso', """
Cada vez que alguien consulta tu historia clínica queda registrado quién fue,
cuándo y desde dónde. Ese registro está encadenado criptográficamente: si
alguien intentara borrar una entrada, la manipulación sería detectable.

Puedes solicitar el detalle de quién ha accedido a tu información.
"""),
        ('Reportes obligatorios', """
La normativa obliga a reportar información de las atenciones al sistema de salud
(RIPS). Ese reporte incluye tu identificación, el diagnóstico y el procedimiento.

Es una obligación legal, no requiere autorización adicional y no puede
rechazarse sin incumplir la norma.
"""),
        ('Limitaciones que debes conocer', """
Preferimos decirlo antes que dejarte descubrirlo:

- La plataforma **no atiende urgencias**.
- Puede haber **interrupciones** por conectividad.
- Las distancias y rutas son **estimaciones**; no consideran el estado de la vía
  ni la seguridad.
- La disponibilidad de medicamentos depende del **inventario físico**.
- Los medicamentos de control especial requieren **receta oficial numerada**, que
  esta plataforma no emite.
- La verificación de interacciones **no cubre toda la farmacología**: se centra
  en las alertas de mayor consecuencia en atención primaria.
"""),
        ('Cómo reclamar', """
**Dentro de la plataforma:** «Mis datos» para lo relacionado con tu información.

**Sobre el servicio de salud:** [[CANAL_PQRS]]. Respuesta en 15 días hábiles.

**Si no te responden o no quedas conforme:**
- Datos personales: Superintendencia de Industria y Comercio.
- Servicio de salud: Superintendencia Nacional de Salud.
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
        'la SIC. Consúltalo con tu asesor jurídico.',
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
