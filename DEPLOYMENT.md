# Despliegue — RuralHealth Connect

Guía para poner el sistema en operación real. Cada paso está aquí porque su
ausencia causa un fallo concreto, no por completitud.

---

## Antes de nada

Si vienes de la versión anterior, **lee primero [`SECURITY.md`](SECURITY.md)**.
Las credenciales que estaban en el `.env` versionado deben rotarse antes de
cualquier despliegue; cifrar con una llave conocida equivale a no cifrar.

---

## 1 · Requisitos

| Componente | Mínimo | Por qué |
| :--- | :--- | :--- |
| Python | 3.10+ | Sintaxis y `zoneinfo` usados en el código. |
| PostgreSQL | 13+ | SQLite no implementa el bloqueo de filas que impide la doble dispensación de medicamentos. |
| Redis | 6+ (recomendado) | Sin almacenamiento compartido, cada worker lleva su propio contador de tráfico y el límite efectivo se multiplica. |
| TLS | Obligatorio | Se transmiten datos de salud. HSTS queda activo en producción. |

---

## 2 · Configuración

```bash
cp .env.example .env
```

Genera cada secreto:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"   # SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(64))"   # JWT_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(64))"   # HASH_PEPPER
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> **`RURALHEALTH_FIELD_ENCRYPTION_KEY` protege toda la historia clínica.**
> Guárdala en un gestor de secretos con copia fuera de línea **antes** del primer
> arranque. Perderla significa perder los datos clínicos de forma irreversible:
> no hay recuperación posible.

> **`RURALHEALTH_HASH_PEPPER` no puede cambiarse a la ligera.** Es la pimienta del
> índice ciego que permite buscar pacientes por documento. Cambiarla invalida
> todas las búsquedas existentes hasta ejecutar `manage.py rebuild-blind-index`.

---

## 3 · Verificación previa

```bash
python manage.py preflight
```

Comprueba secretos, base de datos, cuentas con contraseña inicial pendiente,
médicos sin registro profesional, integridad de la auditoría, TLS, limitación de
tráfico y antigüedad de la base de conocimiento clínico.

**No despliegues si falla.** Cada comprobación corresponde a un fallo observado.

---

## 4 · Esquema

```bash
export FLASK_APP=app.py
flask db upgrade
```

Con varias réplicas, ejecútalo **una sola vez** como paso previo, no en el
arranque de cada contenedor: todas intentarían migrar a la vez.

Si vienes de una base creada por la versión anterior:

```bash
python manage.py backfill        # rellena datos heredados; hazlo con la app detenida
```

---

## 5 · Catálogos oficiales

Los códigos CIE-10 y CUPS que trae el sistema son un arranque mínimo, no el
catálogo oficial. La validación solo exige pertenencia al catálogo cuando la
tabla tiene contenido, así que **un catálogo incompleto rechazaría códigos
válidos**. Carga el oficial antes de operar:

```bash
python manage.py load-cie10 cie10_oficial.csv    # formato: codigo,descripcion
python manage.py load-cups  cups_oficial.csv
```

---

## 6 · Datos del prestador

Sin el código de habilitación del REPS **el RIPS no se puede exportar**. El NIT
no lo sustituye.

```bash
python manage.py create-clinic "Puesto de Salud X" \
    --nit 900123456 \
    --habilitacion 05001234501 \
    --department 05 \
    --municipality 001
```

O desde la interfaz: **Ajustes → Identificación del prestador**.

---

## 7 · Arranque

### Con Docker

```bash
docker build -t ruralhealth:latest .
docker run -d \
    --name ruralhealth \
    --env-file .env \
    -p 5000:5000 \
    -v ruralhealth-uploads:/app/uploads \
    -v ruralhealth-logs:/app/logs \
    --restart unless-stopped \
    ruralhealth:latest
```

### Directo

```bash
gunicorn -c gunicorn.conf.py wsgi:app
```

**Nunca `python app.py` en producción.** El servidor de desarrollo de Flask es de
un solo hilo y no está preparado para tráfico real.

---

## 8 · Proxy inverso

Ejemplo con Nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name salud.ejemplo.gov.co;

    ssl_certificate     /etc/letsencrypt/live/salud.ejemplo.gov.co/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/salud.ejemplo.gov.co/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;

    # Debe coincidir con MAX_CONTENT_LENGTH de la aplicación.
    client_max_body_size 5M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}

server {
    listen 80;
    server_name salud.ejemplo.gov.co;
    return 301 https://$host$request_uri;
}
```

> **`X-Forwarded-For` importa.** La aplicación activa `ProxyFix` solo en
> producción, porque confiar en esa cabecera sin un proxy delante permitiría
> falsear la IP de origen y con ello evadir el bloqueo por intentos fallidos.
> Si despliegas **sin** proxy inverso, no uses `FLASK_ENV=production` con
> `ProxyFix` activo sin revisar esta implicación.

Declara los dominios servidos:

```bash
RURALHEALTH_ALLOWED_HOSTS=salud.ejemplo.gov.co
```

Sin esta lista, una cabecera `Host` manipulada puede alterar los enlaces
absolutos, incluido el de restablecimiento de contraseña.

---

## 9 · Copias de seguridad

Lo que hay que respaldar, y por qué:

| Elemento | Frecuencia | Nota |
| :--- | :--- | :--- |
| Base de datos | Diaria, con retención de 30 días | Contiene la historia clínica. Conservación legal mínima: 15 años. |
| `uploads/` | Diaria | Documentos clínicos, firmas y comprobantes. |
| Llaves de cifrado | Una vez, fuera de línea | **Sin ellas la copia de la base es ilegible.** Guárdalas por separado. |

```bash
pg_dump "$SQLALCHEMY_DATABASE_URI" | gzip > backup_$(date +%F).sql.gz
```

Prueba la restauración periódicamente. Una copia que nunca se ha restaurado no
es una copia: es una suposición.

---

## 10 · Supervisión

| Sonda | Uso |
| :--- | :--- |
| `GET /health` | Vivacidad. No toca la base de datos. Para el reinicio del contenedor. |
| `GET /ready` | Disponibilidad. Comprueba base de datos y base clínica. Para el balanceador. |

Eventos que merecen alerta, consultables en **Admin → Auditoría**:

- `login_blocked_by_lockout`, `jwt_blocked_by_lockout` — ataque de fuerza bruta.
- `medical_order_hash_rejected` — orden médica manipulada.
- `delivery_identity_mismatch` — el documento presentado no coincide con el titular.
- `prescription_safety_override` — se prescribió pese a una alerta bloqueante.
- Cadena de auditoría rota — manipulación del registro.

---

## 11 · Mantenimiento periódico

```bash
# Semanal
python manage.py verify-audit-chain
python manage.py verify-stock-ledger

# Mensual
python manage.py purge-login-attempts

# Semestral — requiere revisión por químico farmacéutico
python manage.py check-knowledge-base
```

> La base de conocimiento clínico (`clinical_safety.py`) debe revisarse cada seis
> meses contra el listado vigente del INVIMA. Una base desactualizada sigue
> emitiendo alertas, pero deja de cubrir lo que se haya incorporado después.

---

## 12 · Antes de atender al primer paciente

- [ ] `manage.py preflight` sin errores
- [ ] Secretos rotados y guardados fuera de línea (`SECURITY.md`)
- [ ] Copia de seguridad probada **restaurándola**
- [ ] Catálogos CIE-10 y CUPS oficiales cargados
- [ ] Código de habilitación REPS registrado
- [ ] Todos los médicos con registro profesional
- [ ] Todas las cuentas iniciales con contraseña cambiada
- [ ] HTTPS con certificado válido y HSTS activo
- [ ] `RURALHEALTH_ALLOWED_HOSTS` configurado
- [ ] Redis configurado para la limitación de tráfico
- [ ] Base de conocimiento clínico revisada por químico farmacéutico
- [ ] Personal formado en el flujo de alergias: **un paciente sin alergias
      registradas no es un paciente sin alergias, es uno del que no sabemos**
- [ ] Procedimiento definido para entregar códigos de restablecimiento en persona
- [ ] Responsable designado para atender solicitudes de habeas data en plazo

---


## 13 · Interoperabilidad IHCE (obligatoria)

La **Resolución 1888 de 2025** obliga a todo prestador inscrito en REPS a remitir
un **Resumen Digital de Atención (RDA)** a la plataforma nacional por cada
atención que preste, en estándar HL7 FHIR R4. Entró en vigencia el 15 de octubre
de 2025 con un plazo de integración de seis meses.

No es opcional y no depende del tamaño del prestador.

### 13.1 · Obtener las credenciales

Son cuatro pasos ante el Ministerio, y ninguno es instantáneo. Empiece por aquí,
no el día antes de salir a producción.

1. Registre el prestador en **Mi Seguridad Social**.
2. Designe formalmente un **delegado** del prestador.
3. Inscriba al delegado, con su documento de identidad, en el **módulo IHCE de
   Hércules** (SISPRO).
4. Hércules genera el **ClientID** y el **ClientSecret**, y le entrega la **clave
   de suscripción** y la **URL base** del ambiente.

Ponga esos valores en las variables `IHCE_*` del archivo `.env`.

### 13.2 · Verificar la conexión

```bash
python manage.py rda-status
```

Dice si faltan credenciales, contra qué ambiente apunta y cuántos RDA hay en cada
estado.

### 13.3 · Revisar un documento antes de transmitir

Antes de tener credenciales ya puede comprobar que el mapeo es correcto:

```bash
python manage.py rda-preview <id_de_la_atencion>
python manage.py rda-preview <id_de_la_atencion> --json
```

Valida localmente contra las reglas del Manual de operaciones v1.4 y muestra qué
secciones llevan datos y cuáles viajan vacías. El documento contiene información
clínica: no lo pegue en un ticket ni en un chat.

### 13.4 · Transmitir

La transmisión **no ocurre durante la atención**. Al cerrar una historia clínica
el RDA solo se encola; un proceso aparte lo envía y reintenta.

Esto es deliberado. Si se llamara al Ministerio dentro de la consulta, una caída
de su servidor impediría cerrar la historia clínica, y en una zona rural con mala
conectividad eso pasa. La atención nunca puede depender de que Bogotá conteste.

Programe el envío cada pocos minutos:

```bash
*/5 * * * * cd /ruta/al/proyecto && python manage.py rda-send >> logs/rda.log 2>&1
```

### 13.5 · Atender los que no salen

```bash
python manage.py rda-problems
```

Hay dos causas distintas:

- **bloqueado**: faltan datos locales. Lo dice explícitamente: el paciente no
  tiene fecha de nacimiento, el profesional no tiene registro médico, la
  institución no tiene código de habilitación, la atención no tiene diagnóstico
  CIE-10. Se corrige en la interfaz, no en el código.
- **rechazado**: el Ministerio devolvió un error de estructura, o se agotaron los
  reintentos. El mensaje trae el `OperationOutcome` con el campo señalado.

Una vez corregida la causa:

```bash
python manage.py rda-retry --id <id>
```

### 13.6 · Puesta en marcha con historias anteriores

Si ya hay atenciones registradas antes de configurar la integración:

```bash
python manage.py rda-backfill --since 2026-01-01
```

Encola las que no tengan registro de envío. Revise antes con `--limit` pequeño.

### 13.7 · Lo que no se puede verificar sin credenciales

El mapeo se construyó contra los perfiles publicados en
`https://vulcano.ihcecol.gov.co/` y se valida en local contra las reglas del
manual. Pero **que el Ministerio acepte los documentos solo se comprueba contra
el ambiente de pruebas**. Antes de atender al primer paciente en producción,
transmita al sandbox y confirme que responde 200.

Hay además tres validaciones que solo puede hacer el servidor y que dependen de
datos que no están en esta aplicación:

- el paciente debe existir en **EVOL** y coincidir en tipo y número de documento,
  primer apellido, primer nombre y sexo;
- el profesional debe estar activo en **RETHUS**;
- la institución y la sede deben estar habilitadas en **REPS**.

Si alguno falla, el RDA se rechaza aunque el documento esté bien armado.

## Problemas frecuentes

**La aplicación no arranca y muestra «ARRANQUE DETENIDO».**
Es el comportamiento buscado: falta un secreto o alguno usa un valor comprometido.
El mensaje enumera todos los problemas a la vez. Consulta `.env.example`.

**«SQLite no es apto para producción».**
Correcto. SQLite no implementa `SELECT … FOR UPDATE`, y sin él dos expendedores
simultáneos pueden entregar el mismo inventario. Usa PostgreSQL.

**El RIPS no se genera y muestra una lista de faltantes.**
También es el comportamiento buscado. La versión anterior rellenaba los huecos con
datos inventados. Completa lo que la pantalla indica.

**Un médico no puede prescribir.**
Le falta el registro médico profesional o la firma digital. Ambos son requisitos
legales del documento. Se registran en Ajustes.

**Los datos aparecen como texto cifrado ilegible.**
La llave de cifrado no coincide con la que cifró los datos. Restaura la llave
original; si la rotaste, ejecuta `manage.py rotate-encryption-key`. **No borres
los datos**: con la llave correcta vuelven a ser legibles.
