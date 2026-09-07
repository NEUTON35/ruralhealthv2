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
5. **Determinar si hay obligación de registrar las bases ante el RNBD.** El
   Decreto 090 de 2018 solo obliga a sociedades y entidades sin ánimo de lucro
   con activos superiores a 100.000 UVT, y a personas jurídicas públicas. Hay
   que declarar la situación real en el artículo 13 de la política: afirmar un
   registro inexistente es una declaración falsa ante la SIC.
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
9. **Credenciales del IHCE** en Hércules (SISPRO), y prueba contra el ambiente
   sandbox antes de producción. El código está listo; sin credenciales los RDA
   se acumulan en la cola sin perderse, pero no se remiten.

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


# Quinta pasada — 2026-09-07

Revisión de los documentos legales contra la norma citada, verificando cada
referencia en la fuente y no de memoria.

## P0 — Bloqueante

### P0-16 · El sistema incumple la Resolución 1888 de 2025 (plazo vencido)

Este es el hallazgo más grave de las cinco pasadas y no se corrige con texto.

La Ley 2015 de 2020 creó la historia clínica electrónica interoperable. La
Resolución 1888 de 2025 adoptó el **Resumen Digital de Atención en Salud (RDA)**
y obliga a todo prestador a generar, por cada atención, un documento en estándar
**HL7 FHIR R4** y transmitirlo a la plataforma nacional del Ministerio de Salud,
de modo que cualquier profesional que atienda al paciente en el país pueda
consultar sus datos clínicos relevantes.

- Entrada en vigencia: **15 de octubre de 2025**
- Plazo de integración: **seis meses**, es decir hasta el **15 de abril de 2026**
- Fecha de esta revisión: **7 de septiembre de 2026**

El plazo venció hace casi cinco meses. El sistema no tiene ninguna
implementación: cero referencias a FHIR, RDA o IHCE en todo el código.

No es un problema de redacción. Es desarrollo: mapear el modelo clínico a los
recursos FHIR del Resumen Digital, implementar el cliente contra la plataforma
nacional, gestionar credenciales y reintentos, y dejar constancia por atención
de qué se remitió y cuándo.

**Estado: implementado** en la sexta pasada. Ver más abajo.

## P1 — Errores en los documentos legales

### P1-9 · Se citaba la Ley 1266 de 2008 como fundamento

La política de datos invocaba la Ley 1266 de 2008. Esa ley regula el habeas data
**financiero, crediticio, comercial y de servicios**; no cubre datos de salud,
que se rigen por la Ley 1581 de 2012. Citarla es atribuirle al documento un
fundamento que no tiene. Retirada.

### P1-10 · Se afirmaba un registro ante el RNBD que puede no existir

El artículo 13 declaraba, sin condición alguna, que las bases de datos se
inscriben en el Registro Nacional de Bases de Datos.

El Decreto 090 de 2018 redujo el universo de obligados a las sociedades y
entidades sin ánimo de lucro con activos superiores a **100.000 UVT** y a las
personas jurídicas de naturaleza pública. Un operador pequeño no está obligado,
y afirmar un registro inexistente es una declaración falsa ante la SIC.

El artículo ahora explica el criterio del Decreto 090 y exige declarar la
situación real, advirtiendo además que no estar obligado no exime del resto de
la Ley 1581.

### P1-11 · Faltaban los términos especiales de conservación

El artículo 7 prometía suprimir la información a los quince años. La Resolución
839 de 2017 establece dos excepciones que el documento omitía por completo:

1. Historias de **víctimas de violaciones de derechos humanos o de infracciones
   graves al DIH**: los términos **se duplican**.
2. Historias que formen parte de un proceso por **delitos de lesa humanidad**:
   conservación **permanente**.

En una plataforma de salud rural colombiana esto no es un caso de borde: es
población que va a estar en la base de datos. La política prometía por escrito
una destrucción que en esos casos sería ilegal.

Se añadieron ambos supuestos al documento y dos filas a `RetentionPolicy`, para
que cualquier rutina de depuración que se escriba después las encuentre en
datos. Hoy no existe ninguna rutina de borrado automático, así que el riesgo era
de promesa, no de destrucción efectiva.

Se añadió además el desglose que exige la norma: cinco años en archivo de
gestión y diez en archivo central.

### P1-12 · Faltaba el Aviso de Privacidad

Los artículos 14 y 15 del Decreto 1377 de 2013 exigen un **aviso de privacidad**
como documento autónomo, con contenido mínimo propio, para el momento en que se
recolectan los datos. No existía. Se redactó (`privacy_notice`, versión 1.0), se
publicó en `/aviso-de-privacidad` y se enlazó desde el formulario de registro,
que es donde ocurre la recolección.

### P1-13 · Una sola casilla autorizaba datos personales y sensibles

El formulario de registro pedía en una única casilla la autorización para
"Datos Personales y Sensibles". El artículo 6 de la Ley 1581 exige que la
autorización para datos sensibles se obtenga **de forma separada** y advirtiendo
al titular que **no está obligado a otorgarla**.

El flujo separado ya existía (`patient.consent`), pero la casilla del registro lo
contradecía y viciaba ambos consentimientos. Ahora la casilla cubre solo datos
personales y anuncia que la de salud se pedirá aparte y puede negarse.

### P1-14 · Faltaba el fundamento de la firma electrónica

Los documentos afirmaban que las órdenes llevan firma del profesional y sello
criptográfico, sin decir qué es eso jurídicamente. De ello depende que la orden
sea oponible.

Nueva cláusula 9 de los términos: firma electrónica del artículo 7 de la Ley 527
de 1999 y del Decreto 2364 de 2012, describiendo el método (autenticación con
credenciales personales, registro de fecha, hora y origen, huella criptográfica
del contenido y auditoría encadenada) y advirtiendo que no sustituye la firma
digital certificada donde la norma la exija expresamente.

### P1-15 · Derecho de retracto sin plazo

Se mencionaba el artículo 47 de la Ley 1480 de 2011 sin indicar el término. Son
**cinco días hábiles**. Añadido, junto con la excepción de los servicios que ya
comenzaron a ejecutarse con anuencia del consumidor.

## P2 — Precisión de las citas

- **Ley 1712 de 2014** se citaba como fundamento del documento de transparencia.
  Esa ley obliga a los sujetos obligados: entidades públicas y particulares que
  ejercen función pública o administran recursos públicos. Un operador privado
  normalmente no lo es. Retirada y sustituida por la **Resolución 13437 de 1991**,
  que es la fuente real del decálogo de derechos del paciente que el documento
  enumera, y por la **Ley 23 de 1981**.
- Añadidas **Resolución 3100 de 2019** (habilitación, en la que se apoya la
  cláusula 1) y **Ley 1438 de 2011 artículo 136** (reserva de la historia clínica).
- Corregida una referencia cruzada interna: el artículo 8 remitía al "artículo
  siguiente" para el canal de atención, pero el siguiente es el de medidas de
  seguridad. El canal está en el artículo 10.
- El fundamento de los cinco años de auditoría pasó de "deber de demostrar el
  cumplimiento" a **responsabilidad demostrada, Decreto 1377 de 2013**.
- Añadido el contrato de **transmisión de datos** del artículo 25 del Decreto
  1377, que rige la relación entre cada IPS responsable y el operador encargado,
  y se aclaró que el profesional independiente obra como responsable.

## Versiones

| Documento | Antes | Ahora |
| :--- | :--- | :--- |
| Términos y condiciones | 3.0 | 4.0 |
| Política de tratamiento de datos | 3.0 | 4.0 |
| Aviso de privacidad | no existía | 1.0 |
| Consentimiento de telemedicina | 2.0 | 2.0 (sin cambios) |
| Transparencia y derechos | 3.0 | 4.0 |

5.129 palabras en cinco documentos. Cero rayas, cero negritas de énfasis, trato
de usted en todo.

## Lo que sigue requiriendo un abogado

Esta pasada verifica citas y estructura. No sustituye el concepto de un abogado
con tarjeta profesional, que sigue siendo necesario para:

1. **La cláusula 14 de responsabilidad.** Un límite de responsabilidad mal
   redactado se cae entero, y el que hay no ha sido revisado por nadie.
2. **El fundamento de la transferencia internacional** (`FUNDAMENTO_TRANSFERENCIA`).
   Cuál de las tres excepciones del artículo 26 se invoca es una decisión
   jurídica, no técnica.
3. **El contrato de transmisión de datos** con cada IPS. El documento lo anuncia;
   alguien tiene que redactarlo y firmarlo.
4. **El régimen del profesional independiente** como responsable autónomo: qué
   pasa cuando deja la plataforma y quién custodia entonces esas historias.
5. **Menores de edad.** La cláusula 6 y el artículo 11 recogen el estándar de la
   Sentencia C-748 de 2011, pero el procedimiento de acreditación de la patria
   potestad no está definido ni en el texto ni en el sistema.
6. **Consentimiento informado asistencial** distinto del de telemedicina, para
   procedimientos concretos, conforme a la Ley 23 de 1981.


# Sexta pasada — 2026-09-07

Implementación de la interoperabilidad IHCE, que la quinta pasada había
identificado como P0-16 y dejado sin resolver.

## P0-16 · Resumen Digital de Atención (Resolución 1888 de 2025)

### De dónde salió la especificación

No se inventó nada. El mapeo se construyó contra las fuentes oficiales:

- **Guía de implementación FHIR** publicada en `https://vulcano.ihcecol.gov.co/`.
  El `StructureDefinition-CompositionAmbulatoryRDA` se descargó y se parseó para
  extraer las diez secciones con sus códigos LOINC y sus títulos literales, que
  el perfil fija como valores constantes.
- **Manual de operaciones de interoperabilidad IHCE v1.4** del Ministerio, de
  donde salen los endpoints, el flujo de autenticación, las convenciones de
  referencias y las reglas de validación 1 a 8.
- **ValueSet ColombianPersonIdentifierCodes**, para los diecisiete tipos de
  documento admitidos.

### Arquitectura: por qué una cola y no una llamada directa

Lo natural sería llamar al Ministerio al cerrar la consulta. Sería un error.

Eso ataría la atención clínica a que la red responda, y la red es justamente lo
que falla en un puesto de salud rural. Un profesional no puede quedarse sin poder
cerrar una historia clínica porque un servidor de Bogotá no contesta.

Al cerrar la atención solo se encola (`RDASubmission`). Un proceso aparte
transmite y reintenta con espera creciente. La atención nunca depende de la
disponibilidad del Ministerio, y el deber de remitir queda en una tabla
auditable: en cualquier momento puede responderse cuántas atenciones están
pendientes de remisión y por qué.

### Validación local antes de transmitir

El propio manual la pide, pero aquí pesa una razón adicional: la conectividad
rural es cara y escasa. Gastar una llamada en un documento que va a devolver 400
no solo pierde esa llamada, sino que mete el envío en la cola de reintentos y
retrasa a los que sí estaban bien.

Se implementan las reglas del manual que pueden comprobarse sin consultar
registros nacionales. Las que dependen de EVOL, REPS y RETHUS solo puede
resolverlas el servidor.

### Clasificación de errores

Determina si reintentar sirve de algo:

| Respuesta | Tratamiento | Motivo |
| :--- | :--- | :--- |
| 200 | aceptado | se guarda el acuse de recibo |
| 409 | duplicado, no se reintenta | el Ministerio ya lo tenía: el deber está cumplido |
| 400 | rechazado, no se reintenta | reintentar da 400 otra vez |
| 401/403 | un reintento con token nuevo | puede ser un token vencido antes de tiempo |
| 5xx y red | reintento con espera creciente | el otro lado no está disponible |

Los reintentos tienen techo (ocho). Un envío con cien fallos no se arregla con el
ciento uno, y la cola dejaría de ser legible.

### Archivos

| Archivo | Contenido |
| :--- | :--- |
| `ihce/terminology.py` | Códigos, perfiles, secciones y endpoints oficiales |
| `ihce/config.py` | Credenciales por entorno, sin exponerlas en logs |
| `ihce/mapping.py` | Construcción del Bundle FHIR |
| `ihce/validation.py` | Reglas del manual, comprobadas en local |
| `ihce/client.py` | OAuth2 contra Azure AD y transmisión |
| `ihce/outbox.py` | Cola, reintentos y clasificación de estados |
| `models.py` | `RDASubmission`, con índice único por atención |
| `manage.py` | `rda-status`, `rda-send`, `rda-problems`, `rda-retry`, `rda-backfill`, `rda-preview` |

`preflight` ahora falla en producción si faltan credenciales del IHCE, porque sin
ellas el prestador está incumpliendo.

### Pruebas

60 pruebas nuevas, sin red. Cubren las reglas 1 a 6 del manual, el contenido
clínico del documento, la clasificación de errores, la espera creciente, el techo
de reintentos y que guardar una historia clínica encole su RDA.

Una prueba comprueba que la base de datos impide remitir dos veces la misma
atención, y no solo la comprobación previa en código.

### Lo que estas pruebas no demuestran

**Que el Ministerio acepte los documentos.** Eso exige credenciales reales contra
el ambiente de pruebas, que solo se obtienen tras registrar al prestador en
Hércules. Lo verificado es que el documento cumple las reglas que el propio
manual dice que el servidor aplica.

Quedan tres validaciones que solo puede hacer el servidor, y que dependen de
datos que no están en esta aplicación:

1. el paciente debe existir en **EVOL** y coincidir en tipo y número de documento,
   primer apellido, primer nombre y sexo;
2. el profesional debe estar activo en **RETHUS**;
3. la institución y la sede deben estar habilitadas en **REPS**.

**Antes de atender al primer paciente en producción hay que transmitir al sandbox
y confirmar que responde 200.** Está documentado en DEPLOYMENT.md, sección 13.7.

### Datos que el perfil exige y la aplicación aún no captura

El perfil `PatientRDA` marca como obligatorias varias extensiones que hoy no
existen en el modelo: nacionalidad, pertenencia étnica, condición de discapacidad,
ocupación y zona de residencia. La aplicación sí tiene `zone`, `department_code`
y `municipality_code`.

Mientras no se capturen, el Ministerio puede devolver advertencias o rechazos por
esos campos. Es trabajo de interfaz, no de mapeo, y depende de cómo el prestador
quiera recolectar datos que son sensibles por sí mismos (la pertenencia étnica lo
es). Se deja señalado en lugar de inventar valores por defecto: un dato étnico
inventado en un registro nacional de salud es peor que un dato ausente.
