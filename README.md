# 🏥 RuralHealth Connect

> [!IMPORTANT]
> **Antes de desplegar con datos de pacientes reales, lee
> [`SECURITY.md`](SECURITY.md) y [`AUDIT.md`](AUDIT.md).**
> Las credenciales que estaban en el `.env` del proyecto deben rotarse: hasta
> entonces, la historia clínica cifrada es descifrable por cualquiera que haya
> tenido acceso a ese archivo.
>
> Verifica el entorno con `python manage.py preflight` antes de cada despliegue.


**RuralHealth Connect** es una plataforma integral de salud digital diseñada para entornos con baja conectividad. Permite la gestión completa de atención médica rural a través de perfiles especializados, chats clínicos cifrados, telemedicina, órdenes médicas digitales, mapas de geolocalización y cumplimiento normativo colombiano (RIPS).

---

## 📋 Tabla de Contenido

*   [🚀 Visión General](#-visión-general)
*   [🌟 Funcionalidades Principales](#-funcionalidades-principales)
*   [🏗️ Arquitectura del Sistema](#️-arquitectura-del-sistema)
*   [⚡ Optimizaciones de Rendimiento](#-optimizaciones-de-rendimiento)
*   [🛠️ Tecnologías](#️-tecnologías)
*   [⚙️ Instalación y Ejecución](#️-instalación-y-ejecución)
*   [📱 Alojamiento en Red Local (PC + Celular)](#-alojamiento-en-red-local-pc--celular)
*   [👥 Roles del Sistema](#-roles-del-sistema)
*   [📂 Registro de Salud (RIPS)](#-registro-de-salud-rips)
*   [🔐 Seguridad y Privacidad](#-seguridad-y-privacidad)
*   [🗺️ Mapas y Ubicación](#️-mapas-y-ubicación)
*   [💰 Monetización y Convenios](#-monetización-y-convenios)
*   [💊 Logística de Farmacia](#-logística-de-farmacia)
*   [📊 Encuesta de Satisfacción](#-encuesta-de-satisfacción-y-validación-de-campo)

---

## 🚀 Visión General

La aplicación está optimizada para interconectar pacientes, médicos, personal de apoyo y administradores, mejorando el acceso a la salud en zonas remotas. Reduce los tiempos de espera presenciales, evita viajes innecesarios y digitaliza los procesos legales médicos para auditoría instantánea.

---

## 🌟 Funcionalidades Principales

1. **Agendamiento Digital Multicanal**: Los pacientes pueden agendar citas presenciales o por telemedicina basándose en la disponibilidad en tiempo real del médico.
2. **Telemedicina Inteligente**: Chats cifrados asíncronos y videollamadas. Separación entre chats de "Consultas Médicas" (diagnóstico) y chats de "Soporte" (preguntas administrativas, farmacia).
3. **Órdenes Médicas Legales (SERSALUD)**: Emisión de prescripciones digitales con validez legal colombiana. Incluye firma digital del médico, diagnósticos CIE-10, datos del aseguramiento y validación de inventario en tiempo real.
4. **Dashboard de Analítica Estratégica**: Panel administrativo avanzado con gráficos (Chart.js) sobre productividad médica, top de medicamentos recetados y distribución de diagnósticos.
5. **Exportación RIPS**: Generación del Registro Individual de Prestación de Servicios de Salud en formato `.zip` con un solo clic.
6. **Despacho Farmacéutico Inteligente**: Motor lógico para entregas parciales y alertas "Listos para completar" cuando llega nuevo inventario físico.
7. **PWA y Modo Offline**: La plataforma es instalable como App nativa. Su Service Worker incluye caché inteligente para funcionar en zonas rurales con conectividad intermitente, permitiendo enviar chats y ver agendas sin conexión.
8. **Verificación de Seguridad Clínica**: Antes de firmar una orden, el sistema la
   contrasta con las alergias del paciente (incluida la reactividad cruzada entre
   familias farmacológicas), las interacciones con su tratamiento activo, la
   duplicidad terapéutica, las contraindicaciones en embarazo y las restricciones
   pediátricas. Las alertas bloqueantes exigen justificación escrita, que queda en
   la orden y en la auditoría.
9. **Trazabilidad de Medicamentos**: Libro mayor inmutable y encadenado por hash de
   cada movimiento de inventario y cada entrega, con verificación de identidad de
   quien retira.
10. **Derechos de Habeas Data**: El paciente puede descargar una copia completa de
    sus datos, solicitar rectificación o supresión y revocar su autorización, con
    plazos legales controlados.
11. **Motor de Robustez Zero-Trust**: Limitación de tráfico, bloqueo de cuenta
    persistido, control estricto de firmas digitales y aislamiento por clínica.

---

## 🏗️ Arquitectura del Sistema

El proyecto sigue un patrón MVC (Model-View-Controller) tradicional implementado en Flask, enfocado fuertemente en **Scopes** de datos. Esto significa que cada consulta a la base de datos se filtra automáticamente por el `clinic_id` del usuario (usando `execution_options` en SQLAlchemy) para prevenir fugas de datos entre diferentes sedes u hospitales.

*   `app.py`: Punto de entrada, inicialización de dependencias, limitador de tasa (Rate Limiting).
*   `models.py`: Declaración de tablas y relaciones. Implementa clases mixin `ClinicScoped`.
*   `routes_*.py`: Controladores separados por contexto de usuario (`routes_patient`, `routes_doctor`, `routes_admin`, `routes_analytics`, etc.).
*   `security.py`: Motor de cifrado AES, hashing de contraseñas, y sistema de logs de auditoría estricto.
*   `dispatch_engine.py`: Motor de despacho y entregas parciales de farmacia.
*   `clinical_safety.py`: Verificación de alergias, interacciones, duplicidad,
    embarazo, pediatría y medicamentos de control especial.
*   `ledger.py`: Libro mayor de inventario y dispensación. **Único camino por el
    que deben cambiar las existencias**: aplica decremento condicional atómico y
    deja asiento inmutable de cada movimiento.
*   `config.py`: Configuración *fail-closed*. En producción no arranca sin secretos.
*   `rips_service.py`: Generación y validación del RIPS.
*   `routes_privacy.py`: Derechos del titular sobre sus datos (Ley 1581).
*   `manage.py`: Comandos de administración (`preflight`, `backfill`,
    `verify-audit-chain`, `rotate-encryption-key`, …).
*   `tests/`: 205 pruebas automatizadas.

---

## ⚡ Optimizaciones de Rendimiento

La plataforma ha sido optimizada para operar eficientemente en servidores locales de bajo costo o computadores de gama media/baja en puestos de salud rurales:

*   **Reducción de Consultas a Base de Datos (Matriz de Farmacia)**: Se eliminó el bucle de consultas anidadas $O(P \times M)$ en la generación de matrices de inventario de medicamentos. Ahora, el sistema recupera todos los stocks coincidentes para todas las farmacias y medicamentos en una única consulta por lote ($O(1)$ en base de datos) y construye un índice hash en memoria para búsquedas instantáneas en $O(1)$.
*   **Derivación de Clave Cacheada (corrección de fondo)**: la función que obtiene la
    clave de cifrado ejecutaba 600.000 iteraciones de PBKDF2 en **cada** lectura y
    escritura de un campo cifrado. Como la PII y la historia clínica pasan por ahí,
    listar cien pacientes disparaba cientos de derivaciones. Al cachear la instancia
    por proceso, la suite de pruebas completa bajó de 4 min 21 s a 58 s. Esta era la
    causa principal de la lentitud que reportaron los usuarios en la encuesta de campo.
*   **Ajuste Inteligente de Contexto de Seguridad**: Las cabeceras estrictas de seguridad (HSTS, secure cookies) se desactivan automáticamente en entornos de desarrollo/pruebas locales para permitir el uso del protocolo HTTP en redes locales inalámbricas, pero se mantienen estrictamente activadas en entornos de producción (HTTPS).

---

## 🛠️ Tecnologías

*   **Backend**: Python 3.10+ / Flask / Flask-SQLAlchemy / Flask-Login
*   **Frontend**: Jinja2 / Tailwind CSS (CDN) / Lucide Icons / Chart.js
*   **PWA**: Service Worker / Manifest.json (Soporte Offline y Caché)
*   **Mapas**: Leaflet.js / OpenStreetMap
*   **Seguridad Avanzada**:
    *   **Cifrado**: Cryptography (AES-256) / PyJWT
    *   **Sanitización**: Bleach (XSS Prevention)
    *   **Headers**: Flask-Talisman (CSP, HSTS, XFO)
    *   **Control de Tráfico**: Flask-Limiter (Rate Limiting)
    *   **Validación de Archivos**: Python-Magic (MIME detection)
*   **Base de Datos**: SQLite (desarrollo) / PostgreSQL compatible (producción)

---

## ⚙️ Instalación y Ejecución

### Requisitos

| Componente | Mínimo | Por qué |
| :--- | :--- | :--- |
| Python | 3.10+ | Sintaxis y `zoneinfo` usados en el código. |
| PostgreSQL | 13+ (producción) | SQLite no implementa el bloqueo de filas que impide la doble dispensación de medicamentos. |
| Redis | 6+ (recomendado) | Sin almacenamiento compartido, cada worker lleva su propio contador de tráfico. |

### Instalación

```bash
python -m venv .venv
# Windows:  .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate

pip install -r requirements.txt -r requirements-dev.txt
```

### Configuración

```bash
cp .env.example .env
```

Genera cada secreto y complétalos en `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> [!WARNING]
> **La aplicación no arranca si falta un secreto en producción.** Es deliberado:
> arrancar con una llave por defecto dejaría la historia clínica cifrada con una
> clave pública, que es peor que no cifrarla porque además aparenta protección.
>
> **`RURALHEALTH_FIELD_ENCRYPTION_KEY` protege toda la historia clínica.**
> Guárdala fuera de línea antes del primer arranque. Perderla significa perder
> los datos clínicos de forma irreversible.

### Esquema y datos de referencia

```bash
export FLASK_APP=app.py        # PowerShell: $env:FLASK_APP="app.py"
flask db upgrade               # aplica las migraciones

# Catálogos oficiales. Los que trae el sistema son un arranque mínimo:
# uno incompleto rechazaría códigos válidos al prescribir.
python manage.py load-cie10 cie10_oficial.csv
python manage.py load-cups  cups_oficial.csv
```

### Ejecución

```bash
# Desarrollo con base de datos local (no necesita servidor externo)
python app.py --local

# Desarrollo usando la base configurada en .env
python app.py

# Producción — nunca `python app.py`
gunicorn -c gunicorn.conf.py wsgi:app
```

### Pruebas

```bash
pytest                # 205 pruebas
pytest -q tests/test_clinical_safety.py   # solo seguridad clínica
```

> **`--local`** crea `instance/ruralhealth.db` en tu equipo y arranca sin
> depender de ningún servidor. Es la forma de trabajar cuando la base remota no
> está disponible, sin tener que editar `.env` cada vez.
>
> Si la conexión falla, la aplicación te dice **por qué** y **qué hacer** en vez
> de mostrar una traza de SQLAlchemy.

Guía completa de despliegue: [`DEPLOYMENT.md`](DEPLOYMENT.md).

> **Cuentas iniciales.** En el primer arranque se crean `superadmin` y `admin`.
> Si no defines sus contraseñas por variable de entorno, se generan al azar y se
> muestran **una sola vez** en consola: no se escriben en los archivos de registro.
> Ambas nacen marcadas para cambio obligatorio en el primer inicio de sesión.

---

## 📱 Alojamiento en Red Local (Uso simultáneo en PC y Celular)

RuralHealth Connect está configurado para ejecutarse en toda tu red local. Esto te permite abrir la aplicación desde tu computadora y tu celular al mismo tiempo usando una red Wi-Fi compartida.

### Paso 1: Conecta ambos dispositivos a la misma red Wi-Fi
Para que los dispositivos puedan comunicarse, asegúrate de que tu celular y tu PC estén conectados a la misma red de internet local (mismo router/Wi-Fi).

### Paso 2: Inicia el Servidor
Al ejecutar `python app.py`, la aplicación detectará automáticamente la dirección IP local de tu PC (por ejemplo, `192.168.1.45`) y la mostrará en la consola:

```text
+------------------------------------------------------------+
|            RURALHEALTH CONNECT - SERVIDOR LOCAL            |
+------------------------------------------------------------+
| Acceso desde esta PC:   http://localhost:5000              |
| Acceso desde tu Celular: http://192.168.1.45:5000          |
|                                                            |
|  (Ambos dispositivos deben estar en la misma red Wi-Fi)    |
+------------------------------------------------------------+
```

### Paso 3: Accede desde tu Celular
Abre el navegador web de tu celular e ingresa la URL de acceso desde el celular mostrada en la consola (por ejemplo, `http://192.168.1.45:5000`). ¡Y listo! Podrás iniciar sesión y usar todas las funciones.

> [!TIP]
> **¿Problemas de Conexión? (Configurar Cortafuegos/Firewall de Windows)**
> Si tu celular no carga la página, es muy probable que el Firewall de Windows esté bloqueando la conexión entrante en el puerto `5000`. Puedes solucionarlo rápidamente siguiendo estos pasos:
> 1. Abre el menú inicio de Windows, escribe **Firewall de Windows Defender** y selecciónalo.
> 2. Haz clic en **Configuración avanzada** (columna izquierda).
> 3. Selecciona **Reglas de entrada** y luego haz clic en **Nueva regla...** (columna derecha).
> 4. Elige **Puerto**, haz clic en Siguiente.
> 5. Selecciona **TCP** y en *Puertos locales específicos* escribe `5000`. Haz clic en Siguiente.
> 6. Selecciona **Permitir la conexión**, haz clic en Siguiente.
> 7. Deja marcadas todas las casillas (Dominio, Privado, Público) y haz clic en Siguiente.
> 8. Escribe un nombre para la regla (ej. `RuralHealth Connect LAN`) y haz clic en Finalizar.

---

## 👥 Roles del Sistema

| Rol | Descripción y Flujo |
| :--- | :--- |
| **Superadmin** | Administrador global. Crea nuevas clínicas en el sistema y gestiona las aseguradoras/pólizas. |
| **Admin** | Gerente de la sede. Emite códigos de acceso, exporta RIPS consolidados y audita el stock de la farmacia local. |
| **Doctor** | Profesional de la salud. Atiende pacientes por chat/video, redacta notas de evolución CIE-10 y emite recetas. Puede ser institucional (trabaja para una clínica) o autónomo (gestiona sus propios pagos). |
| **Patient** | Paciente final. Agenda citas, gestiona tickets de medicamentos, sube comprobantes de pago y utiliza telemedicina. |
| **Expendedor** | Regente de farmacia. Valida tickets mediante un hash, entrega medicamentos, realiza entregas parciales y solicita reabastecimiento. |
| **Staff/Receptionist** | Personal administrativo. Mantiene los chats de soporte, confirma agendas y recepciona cargamentos de inventario físico. |

---

## 📂 Registro de Salud (RIPS)

Cumple la normativa colombiana para el **Registro Individual de Prestación de
Servicios de Salud**, generando los archivos planos CT, AF, US y AC.

> [!IMPORTANT]
> **El generador no inventa ningún dato.** Si falta información obligatoria,
> **rechaza la exportación** y devuelve un informe de qué falta y en qué paciente.
>
> Es deliberadamente más incómodo que producir un archivo siempre: un RIPS es una
> declaración ante el sistema de salud, y radicar datos supuestos es reportar
> información falsa. Además, estos registros alimentan las estadísticas con las
> que se planifica la salud del territorio, de modo que un dato inventado en un
> municipio rural distorsiona decisiones reales sobre él.

*   **Validación previa** (`Admin → Validar RIPS`): revisa el periodo sin generar
    el archivo, para corregir antes de intentar radicar.
*   **Requisitos que se verifican**: código de habilitación del REPS del prestador,
    identificación desagregada del paciente (tipo y número de documento, apellidos
    y nombres por separado, fecha de nacimiento, sexo, códigos DANE), diagnóstico
    CIE-10 y procedimiento CUPS de cada atención, y registro profesional del médico.
*   **Fuera de alcance**: la radicación electrónica vigente (Resolución 2275 de
    2023) exige además factura electrónica validada por la DIAN y envío al
    MinSalud. Eso se tramita con el operador de facturación del prestador; el
    paquete generado incluye un `LEEME.txt` con lo que queda pendiente.

---

## 🔐 Seguridad y Privacidad

> Auditoría completa con los 34 hallazgos y su corrección: [`AUDIT.md`](AUDIT.md).
> Modelo de amenazas y procedimiento de rotación: [`SECURITY.md`](SECURITY.md).

### Identidad y acceso

*   **Bloqueo de cuenta persistido**: 5 intentos fallidos bloquean 15 minutos. El
    contador vive en base de datos, así que sobrevive a los reinicios y es común a
    todos los workers. **Se aplica por igual al formulario web y a la API JWT**;
    antes la API no lo consultaba y ofrecía un canal de fuerza bruta sin límite.
*   **Restablecimiento de contraseña**: código de un solo uso emitido por el
    administrador y entregado en persona, con caducidad de 30 minutos. Del código
    solo se guarda su hash.
*   **Cambio obligatorio de contraseña inicial**: las cuentas creadas por un
    administrador no pueden operar hasta que su titular define una propia.
*   **Política de contraseñas**: mínimo 12 caracteres, mayúsculas, minúsculas,
    número y símbolo; se rechazan las comunes, las que contienen el nombre o el
    usuario, y las que quedaron expuestas en el repositorio.
*   **Sesión**: caducidad por inactividad (30 min) y absoluta (8 h), regeneración
    completa al iniciar sesión, cookies `HttpOnly` / `Secure` / `SameSite=Lax`.

### Datos clínicos

*   **Cifrado en reposo**: Fernet (AES-128-CBC + HMAC-SHA256) por campo, con la
    llave fuera de la base de datos.
*   **Índice ciego**: búsqueda por documento mediante HMAC con pimienta; el
    documento en claro nunca se indexa.
*   **Aislamiento por clínica**: filtrado automático en la capa ORM más
    comprobación explícita de pertenencia en cada endpoint.
*   **Sin residuos en dispositivos compartidos**: el Service Worker no almacena
    ninguna respuesta autenticada, y al cerrar sesión se purga la caché y el
    almacenamiento local. Antes se cacheaban chats clínicos e historias, que
    quedaban accesibles sin sesión para el siguiente usuario del navegador.

### Integridad y trazabilidad

*   **Auditoría encadenada por hash**: alterar o eliminar una entrada rompe la
    verificación de todas las siguientes. Consultable en **Admin → Auditoría** y
    verificable con `manage.py verify-audit-chain`.
*   **Libro mayor de inventario y dispensación**: asiento inmutable de cada
    movimiento, con saldo anterior y posterior, responsable y motivo.
*   **Firma de órdenes médicas**: HMAC-SHA256 sobre el contenido íntegro,
    verificado en cada dispensación; y sello que vincula la orden con la identidad
    y el registro profesional de quien la firmó.
*   **Dispensación atómica**: decremento condicional en una sola sentencia SQL más
    bloqueo pesimista donde el motor lo soporta. Impide que dos expendedores
    simultáneos entreguen el mismo inventario.

### Cumplimiento normativo

*   **Ley 1581 de 2012 (habeas data)**: exportación completa de datos, solicitudes
    de rectificación y supresión con plazo controlado, y revocatoria de
    autorización. La historia clínica no se elimina a petición: prevalece el deber
    de conservarla 15 años (Resolución 839 de 2017), y así se le explica al titular.
*   **Consentimiento versionado**: se registra qué texto aceptó el paciente, con la
    versión y el hash del documento.
*   **Retención documental**: plazos por tipo de registro, con su base legal, en datos.

---

## 🗺️ Mapas y Ubicación

Utilizamos un componente de ubicación personalizado con **Leaflet.js**:
*   **Pin Arrastrable**: Permite definir ubicaciones exactas de clínicas y consultorios en zonas sin direcciones postales claras.
*   **Búsqueda por Radio**: Los pacientes pueden encontrar servicios basados en su geolocalización actual (modo "Cerca de mí").

---

## 💰 Monetización y Convenios

El sistema incluye un motor flexible de facturación:
*   **Suscripciones Autónomas**: Planes para médicos que ejercen por su cuenta.
*   **Pólizas de Aseguradoras**: Integración mediante códigos de invitación.
*   **Validación de Comprobantes**: Subida segura de imágenes o PDF de transferencias bancarias para aprobación manual por el médico.

---

## 💊 Logística de Farmacia y Despacho Inteligente

El módulo de farmacia resuelve el problema crítico del quiebre de stock en zonas rurales:

*   **Tickets Dinámicos (Balance-Aware)**: Cálculo en tiempo real de `Pendiente = Requerido - Entregado`.
*   **Filtro "Ready to Complete"**: Muestra qué pacientes en cola ya pueden recibir sus medicinas porque el stock físico fue reabastecido.
*   **Despacho en Un Clic**: Deduce del inventario instantáneamente.
*   **Reposición Automatizada**: El Staff puede recibir cargamentos completos, reactivando alertas de stock de toda la red.

---

## 📊 Encuesta de Satisfacción y Validación de Campo

Como parte del desarrollo, se aplicó una encuesta de validación de campo a los usuarios de la plataforma para medir la facilidad de uso, claridad y utilidad en entornos de baja conectividad.

*   **Enlace al Formulario**: [Encuesta de Satisfacción - Google Forms](https://docs.google.com/forms/d/e/1FAIpQLSfSbP22sCldvJNxgfxAq0okI1N0MP-mCmmaDg4wWji_NUbV_w/viewform)
*   **Métricas del Piloto**:
    *   **100% de Aceptación**: Todos los usuarios encuestados expresaron que volverían a usar el servicio y lo recomendarían.
    *   **Ahorro de Traslados**: El 100% confirmó que la plataforma les evitó desplazamientos largos innecesarios.
    *   **Atención Médica**: Calificada mayoritariamente como "Excelente".
    *   **Fluidez**: la retroalimentación sobre velocidad motivó la optimización de las
    consultas de farmacia. La auditoría posterior encontró que la causa principal
    era otra —la derivación de clave repetida en cada campo cifrado— y también
    quedó corregida.

> [!NOTE]
> Seis respuestas son una señal útil de aceptación, no una validación clínica.
> Antes de la implantación real conviene un piloto con más usuarios y con
> medición de desenlaces, no solo de satisfacción.

### Resultados Consolidados de la Encuesta

| Fecha/Hora | ¿Cargó Correctamente? | ¿Información Clara? | Calificación Atención | ¿Evitó Desplazamientos? | ¿Volvería a Usar? | ¿Recomendaría? | Aspectos a Mejorar / Comentarios |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 20/05/2026 23:31:00 | Siempre | Sí | Excelente | Sí | Sí | Sí | "Todo está perfecto" |
| 21/05/2026 15:15:38 | Siempre | Sí | Excelente | Sí | Sí | Sí | "Todo bien" |
| 21/05/2026 17:13:08 | Siempre | Sí | Buena | Sí | Sí | Sí | "No hay opciones de mejora. Me parece muy práctica la web y ahorra mucho tiempo." |
| 21/05/2026 17:14:36 | Casi siempre | Sí | Excelente | Sí | Sí | Sí | "Fluidez de la página. Falta velocidad pero cumple con sus funciones." |
| 21/05/2026 17:16:42 | Casi siempre | Sí | Excelente | Sí | Sí | Sí | "La velocidad de carga. Buenos servicios y ayuda mucho a la hora de consultas." |
| 21/05/2026 18:57:16 | Siempre | Sí | Excelente | Sí | Sí | Sí | "Todo me parece muy completo. Súper bueno, muy buena la atención." |

---

© 2026 RuralHealth Connect. Todos los derechos reservados.
