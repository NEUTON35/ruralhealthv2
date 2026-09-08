# Seguridad, RuralHealth Connect

---

## ⚠️ ACCIÓN REQUERIDA ANTES DE OPERAR: rotación de credenciales

Durante la auditoría del 2026-09-05 se encontró que el archivo `.env` versionado en el
directorio del proyecto contenía credenciales de producción en texto plano:

| Secreto | Estado | Consecuencia de la exposición |
| :--- | :--- | :--- |
| `SQLALCHEMY_DATABASE_URI` | **Comprometido** | Acceso completo de lectura y escritura a la base de datos de pacientes. |
| `RURALHEALTH_FIELD_KEY_SEED` | **Comprometido** | Descifrado de toda la PII y la historia clínica en reposo. |
| `RURALHEALTH_SECRET_KEY` | **Comprometido** | Falsificación de cookies de sesión y de hashes de orden médica. |
| `RURALHEALTH_JWT_SECRET_KEY` | **Comprometido** | Emisión de tokens de API válidos para cualquier usuario. |
| `RURALHEALTH_SUPER_PASSWORD` | **Comprometido** | Acceso de superadministrador. |
| `RURALHEALTH_ADMIN_PASSWORD` | **Comprometido** | Acceso de administrador de clínica. |

Además, `app.py` incluía las contraseñas de `superadmin` y `admin` como literales en el código
fuente y las escribía en `logs/ruralhealth.log`.

**Estos valores deben considerarse públicos.** Cifrar los datos con una llave conocida equivale a
no cifrarlos: si esos datos son de pacientes reales, la situación constituye un incidente de
seguridad notificable ante la Superintendencia de Industria y Comercio conforme al artículo 17
literal n) de la Ley 1581 de 2012.

### Procedimiento de rotación

**1 · Base de datos**

```bash
# En la consola del proveedor (Render): rotar la contraseña del usuario de base de datos.
# Después, revisar accesos no reconocidos:
psql "$NUEVA_URI" -c "SELECT usename, client_addr, backend_start FROM pg_stat_activity;"
```

**2 · Llaves de aplicación**

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"   # SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(64))"   # JWT_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(64))"   # HASH_PEPPER
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FIELD_ENCRYPTION_KEY
```

**3 · Recifrado de los datos existentes**

Cambiar la llave de campo sin recifrar deja los datos ilegibles. El proceso es guiado:

```bash
python manage.py rotate-encryption-key --old-seed "<semilla-anterior>" --new-key "<clave-nueva>"
```

El comando exige confirmación explícita, opera en una única transacción y verifica cada registro
tras el recifrado. Hacer copia de seguridad antes.

**4 · Reindexado del índice ciego**

Cambiar `HASH_PEPPER` invalida la búsqueda por cédula. Tras rotarlo:

```bash
python manage.py rebuild-blind-index
```

**5 · Contraseñas**

```bash
python manage.py force-password-reset --all-privileged
```

**6 · Purga de secretos de los registros**

```bash
python manage.py scrub-logs
```

**7 · Sesiones y tokens activos**

Rotar `SECRET_KEY` invalida todas las cookies de sesión. Para los JWT emitidos:

```bash
python manage.py revoke-all-tokens
```

---

## Modelo de amenazas

| Activo | Amenaza | Control |
| :--- | :--- | :--- |
| Historia clínica | Acceso no autorizado entre clínicas | Ámbito por `clinic_id` aplicado en la capa ORM (`with_loader_criteria`), reforzado por comprobación de pertenencia en cada endpoint. |
| Historia clínica | Robo del volcado de base de datos | Cifrado Fernet (AES-128-CBC + HMAC-SHA256) por campo, con llave fuera de la base de datos. |
| Cédula del paciente | Correlación entre bases | Índice ciego HMAC con pimienta; el valor en claro nunca se indexa. |
| Sesión | Secuestro | Cookies `HttpOnly`, `Secure`, `SameSite=Lax`; regeneración en login; caducidad por inactividad y absoluta. |
| Credenciales | Fuerza bruta | Bloqueo persistido en base de datos tras 5 fallos, aplicado por igual al formulario web y a la API JWT. |
| Orden médica | Falsificación | HMAC-SHA256 sobre el contenido íntegro de la orden, verificado en cada dispensación. |
| Inventario | Doble dispensación | Bloqueo pesimista de fila y decremento condicional dentro de una transacción. |
| Registro de auditoría | Manipulación posterior | Encadenamiento hash: alterar una entrada rompe la cadena de todas las siguientes. |
| Dispositivo compartido | Datos residuales | El Service Worker no almacena respuestas autenticadas; purga de caché al cerrar sesión. |

---

## Reporte de vulnerabilidades

Escribir a la dirección de contacto de la organización con el asunto `[SEGURIDAD]`.
No abrir incidencias públicas para fallos explotables.

Compromiso de respuesta: acuse en 48 horas, evaluación inicial en 5 días hábiles.

---

## Verificación previa al despliegue

```bash
python manage.py preflight
```

Comprueba, y falla si alguno no se cumple:

- Todos los secretos definidos y distintos de los valores de ejemplo.
- `FLASK_ENV=production` y PostgreSQL como motor de base de datos.
- Migraciones al día.
- Ninguna cuenta con contraseña inicial pendiente de cambio.
- Almacenamiento compartido configurado para la limitación de tráfico.
- HTTPS forzado y HSTS activo.
- Permisos del directorio de subidas restringidos.
