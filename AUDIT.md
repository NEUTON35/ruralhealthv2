# RuralHealth Connect — Auditoría Técnica, Clínica y Legal

**Fecha:** 2026-09-05
**Alcance:** revisión completa del código (7.191 líneas Python + 7.472 líneas de plantillas), modelo de datos,
superficie de seguridad, cumplimiento normativo colombiano y seguridad del paciente.
**Contexto:** el sistema se destina a operación real en la red de salud. Cada hallazgo se clasifica por el
daño que puede causar a un paciente o la exposición legal que genera para el prestador.

---

## Clasificación

| Nivel | Significado |
| :--- | :--- |
| **P0** | Riesgo directo al paciente o incumplimiento legal en curso. Bloquea despliegue. |
| **P1** | Requisito normativo o de seguridad ausente. Bloquea operación sostenida. |
| **P2** | Fiabilidad, mantenibilidad y operación. Necesario antes de escalar. |

---

## P0 — Bloqueantes

### P0-1 · Credenciales de producción en el repositorio

`.env` contiene la cadena de conexión completa a la base de datos PostgreSQL de producción
(host, usuario y contraseña en claro), además de las tres llaves criptográficas del sistema
(`SECRET_KEY`, `JWT_SECRET_KEY`, `FIELD_KEY_SEED`).

La `FIELD_KEY_SEED` deriva la clave Fernet que cifra **toda la PII y la historia clínica** en reposo.
Quien tenga ese archivo puede descifrar el 100 % de los datos de salud del sistema.

`app.py` además incluía contraseñas literales de `superadmin` y `admin` como valor por defecto en el código
fuente, y las escribía en `logs/ruralhealth.log` en texto plano.

**Impacto:** compromiso total. Incumple el deber de seguridad del artículo 4 de la Ley 1581 de 2012.
**Acción:** eliminadas del código; arranque *fail-closed*; rotación obligatoria documentada en `SECURITY.md`.

### P0-2 · El Service Worker cachea historia clínica en dispositivos compartidos

`static/service-worker.js` guardaba en `CacheStorage` **toda** respuesta HTML `200`, incluidos chats
clínicos, órdenes médicas e historias de pacientes. La aplicación envía `Cache-Control: no-store` en
respuestas autenticadas y el Service Worker lo ignoraba deliberadamente.

En un puesto de salud rural el dispositivo es compartido. El siguiente usuario del navegador podía
recuperar sin conexión —y sin sesión— las páginas del paciente anterior. No había purga al cerrar sesión.

**Impacto:** fuga de datos sensibles de salud a terceros no autorizados.
**Acción:** el Service Worker ya no cachea nada autenticado; solo el *shell* público. Purga total en `logout`.

### P0-3 · La API JWT evade el bloqueo de cuenta

`POST /api/auth/token` autenticaba con usuario y contraseña **sin** consultar `login_is_locked()`,
sin registrar fallos y sin auditar. El bloqueo tras 5 intentos solo protegía el formulario web.
Un atacante obtenía un canal de fuerza bruta ilimitado contra cualquier cuenta, incluida `superadmin`,
y a cambio recibía un *refresh token* de 7 días.

**Acción:** mismo bloqueo, misma auditoría, respuesta genérica y de tiempo constante; revocación al cerrar sesión.

### P0-4 · Condición de carrera en la dispensación de medicamentos

`evaluate_dispatch_status()` leía el stock y `confirm_delivery()` lo descontaba después, sin bloqueo de fila
ni transacción. Dos expendedores simultáneos podían entregar el mismo inventario dos veces.
Peor: `routes_expendedor.confirm` invocaba el evaluador con `ignore_commitments=True`, ignorando
deliberadamente el stock ya reservado para otros pacientes.

**Impacto:** un paciente con ticket válido y stock reservado se queda sin su medicamento. En tratamientos
crónicos o críticos esto es daño directo.
**Acción:** bloqueo pesimista (`SELECT … FOR UPDATE`), decremento condicional atómico y verificación de
invariantes; `ignore_commitments` eliminado del camino de entrega.

### P0-5 · Sin trazabilidad de inventario ni de entrega

El stock se mutaba en sitio (`stock.cantidad = …`). No existía registro de quién movió qué, cuándo, ni por qué.
`delivered_json` se sobrescribía en cada entrega parcial, destruyendo el historial anterior.

Para medicamentos esto incumple la trazabilidad exigida al servicio farmacéutico
(Decreto 780 de 2016 / Resolución 1403 de 2007).

**Acción:** libro mayor inmutable con encadenamiento hash (`StockLedgerEntry`, `DispensingLedgerEntry`).

### P0-6 · Órdenes médicas sin validación clínica ni requisitos legales

La emisión de recetas (`routes_doctor.prescription`) no validaba nada:

- Los códigos CIE-10 se guardaban sin pasar por `validate_medical_code()` — la función existe y **nunca se llamaba** en este flujo.
- No se exigía registro médico profesional. Una cuenta de médico creada sin `medical_registration`
  podía firmar órdenes con validez legal.
- No se exigía firma. `signature_hash` es columna del modelo y **nunca se poblaba**.
- Sin verificación de alergias del paciente ni de interacciones medicamentosas.
- Sin límite superior de vigencia: se aceptaba una orden válida por siglos.
- `_generate_order_number()` usaba `last_order.id + 1`, con carrera: dos órdenes concurrentes reciben el mismo número legal.

**Impacto:** prescripción de un medicamento al que el paciente es alérgico. Es el riesgo de daño más alto del sistema.
**Acción:** motor `clinical_safety.py` (alergias, interacciones, duplicidad terapéutica, dosis, controlados),
validación CIE-10 obligatoria, registro médico y firma exigidos, numeración atómica y firma criptográfica de la orden.

### P0-7 · Inyección de fórmulas en exportaciones CSV

`export_clinical_records` y `rips_service` escribían campos controlados por el usuario directamente al CSV.
Un nombre que empiece por `=`, `+`, `-` o `@` se ejecuta como fórmula al abrir el archivo en Excel.
Los destinatarios de estas exportaciones son auditores y entes de control.

**Acción:** neutralización de fórmulas en todas las salidas tabulares.

### P0-8 · RIPS con datos fabricados

`rips_service.generate_rips` emitía literales inventados en cada registro: apellidos `"Apellido1"`/`"Apellido2"`,
edad `30`, sexo `M`, entidad `EPS000`, factura `FAC-001`, municipio `11/001`.

Radicar esto ante el sistema de salud es reporte de información falsa.

**Acción:** el generador exige datos reales, valida cada registro y **rechaza** la exportación
incompleta con un informe de qué falta y en qué paciente.

### P0-9 · Reescritura completa de la base de datos en cada arranque

`ensure_security_schema()` se ejecuta a nivel de módulo y recorre `User`, `Chat`, `Message`, `Appointment`,
`Rating`, `MedicalHistory`, `Stock`, `MedicalOrder` y `MedicationPickupTicket` **enteros**, marcando cada campo
cifrado como modificado para forzar su recifrado.

Con datos reales el arranque se vuelve minutos u horas, cada worker de Gunicorn lo repite en paralelo sobre la
misma base, y el `ALTER TABLE` construido con `f-string` reemplaza a un sistema de migraciones.

**Acción:** migraciones Alembic; el arranque ya no reescribe datos; el *backfill* es un comando explícito.

---

## P1 — Requisitos ausentes

| # | Hallazgo | Acción |
| :--- | :--- | :--- |
| P1-1 | **Sin recuperación de contraseña.** Un médico rural que olvida su clave queda fuera del sistema de forma permanente. | Flujo de restablecimiento con token de un solo uso, expiración y auditoría. |
| P1-2 | **Sin registro de alergias del paciente.** No existía el modelo. | `PatientAllergy` + verificación bloqueante en la prescripción. |
| P1-3 | **Sin derechos de habeas data.** Ley 1581 art. 8: acceso, rectificación y supresión. No implementados. | `routes_privacy.py`: exportación completa de datos y solicitud de supresión con trazabilidad. |
| P1-4 | **Auditoría no consultable ni a prueba de manipulación.** `AuditLog` se escribía y nadie podía leerlo; un DBA podía alterarlo sin dejar rastro. | Encadenamiento hash + visor con filtros para administradores. |
| P1-5 | **Bloqueo de login en memoria del proceso.** Se pierde al reiniciar y no se comparte entre workers: el atacante reinicia el contador cambiando de worker. | Bloqueo persistido en base de datos. |
| P1-6 | **Rate limiting en `memory://`.** Mismo defecto: por proceso, no por sistema. | Backend configurable (Redis) con degradación advertida. |
| P1-7 | **Exención CSRF global de `/api/`.** Cualquier endpoint futuro bajo `/api/` nace sin protección CSRF. | Lista explícita de exenciones. |
| P1-8 | **Sin caducidad de sesión por inactividad.** Solo vida absoluta de cookie. | Inactividad + vida absoluta, aplicadas en servidor. |
| P1-9 | **Contraseñas semilla nunca forzadas a cambiar.** `admin`/`superadmin` operan indefinidamente con la clave inicial. | `must_change_password` con bloqueo de navegación. |
| P1-10 | **Médicos sin registro profesional obligatorio.** El alta de médico no lo pedía. | Campo obligatorio y validado en el alta. |
| P1-11 | **Enumeración de usuarios por tiempo de respuesta** en login: el hash solo se calcula si el usuario existe. | Comparación de tiempo constante con hash señuelo. |
| P1-12 | **Sin verificación de identidad del paciente en el mostrador.** El expendedor confirmaba entregas sin contrastar documento. | Confirmación de cédula y registro de quién recibió. |
| P1-13 | **KPI incorrecto:** «Pacientes nuevos este mes» contaba *todos* los pacientes históricos. `User` no tenía `created_at`. | Columna añadida y métrica corregida. |
| P1-14 | **Consentimiento sin versionado.** No se registra *qué texto* aceptó el paciente; sin eso el consentimiento no es defendible. | Versión y hash del documento en cada aceptación. |

---

## P2 — Operación y fiabilidad

| # | Hallazgo | Acción |
| :--- | :--- | :--- |
| P2-1 | **Cero pruebas automatizadas** en un sistema de soporte a decisiones clínicas. | Suite `pytest`: seguridad, aislamiento entre clínicas, concurrencia de inventario y seguridad clínica. |
| P2-2 | **Sin migraciones.** `Flask-Migrate` declarado, carpeta inexistente; el esquema evolucionaba con `ALTER TABLE` a mano. | Alembic inicializado. |
| P2-3 | **Sin control de versiones.** El proyecto no era un repositorio Git. | Repositorio inicializado con `.gitignore` que excluye `.env`, `uploads/`, `logs/` e `instance/`. |
| P2-4 | **Dependencia de CDN en una app para zonas sin conectividad.** Tailwind, Lucide y Leaflet se cargan de `unpkg`/`cdn.tailwindcss.com`, sin SRI y con `@latest` (versión no fijada). Sin internet la interfaz se degrada; con internet, un CDN comprometido ejecuta código arbitrario sobre datos clínicos. | Versiones fijadas, SRI y precarga en el Service Worker. |
| P2-5 | **Sin `/health` ni `/ready`.** No hay forma de supervisar el servicio. | Sondas de salud y disponibilidad. |
| P2-6 | **Sin configuración de despliegue.** No hay WSGI de producción, contenedor ni documentación operativa. | `wsgi.py`, `gunicorn.conf.py`, `Dockerfile`, `DEPLOYMENT.md`. |
| P2-7 | **N+1 y carga completa en memoria.** `/api/omnisearch` carga todos los médicos, pacientes y stock de la clínica y filtra en Python. | Filtrado en base de datos, con límites. |
| P2-8 | **Sin límite de tamaño ni rotación de `uploads/`.** Crecimiento sin control en el disco de un puesto rural. | Cuota por clínica y verificación de espacio. |
| P2-9 | **Excepciones silenciadas.** `except Exception: pass` en el guardado de firma; `print()` para errores. | Registro estructurado. |
| P2-10 | **`db.create_all()` en producción.** Crea esquema sin control de versión. | Solo en desarrollo y pruebas. |
| P2-11 | **Derivación de clave en cada acceso a un campo cifrado.** `_fernet()` ejecutaba 600.000 iteraciones de PBKDF2 en **cada** cifrado y descifrado. Como `EncryptedText` interviene en toda lectura y escritura de PII, listar cien pacientes disparaba cientos de derivaciones — décimas de segundo por campo sobre el hardware de un puesto rural. Es la causa real de la lentitud que reportaron los usuarios encuestados. | Instancia Fernet cacheada por proceso. Medido en la suite de pruebas: 4 min 21 s → 58 s. |

> **Sobre el hallazgo P2-11.** La encuesta de campo recogida en el README registra
> dos comentarios sobre velocidad («falta velocidad pero cumple con sus funciones»,
> «la velocidad de carga»). La optimización que se hizo entonces fue eliminar el
> bucle de consultas en la matriz de farmacia, que era real pero no era la causa
> principal: el coste dominante estaba en la derivación de clave repetida en cada
> campo cifrado, que afectaba a **todas** las pantallas con datos de pacientes.

---

---

# Segunda pasada — 2026-09-06

Revisión adicional tras una pregunta sobre convenciones de interfaz. La pregunta
era de estilo; la revisión encontró **10 defectos**, dos de ellos de corrección de
datos y uno de incumplimiento legal en curso.

## P0 — Bloqueantes

### P0-10 · El sistema borraba historias clínicas de forma permanente

Tres caminos distintos eliminaban registros clínicos con `DELETE`:

| Ruta | Qué borraba |
| :--- | :--- |
| `routes_superadmin._delete_patient` | `MedicalHistory`, `MedicalOrder`, `MedicationPickupTicket`, `Chat`, `Message` del paciente |
| `routes_superadmin._delete_clinic` | Lo mismo, para **todos** los pacientes de la clínica |
| `routes_admin` acción `delete_user` | Los chats y citas del profesional — que son consultas **de sus pacientes** |
| `routes_settings` acción `delete_account` | Chats y citas propios, y la fila del usuario |

Esto incumplía el deber de conservar la historia clínica un mínimo de 15 años
(Resolución 839 de 2017) y contradecía de forma directa lo que el propio módulo de
habeas data le explica al paciente: que su historia no puede eliminarse ni aunque
la solicite. La pantalla de ajustes llegaba a prometer «se borrarán todos tus
datos, historiales médicos y citas de forma permanente».

Dar de baja a un médico destruía además la historia de terceros que no tenían
relación alguna con esa baja.

**Acción:** los cuatro caminos pasan a archivar: la cuenta se desactiva, las
consultas abiertas se cierran, las citas futuras se cancelan —liberando el
horario— y el registro clínico permanece. Los textos de la interfaz dicen ahora lo
que realmente ocurre. Una prueba analiza el árbol de sintaxis de todas las rutas
para impedir que vuelva a aparecer un borrado masivo de tablas clínicas.

### P0-11 · Dos pacientes podían ocupar el mismo horario

La reserva de cita era «consultar y luego insertar», sin nada entre ambas
operaciones. Dos pacientes pulsando el mismo horario a la vez pasaban los dos la
comprobación y quedaban los dos agendados con el mismo profesional a la misma
hora. Ocurría en los cuatro caminos que crean citas, y el de «entrega pendiente»
las fijaba todas a las 09:00, de modo que el choque era sistemático.

**Acción:** índice único parcial en base de datos sobre `(doctor_id, date, time)`,
excluyendo las canceladas y no asistidas. Es parcial para que un horario liberado
vuelva a ofrecerse. La migración sanea los duplicados que ya existan marcando como
canceladas las posteriores —sin borrarlas— y avisa de cuáles son, para reprogramar
a esos pacientes.

## P1 — Correcciones de datos y acceso

| # | Hallazgo | Acción |
| :--- | :--- | :--- |
| P1-15 | **La gráfica de tendencia mostraba meses duplicados y omitía otros.** Recorría los meses restando bloques de 30 días. En marzo de 2026 dibujaba diciembre dos veces y se saltaba febrero entero; en mayo repetía enero. El administrador decidía sobre una curva con un mes inventado y otro ausente. | Recorrido por meses reales. Prueba que recorre 36 meses consecutivos. |
| P1-16 | **Las citas canceladas bloqueaban el horario para siempre.** Un paciente que no se presentaba inutilizaba ese cupo de forma permanente, en agendas donde cada consulta cuenta. El contador de cupos disponibles las incluía, así que además mostraba menos disponibilidad de la real. | Solo cuentan las citas que ocupan de verdad. |
| P1-17 | **243 de 256 campos de formulario sin nombre accesible.** Un lector de pantalla los anunciaba como «campo de texto, en blanco»: quien tiene baja visión no podía saber si escribía la dosis o la cantidad. | `aria-label` en los 243, tomado del texto descriptivo real y no del ejemplo del marcador. |
| P1-18 | **Emojis dentro de datos que se guardan en la base.** 21 literales con emoji iban a `Notification.title`, `Notification.message` y `Message.content`, que se cifran, salen en la exportación de historia clínica que reciben los auditores y se imprimen en la orden. En Android antiguo muchos renderizan como un cuadro vacío dentro de un dato clínico. | Retirados de todo literal de Python; prueba que impide su reintroducción. |
| P1-19 | **Toda escritura dependía de JavaScript.** 61 formularios no llevaban el token CSRF en el HTML; lo inyectaba un script al cargar la página. Si ese script no se ejecuta —conexión intermitente, navegador antiguo—, cada envío devuelve un 400 y el usuario ve un formulario que aparentemente no hace nada. | Token en el HTML de los 61. La inyección queda como red de seguridad. |

## P2 — Interfaz y cadena de suministro

| # | Hallazgo | Acción |
| :--- | :--- | :--- |
| P2-12 | **Contraste por debajo del mínimo legal de accesibilidad.** 258 usos de `text-slate-400` sobre blanco dan 2.56:1; WCAG AA exige 4.5:1. Sobre pantalla barata y a pleno sol, ese texto no se lee. | Subido a `slate-600` (7.58:1) en texto; los iconos se dejan intactos. |
| P2-13 | **188 fragmentos de texto por debajo de 12 px** (`text-[10px]`, `text-[9px]`), incluidos códigos de recogida y etiquetas de severidad de alergia. | Elevados al mínimo legible. |
| P2-14 | **Recurso externo sin versión fijada ni verificación.** `lucide@latest` cargaba desde un CDN sin `integrity`: cualquier contenido que sirviera ese CDN se ejecutaba sobre páginas con historia clínica abierta. | Primero se fijó versión y SRI. Tras comprobar en campo que el fallo era peor de lo previsto (ver P0-15), **se eliminó toda dependencia de CDN**. |
| P2-15 | **Glassmorphism y `transition-all`** (13 y 64 usos) sobre el hardware de gama baja que el propio README declara como objetivo. `transition-all` anima también las propiedades que fuerzan recálculo de maquetación. | Retirado el desenfoque; transiciones acotadas a color. |
| P2-16 | **`target="_blank"` sin `rel`** (8) e **imágenes sin `alt`** (4). Lo primero entrega `window.opener` al destino, que puede redirigir la pestaña original a una copia falsa del login. | Corregidos, con pruebas. |
| P2-17 | **Petición rechazada en cada carga de página.** `pwa.js` pedía la agenda sin conexión —que es solo de pacientes— desde todos los roles, generando un 403 por página y llenando la consola de errores que ocultaban los reales. | La petición se hace solo cuando corresponde. |

---

---

# Tercera pasada — 2026-09-06

Revisión de conformidad normativa: recetas, documentos legales, facturación e
interfaz.

## P0 — Bloqueantes

### P0-12 · La prescripción no cumplía la Resolución 1403 de 2007

La norma enumera catorce elementos que debe contener una prescripción. Faltaban
seis, y su ausencia hace el documento **no dispensable**:

| Faltaba | Por qué importa |
| :--- | :--- |
| Denominación Común Internacional | El campo era texto libre: «Dolex» y «paracetamol» entraban igual y la farmacia no podía saber si era una marca. La norma exige prescribir por genérico. |
| Concentración y forma farmacéutica separadas | «Amoxicilina 500 mg» no dice si es cápsula o suspensión. En pediatría esa diferencia cambia cómo se administra. |
| Duración del tratamiento | Sin ella no se puede verificar que la cantidad corresponda a la pauta. |
| Número de historia clínica | Exigido expresamente; no existía en el modelo. |
| Dirección y teléfono del paciente | Existían en el perfil pero no se copiaban a la orden. |
| Cantidad en letras | Una cifra en números se altera con un trazo: «10» se convierte en «100». |

**Acción:** cada renglón valida los nueve campos; sin ellos la orden no se emite.
Los datos del paciente se congelan en la orden en lugar de leerse del perfil: una
orden es un documento con fecha y no puede cambiar porque el perfil cambie
después.

### P0-13 · Telemedicina sin el consentimiento que exige la norma

La Resolución 2654 de 2019 obliga a registrar la modalidad de atención y, en
telemedicina, a obtener un consentimiento informado **específico**, distinto del
consentimiento general de datos. Lo que el paciente debe entender ahí no es cómo
se tratan sus datos, sino que **no habrá examen físico**.

No existía. Se emitían órdenes por telemedicina sin constancia de que el paciente
conociera los límites de esa modalidad.

**Acción:** documento de consentimiento versionado, con aceptación registrada
(versión, hash del texto, dispositivo y momento). Sin él no se emite una orden
nacida de una consulta a distancia.

### P0-14 · Una orden mal emitida no podía retirarse

No había forma de anular una orden. Una receta con la dosis equivocada, el
medicamento cambiado o el paciente confundido **seguía siendo dispensable hasta
su fecha de vencimiento**, que puede ser meses después.

**Acción:** anulación con motivo escrito, solo por quien firmó. La orden no se
borra ni se edita —es historia clínica— pero deja de poder dispensarse, tanto en
la autorización como en la verificación por hash del mostrador.

## P1 — Documentos legales y facturación

| # | Hallazgo | Acción |
| :--- | :--- | :--- |
| P1-20 | **Documentos legales de 931 palabras, escritos como folleto.** Faltaba prácticamente todo lo que exige el artículo 13 del Decreto 1377: identificación del responsable, finalidades, derechos, canal de atención, plazos, vigencia. | Cuatro documentos versionados, 3.356 palabras, con base normativa citada. |
| P1-21 | **El consentimiento no registraba qué texto se aceptó.** Sin la versión y el hash del documento, una constancia pierde valor probatorio en cuanto el texto cambia. | Versión y huella guardadas en cada aceptación. |
| P1-22 | **Transferencia internacional de datos no declarada.** La base está alojada fuera de Colombia. El artículo 26 de la Ley 1581 la restringe y exige un fundamento legal: autorización expresa, cláusulas contractuales o declaración de conformidad de la SIC. | Sección específica en la política, con el fundamento como dato que el prestador debe declarar. |
| P1-23 | **Sin facturación.** El cobro era una foto de transferencia aprobada a mano: sin numeración autorizada, sin identificación fiscal del emisor, sin constancia de por qué no se cobra IVA y sin forma de anularse. | Estructura completa, con numeración atómica e interfaz para el proveedor DIAN. |
| P1-24 | **El médico independiente no tenía dónde facturar.** Factura a su propio nombre, con su cédula y su propia resolución de numeración. Mezclar su consecutivo con el de la clínica invalidaría ambos: cada resolución autoriza un rango a un emisor concreto. | Perfil de facturación propio, con su identificación y su rango. |

## P2 — Interfaz

| # | Hallazgo | Acción |
| :--- | :--- | :--- |
| P2-18 | **El color no significaba nada.** 66 iconos con color propio repartidos en 14 familias, 458 usos del peso de fuente más grueso, 32 gradientes. Cuando todo tiene color, la alerta roja de alergia deja de destacar: compite con la decoración. | Sistema de tres colores semánticos —crítico, advertencia, confirmación— más neutro. Quedan 20 iconos con color, todos de estado clínico. |
| P2-19 | **`pharmacy_utils` calculaba la distancia dos veces por farmacia** al ordenar, y las farmacias sin coordenadas quedaban primero en la lista de «más cercanas». | Una evaluación por elemento; las que no tienen coordenadas van al final. |
| P2-20 | **`print()` para errores** en dos rutas. En un worker de Gunicorn esa salida no la lee nadie. | Registro estructurado. |

---

## Resumen

| Prioridad | 1.ª pasada | 2.ª | 3.ª | Total |
| :--- | ---: | ---: | ---: | ---: |
| P0 | 9 | 2 | 3 | **14** |
| P1 | 14 | 5 | 5 | **24** |
| P2 | 11 | 6 | 3 | **20** |
| **Total** | **34** | **13** | **11** | **58** |

**Pruebas automatizadas:** 274 (0 antes de la auditoría).

## Trabajo pendiente, con su motivo

Puntos que no dependen del código y que ningún cambio mío puede sustituir:

1. **Rotar las credenciales** expuestas en el `.env` versionado (`SECURITY.md`).
   Hasta entonces, la historia clínica cifrada es descifrable por cualquiera que
   haya tenido ese archivo.
2. **Cargar los catálogos CIE-10 y CUPS oficiales.** Los incluidos son un arranque
   mínimo; un catálogo incompleto rechaza códigos válidos al prescribir.
3. **Revisión de `clinical_safety.py` por un químico farmacéutico.** El contenido
   clínico es verificable, pero es una revisión inicial de atención primaria, no un
   catálogo exhaustivo. El módulo lo declara y `manage.py check-knowledge-base`
   informa de su antigüedad.

4. **Completar los datos del prestador en los documentos legales.** Razón social,
   NIT, domicilio, canal de PQRS, área responsable y el fundamento de la
   transferencia internacional de datos. Se configuran en Ajustes; mientras
   falten, los documentos muestran marcadores y no son publicables.
5. **Registrar las bases de datos ante el RNBD** de la Superintendencia de
   Industria y Comercio.
6. **Revisión de los textos legales por un abogado.** Tienen la estructura que
   exige la norma y citan su fundamento, pero son la base sobre la que esa
   revisión trabaja, no su sustituto.
7. **Contratar un proveedor tecnológico de facturación electrónica** y su
   resolución de numeración ante la DIAN. La estructura está lista
   (`billing.py`); por defecto los documentos se generan y numeran pero quedan
   marcados como pendientes de radicar, sin fingir un envío que no ocurrió.
8. **MIPRES** para medicamentos no financiados con UPC. La orden guarda el número
   para vincularse con el reporte oficial, pero la integración exige credenciales
   del prestador.

---

# Cuarta pasada — 2026-09-06

### P0-15 · La interfaz se rompía por completo sin acceso al CDN de Tailwind

Detectado en uso real: la aplicación aparecía como HTML sin ningún estilo.

La causa era `cdn.tailwindcss.com`, que compila el CSS **en el navegador**. Si
ese servidor no es alcanzable —red rural, cortafuegos, bloqueo regional— no hay
degradación elegante: no hay estilos en absoluto. Las demás librerías cargaban
bien desde otro CDN, así que no era falta de conexión sino ese origen concreto.

La primera pasada ya había identificado el riesgo (P2-14) y lo dejó documentado
como pendiente por requerir un paso de build. Ver la aplicación rota en pantalla
dejó claro que el nivel asignado era el equivocado: en una aplicación cuyo caso
de uso declarado es la baja conectividad, un recurso que solo carga con internet
es un recurso que un día no carga.

**Acción:** eliminada toda dependencia de CDN.

| Antes | Ahora |
| :--- | :--- |
| Tailwind compilado en el navegador desde CDN | CSS generado en el build, 56 KB, servido en local |
| Lucide desde unpkg | `static/vendor/lucide.min.js` |
| Leaflet desde unpkg | `static/vendor/leaflet.{js,css}` + sus imágenes |
| Chart.js desde jsDelivr | `static/vendor/chart.umd.min.js` |
| Inter desde Google Fonts | `static/vendor/fonts/` (8 archivos woff2) |

La política de seguridad de contenido pasó de autorizar cuatro CDN a no
autorizar ninguno para scripts ni estilos. Cada uno era un tercero capaz de
ejecutar JavaScript sobre páginas con historia clínica abierta.

El Service Worker precachea la hoja de estilos y las librerías, así que la
interfaz conserva su aspecto sin conexión. El CSS generado se versiona: ejecutar
la aplicación no requiere ni Node ni internet.

Quedan dos recursos externos, ambos por naturaleza: Jitsi para videollamada y
las teselas de OpenStreetMap. Una prueba impide que se cuele cualquier otro.
