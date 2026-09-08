# RuralHealth Connect

Plataforma de salud digital para zonas rurales de Colombia, diseñada para operar
con conectividad intermitente y para cumplir la normativa colombiana de
prestación de servicios de salud.

Este documento sirve para presentar el proyecto ante una clínica, una entidad
territorial o un inversionista. Está escrito para que cada afirmación pueda
comprobarse en el código o en la norma que se cita. Lo que todavía no está
hecho aparece en la sección de estado, no omitido.

---

## El problema

En una zona rural colombiana, una persona con hipertensión puede caminar cuatro
horas hasta el puesto de salud para reclamar su medicamento y encontrarse con
que no hay existencias. Nadie pudo avisarle porque nadie lo sabía: el inventario
vive en una planilla que se actualiza cuando alguien tiene tiempo.

Alrededor de eso hay tres problemas más:

1. **Las plataformas de telemedicina asumen banda ancha.** En el campo el
   internet móvil es intermitente. Una aplicación que exige conexión continua no
   sirve donde más falta hace.
2. **La carga normativa recae sobre personal escaso.** RIPS, historia clínica
   interoperable, notificación al Sivigila, farmacovigilancia y facturación
   electrónica son obligaciones simultáneas para un puesto de salud que puede
   tener dos personas.
3. **Los datos clínicos viven donde no deben.** Planillas en papel, fotos de
   fórmulas médicas y conversaciones por aplicaciones de mensajería general.

---

## Qué hace la plataforma

**Funciona con conectividad intermitente.** Aplicación instalable que conserva
agenda y mensajes en el dispositivo y sincroniza al recuperar señal. No depende
de ningún recurso externo: tipografías, hojas de estilo, iconos, mapas y
gráficas se sirven desde el propio servidor. La política de seguridad de
contenido no autoriza ningún origen de terceros para código ni estilos.

**Despacho farmacéutico con reserva.** El motor de entrega gestiona entregas
parciales y reserva el inventario entrante para quien quedó pendiente. Cada
movimiento de existencias tiene un asiento en un libro mayor encadenado, de modo
que el saldo puede reconstruirse en cualquier fecha y una discrepancia es
detectable.

**Órdenes médicas con contenido legal.** Conforme a la Resolución 1403 de 2007:
identificación completa del prestador y del profesional con su registro médico,
dosis, vía de administración, duración del tratamiento, y vigencia. Antes de
firmar, el sistema contrasta la prescripción con las alergias registradas, el
tratamiento activo, el estado de gestación y las interacciones conocidas.

**Verificación antes de dispensar.** Cada orden lleva un sello criptográfico que
liga profesional, paciente, institución, la lista exacta de medicamentos y la
fecha de vencimiento. Alterar una cantidad después de firmada invalida el sello.

---

## Cumplimiento normativo

La plataforma se construyó contra la norma, no contra una idea general de lo que
la norma pide. Cada módulo cita la disposición que lo obliga.

| Obligación | Norma | Estado |
| :--- | :--- | :--- |
| Historia clínica y su conservación | Resolución 1995 de 1999, Resolución 839 de 2017 | Implementado |
| Contenido de la prescripción | Resolución 1403 de 2007 | Implementado |
| Consentimiento de telemedicina | Resolución 2654 de 2019 | Implementado |
| Habeas data y derechos del titular | Ley 1581 de 2012, Decreto 1377 de 2013 | Implementado |
| Historia clínica interoperable (RDA) | Ley 2015 de 2020, Resolución 1888 de 2025 | Implementado, falta credencial |
| RIPS como soporte de la factura | Resolución 948 de 2026 | Implementado, falta facturador |
| Vigilancia en salud pública | Decreto 3518 de 2006 | Detección implementada |
| Farmacovigilancia | Resolución 1403 de 2007 | Registro implementado |
| PQRS del servicio de salud | Circular Única de la Supersalud | Implementado |
| Facturación electrónica | Resolución DIAN | Estructura lista, falta proveedor |

«Falta credencial» y «falta proveedor» significan exactamente eso: el desarrollo
está hecho y probado, y lo que falta es un trámite que solo puede adelantar el
prestador. Se detalla en la sección de estado.

### Dos decisiones que conviene explicar

**El sistema no radica ante la autoridad; deja constancia de que hay que
hacerlo.** El Sivigila y el INVIMA reciben por sus propios canales, con
credenciales del prestador. Fingir una radicación dejaría al prestador creyendo
que cumplió, que es peor que no tener nada. Lo que la plataforma sí hace es
cerrar el hueco anterior: que un caso notificable pasara inadvertido.

**El sistema se niega a exportar datos incompletos.** El generador de RIPS no
rellena lo que falta. Si una atención no tiene causa externa, o un paciente no
tiene régimen de afiliación, la exportación se detiene y dice qué falta y en qué
registro. Es más incómodo que producir siempre un archivo, y es la única forma de
no radicar información falsa ante el sistema de salud.

---

## Protección de datos

**Cifrado en reposo con AES-256-GCM.** Historia clínica, mensajes, nombres,
documentos y direcciones. Se eligió 256 y no 128 porque la historia clínica se
conserva quince años: lo que se cifra hoy debe seguir siendo secreto en 2041, y
frente a un adversario con computación cuántica el algoritmo de Grover reduce a
la mitad el nivel efectivo de una clave simétrica.

**Búsqueda sin almacenar el documento.** La cédula no se guarda en forma
consultable: se busca por un índice ciego derivado con HMAC y pimienta privada.
Dos instalaciones distintas no pueden cruzarse por ese campo.

**Aislamiento entre instituciones.** Las consultas se filtran por institución en
la capa del ORM, no en cada consulta escrita a mano. Saltarse el filtro exige
declararlo de forma explícita, lo que lo vuelve visible en revisión de código.

**Auditoría encadenada.** Cada entrada incorpora el hash de la anterior, de modo
que alterar o borrar una rompe la verificación de todas las siguientes. No impide
la manipulación (nada lo hace desde dentro de la misma base de datos) pero la
vuelve detectable, que es el requisito real de un registro clínico.

**Transporte.** TLS 1.3 obligatorio hacia las API del Ministerio, como exige el
artículo 6.4 del manual de interoperabilidad.

---

## Modelo de negocio

**Suscripción institucional.** Planes para clínicas y puestos de salud, por
número de profesionales.

**Profesionales independientes.** Un médico rural que ejerce por su cuenta
factura a su propio nombre, con su identificación tributaria y su propia
resolución de numeración ante la DIAN. La plataforma lo contempla como emisor
autónomo, no como un caso derivado de la clínica.

**Aseguradoras y pólizas.** Códigos de afiliación que habilitan descuentos sobre
las tarifas del profesional.

Los servicios de salud humana están excluidos del IVA por el numeral 1 del
artículo 476 del Estatuto Tributario, y la facturación lo refleja.

---

## Estado del proyecto

Honestidad sobre lo que hay: **513 pruebas automatizadas**, ocho auditorías
internas documentadas en `AUDIT.md` con cada hallazgo y su corrección.

### Listo para operar

Atención por chat y videollamada, agenda, historia clínica, órdenes médicas con
verificación de seguridad clínica, despacho farmacéutico con libro mayor,
habeas data, PQRS, vigilancia epidemiológica, farmacovigilancia, y generación de
RIPS y del Resumen Digital de Atención.

### Requiere un trámite del prestador

1. **Credenciales del IHCE**, que se obtienen en Hércules (SISPRO) tras
   registrar al prestador y a su delegado. El código está listo y probado; sin
   credenciales los documentos se acumulan en cola sin perderse.
2. **Proveedor tecnológico de facturación electrónica** y resolución de
   numeración ante la DIAN. Sin factura validada no hay RIPS que radicar.
3. **Catálogos oficiales completos**: CIE-10, CUPS, tablas de referencia del
   RIPS y catálogo de eventos del INS. La aplicación trae semillas parciales,
   marcadas como tales, y comandos de carga.

### Requiere revisión profesional

1. **Un abogado** debe revisar los textos legales. Tienen la estructura que
   exige la norma y citan su fundamento, pero son la base sobre la que esa
   revisión trabaja, no su sustituto.
2. **Un químico farmacéutico** debe revisar la base de conocimiento de seguridad
   clínica. El contenido es verificable, pero es una revisión inicial de
   atención primaria, no un catálogo exhaustivo. El módulo lo declara y hay un
   comando que informa de su antigüedad.

### Antes del primer paciente

`python manage.py preflight` verifica el entorno y **falla** si algo impide
desplegar: secretos comprometidos, base de datos inadecuada, profesionales sin
registro médico, cadena de auditoría rota o credenciales del IHCE ausentes.

---

## Arquitectura, en breve

Python con Flask y SQLAlchemy, PostgreSQL en producción. El esquema lo gobierna
Alembic. Interfaz en Jinja2 con Tailwind compilado localmente. Todo el frontal
se sirve desde el propio servidor.

Los detalles de despliegue están en `DEPLOYMENT.md`, el modelo de amenazas y el
procedimiento de rotación de llaves en `SECURITY.md`, y el historial completo de
auditorías con cada hallazgo en `AUDIT.md`.
