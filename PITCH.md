# 🏥 RuralHealth Connect - Pitch Comercial & Prompt Maestro

Este documento contiene un pitch comercial estructurado para presentar el proyecto ante inversionistas o clínicas, seguido de un **Prompt Maestro** diseñado para que cualquier nuevo agente de Inteligencia Artificial (IA) comprenda instantáneamente la arquitectura, lógica de negocio y seguridad de RuralHealth Connect.

---

## 🚀 Pitch Comercial: Estructura de Presentación (Pitch Deck)

### Diapositiva 1: La Portada (El Gancho)
*   **Título**: RuralHealth Connect
*   **Subtítulo**: Salud digital sin fronteras para comunidades rurales.
*   **Mensaje Clave**: Conectamos de forma asíncrona, segura y robusta a médicos, pacientes y farmacias en zonas con baja conectividad, eliminando los viajes perdidos por falta de stock médico.
*   **Guion del Orador**: *"¿Qué harías si para reclamar una pastilla para la presión tuvieras que caminar 4 horas bajo el sol y, al llegar, te dijeran que no hay inventario? Esto le pasa todos los días a millones de personas en Latinoamérica. RuralHealth Connect es la primera plataforma de salud digital diseñada específicamente para operar en entornos con internet intermitente o nulo, garantizando que nadie viaje en vano por su salud."*

### Diapositiva 2: El Problema (La Brecha de Salud Rural)
*   **Puntos de Dolor**:
    1.  **Aislamiento y Conectividad**: Las plataformas de telemedicina actuales exigen banda ancha constante. En el campo, el internet móvil es inestable o inexistente.
    2.  **Quiebre de Stock en Farmacias**: Los centros de salud rurales no saben qué medicamentos tienen disponibles en tiempo real, forzando a los pacientes a viajes inútiles.
    3.  **Cumplimiento Legal (RIPS)**: Las normativas como el RIPS de Colombia exigen estructurar y exportar datos de consultas de forma estricta, lo que satura administrativamente al escaso personal de salud.
    4.  **Vulnerabilidad de Datos**: La información de pacientes se expone en planillas físicas o chats no seguros.

### Diapositiva 3: La Solución (RuralHealth Connect)
*   **Propuesta de Valor**:
    *   **PWA Offline-First**: Aplicación instalable en celulares y PC que almacena datos de citas y chats en caché local y los sincroniza automáticamente cuando detecta señal.
    *   **Despacho Farmacéutico Balance-Aware**: Un motor logístico inteligente de asignación que gestiona entregas parciales y reserva automáticamente el inventario físico entrante para los pacientes que quedaron pendientes.
    *   **Cumplimiento RIPS Automático**: Genera los archivos de transacciones y consultas de ley en un archivo comprimido (.zip) con un solo clic.

### Diapositiva 4: Seguridad y Privacidad "Zero-Trust"
*   **Pilares de Ciberseguridad**:
    *   **Clinic Scoping**: Consultas a bases de datos filtradas de manera autónoma a nivel de ORM (SQLAlchemy) mediante el identificador de clínica del usuario, impidiendo accesos cruzados accidentales.
    *   **Cifrado AES-256**: Historias clínicas y chats cifrados en reposo.
    *   **Blind Index**: Búsqueda segura de identificaciones (cédulas) mediante un hash seguro enriquecido con sal privada (HMAC con Pepper), protegiendo la identidad del paciente.
    *   **Validación CIE-10 / CUPS**: Verificación estricta de códigos médicos mediante expresiones regulares para garantizar validez clínica y legal.

### Diapositiva 5: Modelo de Negocio e Impacto
*   **Monetización**:
    *   **Suscripción B2B**: Planes mensuales o anuales para clínicas rurales institucionales.
    *   **Suscripción para Médicos Autónomos**: Modelo flexible para profesionales independientes que prestan consulta externa en áreas rurales.
    *   **Integración con Aseguradoras**: Códigos de invitación y validación manual de tiquetes de pago bancario mediante carga de comprobantes.
*   **Impacto Social**: Reducción de costos logísticos para pacientes y aumento de la eficiencia del personal de salud de hasta un 45%.

---

## 🤖 Prompt Maestro para Transferencia de Contexto a otra IA

*Copia y pega este prompt al iniciar una nueva conversación con una IA para que entienda todo el proyecto y pueda codificar, documentar o diseñar de inmediato.*

```text
Actúa como un Ingeniero de Software Principal y Arquitecto de Soluciones de Salud Digital. A continuación, te proporciono el contexto técnico y funcional completo del proyecto "RuralHealth Connect". Memorízalo para responder preguntas, optimizar el código, crear nuevas funcionalidades o redactar documentación técnica.

### 1. PROPÓSITO DEL PROYECTO
RuralHealth Connect es una plataforma web e instalable (PWA) construida en Python (Flask) enfocada en entornos rurales o con baja conectividad. Permite gestionar agendas médicas, telemedicina asíncrona, recetas legales digitales y logística inteligente de farmacia.

### 2. ARQUITECTURA GENERAL
- Backend: Python 3.10+ utilizando Flask, Flask-SQLAlchemy, Flask-Login, Flask-Migrate y python-dotenv.
- Base de Datos: SQLite (para desarrollo y pruebas locales) y PostgreSQL compatible (para producción).
- Seguridad de Red: Flask-Talisman para headers HTTP (CSP, HSTS, secure cookies) y Flask-Limiter para control de peticiones.
- Frontend: Jinja2, Tailwind CSS (CDN), Lucide Icons, Leaflet.js para mapas interactivos y Chart.js para analítica.

### 3. ESTRUCTURA DEL CÓDIGO FUENTE
El proyecto sigue un patrón MVC. Sus archivos principales son:
- app.py: Punto de entrada. Configura la app, inicializa extensiones, registra blueprints y arranca el servidor. Configurado para autodetectar la IP local para hosting en red local (LAN) y relajar cookies seguras si FLASK_ENV != 'production'.
- models.py: Contiene el esquema de base de datos. Implementa la clase mixin 'ClinicScoped' para filtrar automáticamente consultas por 'clinic_id' usando events de SQLAlchemy.
- security.py: Centraliza el cifrado AES-256 en base de datos (EncryptedText), hashing de contraseñas (PBKDF2), blind indexes para cédulas (HMAC con Pepper), auditorías y limitación de intentos de login fallidos.
- dispatch_engine.py: Motor de despacho de medicamentos. Administra tiquetes de recogida parciales ('autorizado', 'sin_stock', 'parcial', 'entregado') y reserva inventarios automáticamente.
- pharmacy_utils.py: Utilidades de inventario. Contiene 'build_stock_matrix' optimizado con consulta única por lote para evitar el antipatrón de base de datos N*M.
- rips_service.py: Generador de archivos de texto plano requeridos por el Ministerio de Salud de Colombia (RIPS: archivos AC, US, AF, CT) empaquetados en un ZIP.
- Blueprints: routes_auth.py, routes_patient.py, routes_doctor.py, routes_admin.py, routes_staff.py, routes_expendedor.py, routes_settings.py, routes_superadmin.py, routes_analytics.py.

### 4. REGLAS CRÍTICAS DE PROGRAMACIÓN Y SEGURIDAD
1. CLINIC SCOPING: Siempre que consultes datos clínicos (pacientes, doctores, chats, órdenes, tiquetes, inventario), la consulta debe pasar por el filtro de 'clinic_id'. La clase 'ClinicScoped' en models.py lo hace automáticamente a menos que se use la opción '.execution_options(include_all_clinics=True)'.
2. CIFRADO PII: Datos personales sensibles (nombres, teléfonos, correos, direcciones, cédulas) se guardan usando el tipo 'EncryptedText' en la base de datos (cifrado AES transparente). Las búsquedas por cédula se deben hacer consultando el campo 'cedula_hash' usando la función 'pii_hash()'.
3. ENTREGAS PARCIALES: El estado de una orden médica pasa a 'completada' únicamente cuando todos los tiquetes de medicamentos asociados a ella tienen estado 'entregado'.
4. COMPATIBILIDAD LAN: Las variables de Talisman y de sesión 'session_cookie_secure', 'force_https' y 'strict_transport_security' deben estar vinculadas a 'is_production = os.environ.get("FLASK_ENV") == "production"' para permitir pruebas HTTP en red local a través de IPs dinámicas.

Entendido el contexto, confírmamelo con un breve resumen técnico de los componentes y espere mis instrucciones.
```
