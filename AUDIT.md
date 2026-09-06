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

## Resumen

| Prioridad | Hallazgos |
| :--- | :--- |
| P0 | 9 |
| P1 | 14 |
| P2 | 10 |
| **Total** | **33** |
